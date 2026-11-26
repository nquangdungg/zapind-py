from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Iterable, TypeVar

from .errors import NetworkError, RateLimitError, ZapindError, normalize_error
from .models import EventHandler, ThreadTarget, ThreadType, UnsubscribeFn

K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")

_SENSITIVE_KEY_RE = re.compile(
    r"token|secret|password|cookie|refresh|credential|auth|imei|cipher|zpw_sek|qr|imagebase64|imagedataurl|content|message|msg|text",
    re.IGNORECASE,
)


def redact_sensitive(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"[bytes({len(value)})]"
    if isinstance(value, list):
        return [redact_sensitive(item, depth + 1) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _SENSITIVE_KEY_RE.search(str(k)):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_sensitive(v, depth + 1)
        return out
    return str(value)


class RedactingLogger:
    """Logger wrapper that redacts sensitive metadata keys before emitting."""

    def __init__(self, logger: logging.Logger | None = None, *, debug: bool = False) -> None:
        self._logger = logger or logging.getLogger("zapind")
        self._debug = debug

    def debug(self, msg: str, meta: dict[str, Any] | None = None) -> None:
        if not self._debug:
            return
        self._logger.debug("%s %s", msg, redact_sensitive(meta) if meta else "")

    def info(self, msg: str, meta: dict[str, Any] | None = None) -> None:
        self._logger.info("%s %s", msg, redact_sensitive(meta) if meta else "")

    def warn(self, msg: str, meta: dict[str, Any] | None = None) -> None:
        self._logger.warning("%s %s", msg, redact_sensitive(meta) if meta else "")

    def error(self, msg: str, meta: dict[str, Any] | None = None) -> None:
        self._logger.error("%s %s", msg, redact_sensitive(meta) if meta else "")


class TTLCache(Generic[K, V]):
    """Bounded LRU-style TTL cache."""

    def __init__(self, max_entries: int = 500, default_ttl: float = 60.0) -> None:
        self._max_entries = max(1, max_entries)
        self._default_ttl = max(0.001, default_ttl)
        self._store: OrderedDict[K, tuple[V, float]] = OrderedDict()

    def get(self, key: K) -> V | None:
        item = self._store.get(key)
        if item is None:
            return None
        val, expires_at = item
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return val

    def set(self, key: K, value: V, ttl: float | None = None) -> None:
        if key in self._store:
            self._store.pop(key)
        elif len(self._store) >= self._max_entries:
            self._store.popitem(last=False)
        self._store[key] = (value, time.monotonic() + (ttl if ttl is not None else self._default_ttl))

    def delete(self, key: K) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class InFlightDeduplicator:
    """Shares one coroutine execution across concurrent callers with the same key."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Task[Any]] = {}

    async def run(self, key: str, coro_factory: Callable[[], Awaitable[T]]) -> T:
        existing = self._pending.get(key)
        if existing is not None:
            return await asyncio.shield(existing)

        task: asyncio.Task[T] = asyncio.create_task(coro_factory())  # type: ignore[arg-type]
        self._pending[key] = task

        def _cleanup(_done: asyncio.Task[Any]) -> None:
            if self._pending.get(key) is task:
                self._pending.pop(key, None)

        task.add_done_callback(_cleanup)
        return await asyncio.shield(task)

    def clear(self) -> None:
        self._pending.clear()


_MAX_KNOWN_USERS = 10_000


class ThreadTypeRegistry:
    """Resolves ThreadType automatically from contextual objects or cached Group IDs."""

    def __init__(self, groups_ttl: float = 300.0) -> None:
        self._group_ids: set[str] = set()
        self._user_ids: OrderedDict[str, None] = OrderedDict()
        self._groups_loaded_at: float = 0.0
        self._groups_ttl = groups_ttl
        self._lock = asyncio.Lock()

    def register_group(self, group_id: str) -> None:
        if group_id:
            self._group_ids.add(group_id)
            self._user_ids.pop(group_id, None)

    def register_groups(self, ids: Iterable[str]) -> None:
        for gid in ids:
            if gid:
                self._group_ids.add(gid)
                self._user_ids.pop(gid, None)
        self._groups_loaded_at = time.monotonic()

    def register_user(self, user_id: str) -> None:
        if not user_id or user_id in self._group_ids:
            return
        if user_id not in self._user_ids and len(self._user_ids) >= _MAX_KNOWN_USERS:
            self._user_ids.popitem(last=False)
        self._user_ids[user_id] = None

    @staticmethod
    def extract_id(target: ThreadTarget) -> str:
        if isinstance(target, str):
            return target.strip()
        if hasattr(target, "thread_id"):
            return str(getattr(target, "thread_id")).strip()
        return str(getattr(target, "id")).strip()

    async def resolve(
        self,
        target: ThreadTarget,
        explicit_type: ThreadType | None = None,
        fetch_all_group_ids: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> tuple[str, ThreadType]:
        thread_id = self.extract_id(target)

        if explicit_type is not None:
            if explicit_type == ThreadType.GROUP:
                self.register_group(thread_id)
            else:
                self.register_user(thread_id)
            return thread_id, explicit_type

        if not isinstance(target, str) and hasattr(target, "thread_type"):
            return thread_id, getattr(target, "thread_type")

        if thread_id in self._group_ids:
            return thread_id, ThreadType.GROUP
        if thread_id in self._user_ids:
            return thread_id, ThreadType.USER

        is_stale = (time.monotonic() - self._groups_loaded_at) > self._groups_ttl
        if fetch_all_group_ids is not None and is_stale:
            async with self._lock:
                if (time.monotonic() - self._groups_loaded_at) > self._groups_ttl:
                    try:
                        ids = await fetch_all_group_ids()
                        self.register_groups(ids)
                    except Exception:
                        pass
            if thread_id in self._group_ids:
                return thread_id, ThreadType.GROUP

        return thread_id, ThreadType.USER

    def clear(self) -> None:
        self._group_ids.clear()
        self._user_ids.clear()
        self._groups_loaded_at = 0.0


@dataclass(frozen=True)
class RetryOptions:
    max_attempts: int = 3
    base_delay: float = 0.3
    max_delay: float = 5.0


class RateLimiter:
    """Async token-bucket rate limiter."""

    def __init__(self, max_requests_per_second: float = 5.0, burst: int | None = None) -> None:
        self._rate = max(0.1, max_requests_per_second)
        self._capacity = float(max(1, burst if burst is not None else int(self._rate) + 1))
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_refill)
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

    async def acquire(self) -> None:
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait_s = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait_s)
            self._refill()
            self._tokens = max(0.0, self._tokens - 1.0)


async def with_timeout(
    coro_factory: Callable[[], Awaitable[T]], timeout: float, label: str = "Request"
) -> T:
    if timeout <= 0:
        return await coro_factory()
    try:
        return await asyncio.wait_for(coro_factory(), timeout=timeout)
    except asyncio.TimeoutError as err:
        raise NetworkError(f"{label} timed out after {timeout:.2f}s", cause=err) from err


async def with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    options: RetryOptions,
    allow_retry: bool = True,
) -> T:
    attempts = max(1, options.max_attempts) if allow_retry else 1
    last_err: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except BaseException as err:
            last_err = err
            retryable = isinstance(err, ZapindError) and err.retryable
            if not allow_retry or not retryable or attempt >= attempts:
                raise
            if isinstance(err, RateLimitError) and err.retry_after_ms and err.retry_after_ms > 0:
                delay = min(err.retry_after_ms / 1000.0, options.max_delay)
            else:
                exp = options.base_delay * (2 ** (attempt - 1))
                delay = min(exp + random.uniform(0, options.base_delay * 0.5), options.max_delay)
            await asyncio.sleep(delay)
    assert last_err is not None
    raise last_err


class PolicyExecutor:
    def __init__(
        self,
        *,
        default_timeout: float,
        retry_options: RetryOptions,
        rate_limiter: RateLimiter | None,
        logger: RedactingLogger,
    ) -> None:
        self._default_timeout = default_timeout
        self._retry = retry_options
        self._limiter = rate_limiter
        self._logger = logger

    async def execute(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        *,
        operation: str,
        timeout: float | None = None,
        idempotent: bool = True,
    ) -> T:
        effective_timeout = timeout if timeout is not None else self._default_timeout

        async def _attempt() -> T:
            if self._limiter is not None:
                await self._limiter.acquire()
            try:
                return await with_timeout(coro_factory, effective_timeout, operation)
            except Exception as err:
                normalized = normalize_error(err, f"Failed during {operation}")
                self._logger.debug(
                    "Operation error",
                    {"operation": operation, "code": normalized.code, "retryable": normalized.retryable},
                )
                raise normalized from err

        if not idempotent or self._retry.max_attempts <= 1:
            return await _attempt()
        return await with_retry(_attempt, self._retry, allow_retry=True)


class EventBus:
    """Async multi-listener event bus with per-handler error isolation."""

    def __init__(self, logger: RedactingLogger) -> None:
        self._listeners: dict[str, list[EventHandler]] = {}
        self._logger = logger
        self._tasks: set[asyncio.Task[Any]] = set()

    def on(self, event: str, handler: EventHandler) -> UnsubscribeFn:
        handlers = self._listeners.setdefault(event, [])
        if handler not in handlers:
            handlers.append(handler)
        return lambda: self.off(event, handler)

    def once(self, event: str, handler: EventHandler) -> UnsubscribeFn:
        def _wrapped(payload: Any) -> Any:
            self.off(event, _wrapped)
            return handler(payload)

        return self.on(event, _wrapped)

    def off(self, event: str, handler: EventHandler) -> None:
        handlers = self._listeners.get(event)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            pass
        if not handlers:
            self._listeners.pop(event, None)

    def emit(self, event: str, payload: Any) -> None:
        handlers = self._listeners.get(event)
        if not handlers:
            return
        iterable = handlers if len(handlers) == 1 else list(handlers)
        for handler in iterable:
            try:
                res = handler(payload)
                if inspect.isawaitable(res):
                    task = asyncio.create_task(self._run_async(event, res))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            except Exception as err:
                self._logger.error("Event handler threw an error", {"event": event, "error": str(err)})

    async def _run_async(self, event: str, awaitable: Awaitable[Any]) -> None:
        try:
            await awaitable
        except Exception as err:
            self._logger.error("Async event handler failed", {"event": event, "error": str(err)})

    def listener_count(self, event: str | None = None) -> int:
        if event is not None:
            return len(self._listeners.get(event, ()))
        return sum(len(h) for h in self._listeners.values())

    def remove_all_listeners(self, event: str | None = None) -> None:
        if event is not None:
            self._listeners.pop(event, None)
        else:
            self._listeners.clear()
