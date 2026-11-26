from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .core import (
    EventBus,
    InFlightDeduplicator,
    PolicyExecutor,
    RateLimiter,
    RedactingLogger,
    RetryOptions,
    ThreadTypeRegistry,
    TTLCache,
)
from .errors import AuthenticationError, SessionError, ValidationError, normalize_error
from .mappers import map_message_kwargs, map_profile_kwargs
from .models import (
    AuthRequiredEvent,
    ClientStatus,
    CloseReason,
    ConnectionEvent,
    Conversation,
    EventHandler,
    Group,
    GroupUpdateEvent,
    Message,
    MessagePage,
    QRCodeData,
    QRScannedData,
    Reaction,
    ReactionEvent,
    SeenEvent,
    SendMessageContent,
    SendMessageReceipt,
    SessionCredentials,
    SessionInfo,
    StoredSessionRecord,
    ThreadType,
    TypingEvent,
    UndoEvent,
    UnsubscribeFn,
    User,
    UserUpdateEvent,
)
from .services import (
    AccountService,
    ContactsService,
    ConversationsService,
    GroupsService,
    MediaService,
    MessagesService,
    ServiceDeps,
    SettingsService,
    UsersService,
)
from .storage import FileSessionStore, MemorySessionStore, SessionStore
from .transport import NodeBridgeTransport, TransportAdapter

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"
)


class _ClientModelActions:
    def __init__(self, client: Zapind) -> None:
        self._client = client

    async def send_message(
        self, target_id: str, content: SendMessageContent, thread_type: ThreadType
    ) -> SendMessageReceipt:
        return await self._client.messages.send(target_id, content, thread_type=thread_type)

    async def reply_message(
        self, message: Message, content: SendMessageContent
    ) -> SendMessageReceipt:
        return await self._client.messages.reply(message, content)

    async def react_message(self, message: Message, reaction: Reaction | str) -> None:
        await self._client.messages.react(message, reaction)

    async def undo_message(self, message: Message) -> None:
        await self._client.messages.undo(message)

    async def delete_message(self, message: Message, only_me: bool = True) -> None:
        await self._client.messages.delete(message, only_me=only_me)

    async def mark_seen(self, messages: list[Message]) -> None:
        await self._client.messages.mark_seen(messages)

    async def get_group_messages(self, group_id: str, count: int | None = None) -> MessagePage:
        return await self._client.conversations.get_messages(group_id, count)

    async def send_typing(self, thread_id: str, thread_type: ThreadType) -> None:
        await self._client.conversations.send_typing(thread_id, thread_type=thread_type)

    async def pin_conversation(
        self, thread_id: str, pinned: bool, thread_type: ThreadType
    ) -> None:
        await self._client.conversations.pin(thread_id, pinned, thread_type=thread_type)

    async def mute_conversation(
        self, thread_id: str, duration_seconds: int, thread_type: ThreadType
    ) -> None:
        await self._client.conversations.mute(
            thread_id, duration_seconds, thread_type=thread_type
        )

    async def send_friend_request(self, user_id: str, message: str) -> None:
        await self._client.contacts.send_request(user_id, message)

    async def block_user(self, user_id: str) -> None:
        await self._client.contacts.block(user_id)

    async def unblock_user(self, user_id: str) -> None:
        await self._client.contacts.unblock(user_id)

    async def set_friend_alias(self, user_id: str, alias: str) -> None:
        await self._client.contacts.set_alias(user_id, alias)

    async def remove_friend(self, user_id: str) -> None:
        await self._client.contacts.remove(user_id)

    async def add_group_members(self, group_id: str, user_ids: list[str]) -> None:
        await self._client.groups.add_members(group_id, user_ids)

    async def remove_group_members(self, group_id: str, user_ids: list[str]) -> None:
        await self._client.groups.remove_members(group_id, user_ids)

    async def rename_group(self, group_id: str, name: str) -> None:
        await self._client.groups.rename(group_id, name)

    async def leave_group(self, group_id: str) -> None:
        await self._client.groups.leave(group_id)


class EventsNamespace:
    def __init__(self, client: Zapind) -> None:
        self._client = client

    def on(self, event: str, handler: EventHandler | None = None) -> Any:
        return self._client.on(event, handler)

    def once(self, event: str, handler: EventHandler) -> UnsubscribeFn:
        return self._client.once(event, handler)

    def off(self, event: str, handler: EventHandler) -> None:
        self._client.off(event, handler)

    async def start(self, *, retry_on_close: bool = False) -> None:
        await self._client.start_listening(retry_on_close=retry_on_close)

    async def stop(self) -> None:
        await self._client.stop_listening()


class Zapind:
    """Async-first, multi-account, contextual Zalo SDK client for Python."""

    def __init__(
        self,
        *,
        session: str | Path | SessionStore | None = None,
        timeout: float = 15.0,
        qr_timeout: float = 180.0,
        retry: RetryOptions | None = None,
        rate_limit: RateLimiter | bool = True,
        cache_ttl: float = 60.0,
        max_cache_entries: int = 500,
        logger: logging.Logger | None = None,
        debug: bool = False,
        self_listen: bool = False,
        user_agent: str = DEFAULT_USER_AGENT,
        language: str = "vi",
        transport: TransportAdapter | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValidationError("timeout must be a positive number")
        if qr_timeout <= 0:
            raise ValidationError("qr_timeout must be a positive number")
        if cache_ttl <= 0:
            raise ValidationError("cache_ttl must be a positive number")
        if max_cache_entries < 1:
            raise ValidationError("max_cache_entries must be a positive integer")
        resolved_retry = retry or RetryOptions()
        if resolved_retry.max_attempts < 1:
            raise ValidationError("retry.max_attempts must be >= 1")
        if resolved_retry.base_delay < 0:
            raise ValidationError("retry.base_delay must be >= 0")
        if resolved_retry.max_delay < resolved_retry.base_delay:
            raise ValidationError("retry.max_delay must be >= retry.base_delay")

        self._qr_timeout = qr_timeout
        self._self_listen = self_listen
        self._user_agent = user_agent
        self._language = language
        self._logger = RedactingLogger(logger, debug=debug)

        if isinstance(session, (str, Path)):
            self._store: SessionStore = FileSessionStore(session)
        elif session is not None:
            self._store = session
        else:
            self._store = MemorySessionStore()

        self._transport: TransportAdapter = transport or NodeBridgeTransport()
        limiter: RateLimiter | None
        if rate_limit is False:
            limiter = None
        elif isinstance(rate_limit, RateLimiter):
            limiter = rate_limit
        else:
            limiter = RateLimiter(max_requests_per_second=5.0, burst=10)

        self._executor = PolicyExecutor(
            default_timeout=timeout,
            retry_options=resolved_retry,
            rate_limiter=limiter,
            logger=self._logger,
        )
        self._event_bus = EventBus(self._logger)
        self._registry = ThreadTypeRegistry(groups_ttl=max(cache_ttl, 300.0))
        self._user_cache: TTLCache[str, User] = TTLCache(
            max_entries=max_cache_entries, default_ttl=cache_ttl
        )
        self._group_cache: TTLCache[str, Group] = TTLCache(
            max_entries=max(1, max_cache_entries // 2), default_ttl=cache_ttl
        )
        self._dedup = InFlightDeduplicator()
        self._actions = _ClientModelActions(self)

        deps = ServiceDeps(
            transport=self._transport,
            executor=self._executor,
            registry=self._registry,
            actions=self._actions,
            user_cache=self._user_cache,
            group_cache=self._group_cache,
            dedup=self._dedup,
        )

        self.account = AccountService(deps)
        self.users = UsersService(deps)
        self.contacts = ContactsService(deps)
        self.groups = GroupsService(deps)
        self.conversations = ConversationsService(deps)
        self.messages = MessagesService(deps)
        self.media = MediaService(deps, self.messages)
        self.settings = SettingsService(deps)
        self.events = EventsNamespace(self)

        self._status = ClientStatus.IDLE
        self._session_info: SessionInfo | None = None
        self._listening = False

    async def __aenter__(self) -> Zapind:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.logout(clear_session=False)

    def get_status(self) -> ClientStatus:
        return self._status

    def get_session(self) -> SessionInfo | None:
        return self._session_info

    def is_authenticated(self) -> bool:
        return (
            self._status == ClientStatus.CONNECTED
            and self._transport.is_connected()
            and self._session_info is not None
        )

    async def login(
        self,
        *,
        credentials: SessionCredentials | None = None,
        on_qr_code: Callable[[QRCodeData], Any] | None = None,
        on_scanned: Callable[[QRScannedData], Any] | None = None,
        on_expired: Callable[[], Any] | None = None,
        on_declined: Callable[[], Any] | None = None,
        timeout: float | None = None,
        force_qr: bool = False,
    ) -> SessionInfo:
        if credentials is not None:
            return await self._auth_with_credentials(credentials)

        if not force_qr:
            stored = await self._store.load()
            if stored and stored.credentials.cookie:
                try:
                    return await self._auth_with_credentials(stored.credentials)
                except Exception as err:
                    normalized = normalize_error(err, "Saved session expired or invalid")
                    self._event_bus.emit(
                        "auth.required",
                        AuthRequiredEvent(reason="session_expired", error=normalized),
                    )
            else:
                self._event_bus.emit("auth.required", AuthRequiredEvent(reason="no_session"))

        return await self._login_with_qr(
            on_qr_code=on_qr_code,
            on_scanned=on_scanned,
            on_expired=on_expired,
            on_declined=on_declined,
            timeout=timeout,
        )

    async def _build_and_persist_session(self, credentials: SessionCredentials) -> SessionInfo:
        account_id = ""
        display_name = ""
        avatar_url = ""
        try:
            raw = await self._executor.execute(
                self._transport.fetch_account_info,
                operation="auth.fetch_profile",
                idempotent=True,
            )
            if raw:
                p = map_profile_kwargs(raw)
                account_id = p["id"]
                display_name = p["display_name"]
                avatar_url = p["avatar_url"]
        except Exception:
            pass

        if not account_id:
            account_id = self._transport.get_own_id()

        info = SessionInfo(
            account_id=account_id,
            display_name=display_name,
            avatar_url=avatar_url,
            status=ClientStatus.CONNECTED,
            authenticated_at=datetime.now(tz=timezone.utc),
        )
        self._session_info = info
        self._status = ClientStatus.CONNECTED
        self._registry.register_user(account_id)

        await self._store.save(
            StoredSessionRecord(
                version=1,
                saved_at=int(time.time() * 1000),
                credentials=credentials,
                account={
                    "accountId": account_id,
                    "displayName": display_name,
                    "avatarUrl": avatar_url,
                },
            )
        )
        return info

    async def _auth_with_credentials(self, credentials: SessionCredentials) -> SessionInfo:
        if not credentials.cookie or not credentials.imei:
            raise ValidationError("credentials must include non-empty cookie and imei")
        self._status = ClientStatus.STARTING
        try:
            await self._executor.execute(
                lambda: self._transport.login_with_credentials(credentials),
                operation="auth.login_with_credentials",
                idempotent=False,
            )
            return await self._build_and_persist_session(credentials)
        except Exception as err:
            self._status = ClientStatus.ERROR
            self._session_info = None
            raise normalize_error(err, "Session authentication failed") from err

    async def _login_with_qr(
        self,
        *,
        on_qr_code: Callable[[QRCodeData], Any] | None = None,
        on_scanned: Callable[[QRScannedData], Any] | None = None,
        on_expired: Callable[[], Any] | None = None,
        on_declined: Callable[[], Any] | None = None,
        timeout: float | None = None,
    ) -> SessionInfo:
        self._status = ClientStatus.QR_PENDING
        effective_timeout = timeout if timeout is not None else self._qr_timeout

        def _handle_qr(qr: QRCodeData) -> None:
            self._status = ClientStatus.QR_PENDING
            if on_qr_code:
                on_qr_code(qr)

        def _handle_scanned(info: QRScannedData) -> None:
            self._status = ClientStatus.SCANNED
            if on_scanned:
                on_scanned(info)

        def _handle_expired() -> None:
            self._status = ClientStatus.IDLE
            if on_expired:
                on_expired()

        def _handle_declined() -> None:
            self._status = ClientStatus.IDLE
            if on_declined:
                on_declined()

        try:
            creds = await self._executor.execute(
                lambda: self._transport.login_with_qr(
                    user_agent=self._user_agent,
                    language=self._language,
                    on_qr_code=_handle_qr,
                    on_scanned=_handle_scanned,
                    on_expired=_handle_expired,
                    on_declined=_handle_declined,
                ),
                operation="auth.login_with_qr",
                timeout=effective_timeout,
                idempotent=False,
            )
            return await self._build_and_persist_session(creds)
        except Exception as err:
            self._status = ClientStatus.ERROR
            self._session_info = None
            raise normalize_error(err, "QR login failed") from err

    async def logout(self, *, clear_session: bool = True) -> None:
        await self.stop_listening()
        await self._transport.disconnect()
        self._session_info = None
        self._status = ClientStatus.IDLE
        self._user_cache.clear()
        self._group_cache.clear()
        self._registry.clear()
        self._dedup.clear()
        if clear_session:
            await self._store.clear()

    async def destroy(self, *, clear_session: bool = False) -> None:
        """Stop listeners, disconnect, clear caches, and remove all registered event listeners."""
        await self.logout(clear_session=clear_session)
        self._event_bus.remove_all_listeners()

    def on(self, event: str, handler: EventHandler | None = None) -> Any:
        """Register an event listener directly or as a decorator (`@client.on("message")`)."""
        if handler is not None:
            return self._event_bus.on(event, handler)

        def _decorator(fn: EventHandler) -> EventHandler:
            self._event_bus.on(event, fn)
            return fn

        return _decorator

    def once(self, event: str, handler: EventHandler) -> UnsubscribeFn:
        return self._event_bus.once(event, handler)

    def off(self, event: str, handler: EventHandler) -> None:
        self._event_bus.off(event, handler)

    async def start_listening(self, *, retry_on_close: bool = False) -> None:
        if not self.is_authenticated():
            raise AuthenticationError("Cannot start listening before login()")
        if self._listening:
            return
        self._listening = True

        def _on_message(raw: dict[str, Any]) -> None:
            kwargs = map_message_kwargs(raw)
            if kwargs["thread_type"] == ThreadType.GROUP:
                self._registry.register_group(kwargs["thread_id"])
            else:
                self._registry.register_user(kwargs["thread_id"])
            if kwargs["is_self"] and not self._self_listen:
                return
            self._event_bus.emit("message", Message(actions=self._actions, **kwargs))

        def _on_reaction(evt: ReactionEvent) -> None:
            if evt.is_self and not self._self_listen:
                return
            self._event_bus.emit("reaction", evt)

        def _on_group_update(evt: GroupUpdateEvent) -> None:
            if evt.group_id:
                self._registry.register_group(evt.group_id)
                self._group_cache.delete(evt.group_id)
            self._event_bus.emit("group.update", evt)

        def _on_user_update(evt: UserUpdateEvent) -> None:
            if evt.user_id:
                self._registry.register_user(evt.user_id)
                self._user_cache.delete(evt.user_id)
            self._event_bus.emit("user.update", evt)

        def _on_typing(evt: TypingEvent) -> None:
            if evt.is_self and not self._self_listen:
                return
            self._event_bus.emit("typing", evt)

        def _on_seen(evt: SeenEvent) -> None:
            self._event_bus.emit("seen", evt)

        def _on_undo(evt: UndoEvent) -> None:
            if evt.is_self and not self._self_listen:
                return
            self._event_bus.emit("undo", evt)

        def _on_connected(evt: ConnectionEvent) -> None:
            self._event_bus.emit("connected", evt)

        def _on_disconnected(evt: ConnectionEvent) -> None:
            self._listening = False
            self._event_bus.emit("disconnected", evt)
            if evt.code in (CloseReason.KICK_CONNECTION, CloseReason.DUPLICATE_CONNECTION):
                err = SessionError(
                    f"Realtime connection closed with code {evt.code}", raw_code=evt.code
                )
                self._status = ClientStatus.ERROR
                self._session_info = None
                self._event_bus.emit(
                    "auth.required", AuthRequiredEvent(reason="session_expired", error=err)
                )

        def _on_error(err: Any) -> None:
            self._event_bus.emit("error", normalize_error(err, "Realtime listener error"))

        await self._transport.start_listening(
            {
                "message": _on_message,
                "reaction": _on_reaction,
                "group.update": _on_group_update,
                "user.update": _on_user_update,
                "typing": _on_typing,
                "seen": _on_seen,
                "undo": _on_undo,
                "connected": _on_connected,
                "disconnected": _on_disconnected,
                "error": _on_error,
            },
            retry_on_close=retry_on_close,
        )

    async def stop_listening(self) -> None:
        if not self._listening:
            return
        self._listening = False
        await self._transport.stop_listening()
