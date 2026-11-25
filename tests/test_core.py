from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from zapind.core import (
    EventBus,
    InFlightDeduplicator,
    RateLimiter,
    RedactingLogger,
    RetryOptions,
    ThreadTypeRegistry,
    TTLCache,
    redact_sensitive,
    with_retry,
    with_timeout,
)
from zapind.errors import (
    APIError,
    AuthenticationError,
    NetworkError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    SessionError,
    ValidationError,
    normalize_error,
)
from zapind.mappers import map_group_kwargs, map_message_kwargs, map_user_kwargs
from zapind.models import (
    Gender,
    GroupType,
    SessionCredentials,
    StoredSessionRecord,
    ThreadType,
)
from zapind.storage import (
    EncryptedFileSessionStore,
    FileSessionStore,
    MemorySessionStore,
)


def test_error_normalization() -> None:
    assert isinstance(normalize_error({"status": 401, "message": "Session expired"}), SessionError)
    assert isinstance(normalize_error({"status": 403, "message": "Forbidden"}), PermissionError)
    assert isinstance(normalize_error({"status": 404, "message": "Not found"}), NotFoundError)
    assert isinstance(normalize_error({"status": 429, "message": "Rate limit"}), RateLimitError)
    assert isinstance(normalize_error(ConnectionError("reset")), NetworkError)
    assert isinstance(normalize_error({"code": "AUTHENTICATION_ERROR", "message": "No auth"}), AuthenticationError)
    assert isinstance(normalize_error({"code": "VALIDATION_ERROR", "message": "Bad input"}), ValidationError)
    err = normalize_error({"status": 503, "code": 500, "message": "Unavailable"})
    assert isinstance(err, APIError)
    assert err.retryable is True


def test_redact_sensitive() -> None:
    out = redact_sensitive(
        {
            "cookie": [{"value": "secret"}],
            "imei": "imei-123",
            "text": "private chat",
            "account_id": "u-1",
        }
    )
    assert out["cookie"] == "[REDACTED]"
    assert out["imei"] == "[REDACTED]"
    assert out["text"] == "[REDACTED]"
    assert out["account_id"] == "u-1"


async def test_ttl_cache_and_deduplicator() -> None:
    cache: TTLCache[str, int] = TTLCache(max_entries=2, default_ttl=0.05)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3
    await asyncio.sleep(0.06)
    assert cache.get("b") is None

    dedup = InFlightDeduplicator()
    calls = 0

    async def slow() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "shared"

    res = await asyncio.gather(
        dedup.run("k", slow),
        dedup.run("k", slow),
        dedup.run("k", slow),
    )
    assert res == ["shared", "shared", "shared"]
    assert calls == 1


async def test_thread_registry_and_policies() -> None:
    registry = ThreadTypeRegistry(groups_ttl=60.0)
    fetches = 0

    async def fetch_groups() -> list[str]:
        nonlocal fetches
        fetches += 1
        return ["g-1", "g-2"]

    assert await registry.resolve("g-1", None, fetch_groups) == ("g-1", ThreadType.GROUP)
    assert await registry.resolve("g-2", None, fetch_groups) == ("g-2", ThreadType.GROUP)
    assert fetches == 1
    assert await registry.resolve("u-9", None, fetch_groups) == ("u-9", ThreadType.USER)

    with pytest.raises(NetworkError):
        await with_timeout(lambda: asyncio.sleep(0.1), 0.015, "slow")

    tries = 0

    async def flaky() -> str:
        nonlocal tries
        tries += 1
        if tries < 2:
            raise NetworkError("transient")
        return "ok"

    assert await with_retry(flaky, RetryOptions(max_attempts=3, base_delay=0.005, max_delay=0.02)) == "ok"
    assert tries == 2

    limiter = RateLimiter(max_requests_per_second=100.0, burst=2)
    await asyncio.gather(limiter.acquire(), limiter.acquire(), limiter.acquire())


async def test_event_bus_and_mappers() -> None:
    bus = EventBus(RedactingLogger())
    seen: list[str] = []

    def bad(_: object) -> None:
        raise RuntimeError("boom")

    async def good(v: str) -> None:
        seen.append(v)

    bus.on("ping", bad)
    unsub = bus.on("ping", good)
    bus.emit("ping", "hello")
    await asyncio.sleep(0.02)
    assert seen == ["hello"]
    unsub()
    bus.emit("ping", "ignored")
    await asyncio.sleep(0.01)
    assert seen == ["hello"]

    u = map_user_kwargs({"userId": "u1", "displayName": "Alice", "gender": 1, "isFr": 1})
    assert u["gender"] == Gender.FEMALE
    assert u["is_friend"] is True

    g = map_group_kwargs({"groupId": "g1", "name": "Devs", "type": 2, "e2ee": 1})
    assert g["type"] == GroupType.COMMUNITY
    assert g["is_e2ee"] is True

    m = map_message_kwargs(
        {
            "type": ThreadType.GROUP,
            "threadId": "g1",
            "data": {
                "msgId": "m1",
                "cliMsgId": "c1",
                "uidFrom": "u1",
                "idTo": "g1",
                "ts": "1700000000000",
                "content": "Hi",
            },
        }
    )
    assert m["text"] == "Hi"
    assert m["sender"].id == "u1"


async def test_session_stores(tmp_path: Path) -> None:
    sample = StoredSessionRecord(
        version=1,
        saved_at=1700000000000,
        credentials=SessionCredentials(
            cookie=[{"name": "zpw_sek", "value": "secret-cookie"}],
            imei="secret-imei",
            user_agent="TestAgent",
        ),
    )

    mem = MemorySessionStore()
    await mem.save(sample)
    assert await mem.load() == sample
    await mem.clear()
    assert await mem.load() is None

    file_store = FileSessionStore(tmp_path / "session.json")
    await file_store.save(sample)
    assert await file_store.load() == sample
    await file_store.clear()
    assert await file_store.load() is None

    enc_path = tmp_path / "session.enc.json"
    enc_store = EncryptedFileSessionStore(enc_path, "strong-passphrase-123456")
    await enc_store.save(sample)
    disk_raw = enc_path.read_text(encoding="utf-8")
    assert "secret-cookie" not in disk_raw
    assert await enc_store.load() == sample

    wrong = EncryptedFileSessionStore(enc_path, "wrong-passphrase-999999")
    with pytest.raises(SessionError):
        await wrong.load()
