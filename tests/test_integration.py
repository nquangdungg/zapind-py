from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from zapind import (
    CloseReason,
    MemorySessionStore,
    Reaction,
    SendMessagePayload,
    SendMessageReceipt,
    SessionCredentials,
    SessionError,
    ThreadType,
    Zapind,
)
from zapind.models import ConnectionEvent, Message, PinnedConversations, QRCodeData, QRScannedData, UnreadMarks
from zapind.transport import TransportAdapter


class MockTransport(TransportAdapter):
    def __init__(self, own_id: str = "u-me") -> None:
        self.connected = False
        self.own_id = own_id
        self.fail_credential_login = False
        self.user_info_calls = 0
        self.group_info_calls = 0
        self.sent_messages: list[tuple[str, SendMessagePayload, ThreadType]] = []
        self.reactions: list[tuple[str, str]] = []
        self.handlers: dict[str, Callable[[Any], Any]] = {}

    async def login_with_credentials(self, credentials: SessionCredentials) -> None:
        if self.fail_credential_login:
            raise SessionError("Saved cookie expired")
        self.connected = True

    async def login_with_qr(
        self,
        *,
        user_agent: str,
        language: str,
        on_qr_code: Callable[[QRCodeData], Any] | None = None,
        on_scanned: Callable[[QRScannedData], Any] | None = None,
        on_expired: Callable[[], Any] | None = None,
        on_declined: Callable[[], Any] | None = None,
    ) -> SessionCredentials:
        if on_qr_code:
            on_qr_code(QRCodeData(image_base64="AAA", image_data_url="data:image/png;base64,AAA"))
        if on_scanned:
            on_scanned(QRScannedData(display_name="Mock Me", avatar_url=""))
        self.connected = True
        return SessionCredentials(
            cookie=[{"name": "zpw_sek", "value": f"cookie-{self.own_id}"}],
            imei=f"imei-{self.own_id}",
            user_agent=user_agent,
            language=language,
        )

    async def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def get_own_id(self) -> str:
        return self.own_id

    async def fetch_account_info(self) -> dict[str, Any] | None:
        return {"userId": self.own_id, "displayName": "Mock Me", "zaloName": "Mock Me", "avatar": ""}

    async def update_profile(self, *, name: str, dob: str, gender: int) -> None:
        pass

    async def update_bio(self, bio: str) -> None:
        pass

    async def keep_alive(self) -> dict[str, int]:
        return {"config_version": 1}

    async def get_user_info(self, user_ids: str | list[str]) -> dict[str, dict[str, Any]]:
        self.user_info_calls += 1
        ids = user_ids if isinstance(user_ids, list) else [user_ids]
        return {uid: {"userId": uid, "displayName": f"User {uid}", "avatar": ""} for uid in ids}

    async def find_user_by_phone(self, phone: str) -> dict[str, Any] | None:
        return {"uid": "u-phone", "display_name": "Phone User"} if phone == "0912345678" else None

    async def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        return {"uid": "u-bob", "display_name": "Bob"} if username == "bob" else None

    async def get_last_online(self, user_id: str) -> dict[str, Any]:
        return {"lastOnline": 1700000000, "settings": {"show_online_status": True}}

    async def get_all_friends(
        self, count: int | None = None, page: int | None = None
    ) -> list[dict[str, Any]]:
        return [{"userId": "u-friend-1", "displayName": "Friend 1", "isFr": 1}]

    async def send_friend_request(self, user_id: str, message: str) -> None:
        pass

    async def accept_friend_request(self, user_id: str) -> None:
        pass

    async def reject_friend_request(self, user_id: str) -> None:
        pass

    async def remove_friend(self, user_id: str) -> None:
        pass

    async def block_user(self, user_id: str) -> None:
        pass

    async def unblock_user(self, user_id: str) -> None:
        pass

    async def set_friend_alias(self, user_id: str, alias: str) -> None:
        pass

    async def get_online_friends(self) -> list[dict[str, Any]]:
        return [{"userId": "u-friend-1", "status": "online"}]

    async def get_all_groups(self) -> dict[str, str]:
        return {"g-100": "v1", "g-200": "v2"}

    async def get_group_info(self, group_ids: str | list[str]) -> dict[str, dict[str, Any]]:
        self.group_info_calls += 1
        ids = group_ids if isinstance(group_ids, list) else [group_ids]
        return {
            gid: {
                "groupId": gid,
                "name": f"Group {gid}",
                "desc": "Team",
                "type": 1,
                "creatorId": self.own_id,
                "memberIds": [self.own_id, "u-friend-1"],
                "adminIds": [self.own_id],
                "totalMember": 2,
                "maxMember": 1000,
            }
            for gid in ids
            if gid.startswith("g-")
        }

    async def get_group_members_info(
        self, member_ids: str | list[str]
    ) -> dict[str, dict[str, Any]]:
        ids = member_ids if isinstance(member_ids, list) else [member_ids]
        return {mid: {"id": mid, "displayName": f"Member {mid}", "avatar": ""} for mid in ids}

    async def create_group(self, *, name: str | None, members: list[str]) -> dict[str, str]:
        return {"groupId": "g-new"}

    async def add_group_members(self, group_id: str, user_ids: list[str]) -> None:
        pass

    async def remove_group_members(self, group_id: str, user_ids: list[str]) -> None:
        pass

    async def rename_group(self, group_id: str, name: str) -> None:
        pass

    async def leave_group(self, group_id: str) -> None:
        pass

    async def get_pinned_conversations(self) -> PinnedConversations:
        return PinnedConversations(conversation_ids=["g-100"], version=1)

    async def get_unread_marks(self) -> UnreadMarks:
        return UnreadMarks(groups=[], users=[])

    async def set_pinned_conversations(
        self, ids: list[str], pinned: bool, thread_type: ThreadType
    ) -> None:
        pass

    async def set_mute(
        self, thread_id: str, duration_seconds: int, thread_type: ThreadType
    ) -> None:
        pass

    async def send_typing(self, thread_id: str, thread_type: ThreadType) -> None:
        pass

    async def send_message(
        self, thread_id: str, payload: SendMessagePayload, thread_type: ThreadType
    ) -> SendMessageReceipt:
        self.sent_messages.append((thread_id, payload, thread_type))
        return SendMessageReceipt(message_id=str(100 + len(self.sent_messages)))

    async def get_group_chat_history(
        self, group_id: str, count: int | None = None
    ) -> dict[str, Any]:
        return {
            "messages": [
                {
                    "type": int(ThreadType.GROUP),
                    "threadId": group_id,
                    "isSelf": False,
                    "data": {
                        "msgId": "m-1",
                        "cliMsgId": "c-1",
                        "msgType": "webchat",
                        "uidFrom": "u-friend-1",
                        "idTo": group_id,
                        "dName": "Friend 1",
                        "ts": "1700000000000",
                        "content": "Hello group",
                    },
                }
            ],
            "hasMore": False,
            "nextCursor": None,
        }

    async def undo_message(self, message: Message) -> None:
        pass

    async def delete_message(self, message: Message, only_me: bool) -> None:
        pass

    async def add_reaction(self, message: Message, reaction: Reaction | str) -> None:
        self.reactions.append((message.id, reaction.value if isinstance(reaction, Reaction) else reaction))

    async def mark_seen(self, messages: list[Message], thread_type: ThreadType) -> None:
        pass

    async def upload_attachment(
        self, sources: list[Any], thread_id: str, thread_type: ThreadType
    ) -> list[dict[str, Any]]:
        return [
            {
                "fileType": "others",
                "fileId": "f-1",
                "fileUrl": "https://cdn/f-1.pdf",
                "fileName": "f-1.pdf",
                "totalSize": 512,
            }
        ]

    async def get_account_settings(self) -> dict[str, Any]:
        return {"show_online_status": True, "display_seen_status": 1, "setting_2FA_status": 1}

    async def update_language(self, lang: str) -> None:
        pass

    async def start_listening(
        self, handlers: dict[str, Callable[[Any], Any]], *, retry_on_close: bool = False
    ) -> None:
        self.handlers = handlers
        handlers["connected"](ConnectionEvent(connected=True))

    async def stop_listening(self) -> None:
        pass


async def test_session_restore_and_expired_fallback() -> None:
    store = MemorySessionStore()
    reasons: list[str] = []

    c1 = Zapind(session=store, transport=MockTransport(), rate_limit=False)
    c1.on("auth.required", lambda e: reasons.append(e.reason))
    s1 = await c1.login()
    assert s1.account_id == "u-me"
    assert reasons == ["no_session"]

    qr_calls = 0
    c2 = Zapind(session=store, transport=MockTransport(), rate_limit=False)
    await c2.login(on_qr_code=lambda _: None)
    assert c2.is_authenticated()

    t3 = MockTransport()
    t3.fail_credential_login = True
    c3 = Zapind(session=store, transport=t3, rate_limit=False)
    c3.on("auth.required", lambda e: reasons.append(e.reason))

    def _qr(_: QRCodeData) -> None:
        nonlocal qr_calls
        qr_calls += 1

    await c3.login(on_qr_code=_qr)
    assert qr_calls == 1
    assert reasons == ["no_session", "session_expired"]


async def test_contextual_models_thread_resolution_and_caching() -> None:
    transport = MockTransport()
    async with Zapind(transport=transport, rate_limit=False) as client:
        await client.login()

        # Auto-resolve ThreadType without manual enum
        await client.messages.send("g-100", "Hello group")
        await client.messages.send("u-42", "Hello user")
        assert transport.sent_messages[0][2] == ThreadType.GROUP
        assert transport.sent_messages[1][2] == ThreadType.USER

        # Contextual models
        user = await client.users.get("u-42")
        assert user is not None
        await user.send("From user.send")

        group = await client.groups.get("g-100")
        await group.send("From group.send")
        history = await group.get_messages(10)
        msg = history.items[0]
        await msg.reply("Replying!")
        await msg.react(Reaction.HEART)

        assert transport.sent_messages[4][1].reply_to is msg
        assert transport.reactions == [("m-1", Reaction.HEART.value)]

        # Caching + deduplication
        await asyncio.gather(
            client.users.get("u-99"),
            client.users.get("u-99"),
            client.users.get("u-99"),
        )
        assert transport.user_info_calls == 2  # u-42 + u-99 once


async def test_realtime_events_and_multi_account() -> None:
    t_a = MockTransport("bot-A")
    t_b = MockTransport("bot-B")
    c_a = Zapind(transport=t_a, rate_limit=False)
    c_b = Zapind(transport=t_b, rate_limit=False)
    await asyncio.gather(c_a.login(), c_b.login())

    received: list[str] = []
    expired: list[str] = []

    @c_a.on("message")
    async def on_msg(msg: Message) -> None:
        received.append(f"{msg.sender.id}:{msg.text}")
        await msg.reply("pong")

    c_a.on("auth.required", lambda e: expired.append(e.reason))
    await c_a.start_listening()

    t_a.handlers["message"](
        {
            "type": int(ThreadType.GROUP),
            "threadId": "g-100",
            "isSelf": False,
            "data": {
                "msgId": "m-9",
                "cliMsgId": "c-9",
                "uidFrom": "u-9",
                "idTo": "g-100",
                "ts": "1700000000000",
                "content": "ping",
            },
        }
    )
    t_a.handlers["disconnected"](ConnectionEvent(connected=False, code=CloseReason.KICK_CONNECTION))
    await asyncio.sleep(0.03)

    assert received == ["u-9:ping"]
    assert t_a.sent_messages[0][1].text == "pong"
    assert expired == ["session_expired"]
    assert c_b.is_authenticated() is True
    assert c_b.account.get_own_id() == "bot-B"


async def test_get_many_cache_validation_and_destroy() -> None:
    from zapind import RetryOptions, ValidationError

    with pytest.raises(ValidationError):
        Zapind(timeout=0)
    with pytest.raises(ValidationError):
        Zapind(cache_ttl=0)
    with pytest.raises(ValidationError):
        Zapind(max_cache_entries=0)
    with pytest.raises(ValidationError):
        Zapind(retry=RetryOptions(max_attempts=0))

    transport = MockTransport()
    client = Zapind(transport=transport, rate_limit=False)
    await client.login()

    with pytest.raises(ValidationError):
        await client.contacts.block("   ")
    with pytest.raises(ValidationError):
        await client.contacts.list(count=0)

    await client.users.get("u-1")
    assert transport.user_info_calls == 1

    many = await client.users.get_many(["u-1", "u-1", "u-2"])
    assert sorted(many.keys()) == ["u-1", "u-2"]
    assert transport.user_info_calls == 2

    # All cached -> 0 extra network calls
    await client.users.get_many(["u-1", "u-2"])
    assert transport.user_info_calls == 2

    await client.start_listening()
    await client.destroy()
    assert client.is_authenticated() is False
