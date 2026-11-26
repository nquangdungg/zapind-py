from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from zapind import (
    APIError,
    CloseReason,
    MediaBuffer,
    MemorySessionStore,
    Mention,
    MessageStyle,
    NetworkError,
    NodeBridgeTransport,
    NotFoundError,
    OldMessagesEvent,
    PermissionError,
    RateLimitError,
    Reaction,
    SendMessagePayload,
    SessionCredentials,
    SessionError,
    TextStyle,
    ThreadType,
    Urgency,
    ValidationError,
    Zapind,
)
from zapind.core import EventBus, InFlightDeduplicator, RedactingLogger
from zapind.errors import normalize_error
from zapind.mappers import map_message_kwargs, map_seen_event
from zapind.models import ConnectionEvent, Message, MessageSenderRef, ReactionEvent
from tests.test_integration import MockTransport


class _PaginationMockTransport(MockTransport):
    def __init__(self) -> None:
        super().__init__()
        self.history_calls: list[tuple[str, int | None, str | None, bool]] = []
        self.old_msg_requests: list[tuple[ThreadType, str | None]] = []
        self.fail_start_once = False
        self.return_stop_paging = False

    async def get_group_chat_history(
        self,
        group_id: str,
        count: int | None = None,
        *,
        cursor: str | None = None,
        is_old: bool = False,
    ) -> dict[str, Any]:
        self.history_calls.append((group_id, count, cursor, is_old))
        if self.return_stop_paging:
            raise APIError("Zalo stop paging error -69", raw_code=-69)
        return {
            "messages": [
                {
                    "type": int(ThreadType.GROUP),
                    "threadId": group_id,
                    "isSelf": False,
                    "data": {
                        "msgId": f"m-{cursor or 'latest'}",
                        "cliMsgId": "c-1",
                        "msgType": "webchat",
                        "uidFrom": "u-friend-1",
                        "idTo": group_id,
                        "dName": "Friend 1",
                        "ts": "1700000000000",
                        "content": "Paged message",
                        "quote": {
                            "ownerId": "u-9",
                            "fromD": "Alice",
                            "cliMsgId": "qc-1",
                            "globalMsgId": "qg-1",
                            "msg": "Original quote",
                            "ts": "1699999999000",
                        },
                    },
                }
            ],
            "hasMore": cursor is None,
            "nextCursor": "cursor-1" if cursor is None else None,
            "isOld": True,
        }

    async def request_old_messages(
        self, thread_type: ThreadType, last_msg_id: str | None = None
    ) -> None:
        self.old_msg_requests.append((thread_type, last_msg_id))

    async def start_listening(
        self, handlers: dict[str, Callable[[Any], Any]], *, retry_on_close: bool = False
    ) -> None:
        if self.fail_start_once:
            self.fail_start_once = False
            raise NetworkError("transient connect failure")
        await super().start_listening(handlers, retry_on_close=retry_on_close)


def test_zapind_py_001_http_status_message_classification() -> None:
    err_429 = normalize_error(RuntimeError("Request failed with status code 429"))
    assert isinstance(err_429, RateLimitError)
    assert err_429.retryable is True

    assert isinstance(normalize_error(RuntimeError("Request failed with status code 401")), SessionError)
    assert isinstance(normalize_error(RuntimeError("Request failed with status code 403")), PermissionError)
    assert isinstance(normalize_error(RuntimeError("Request failed with status code 404")), NotFoundError)

    err_502 = normalize_error(RuntimeError("HTTP 502 Bad Gateway"))
    assert isinstance(err_502, APIError)
    assert err_502.retryable is True

    err_raw = normalize_error({"code": "API_ERROR", "rawCode": -69, "message": "Stop paging"})
    assert isinstance(err_raw, APIError)
    assert err_raw.raw_code == -69


async def test_zapind_py_003_pagination_and_stop_paging() -> None:
    transport = _PaginationMockTransport()
    client = Zapind(transport=transport, rate_limit=False)
    await client.login()

    page1 = await client.conversations.get_messages("g-100", count=20)
    assert len(page1.items) == 1
    assert page1.has_more is True
    assert page1.next_cursor == "cursor-1"
    assert page1.is_old is True
    assert page1.items[0].reply_to is not None
    assert page1.items[0].reply_to.sender_name == "Alice"

    page2 = await client.conversations.get_messages(
        "g-100", count=20, cursor=page1.next_cursor, is_old=page1.is_old
    )
    assert page2.has_more is False
    assert transport.history_calls == [
        ("g-100", 20, None, False),
        ("g-100", 20, "cursor-1", True),
    ]

    # Stop-paging code -69 returns clean empty page instead of raising
    transport.return_stop_paging = True
    page3 = await client.conversations.get_messages("g-100", cursor="cursor-1", is_old=True)
    assert page3.items == []
    assert page3.has_more is False
    assert page3.next_cursor is None
    assert page3.is_old is True


async def test_zapind_py_004_old_messages_event_and_request() -> None:
    transport = _PaginationMockTransport()
    client = Zapind(transport=transport, rate_limit=False)
    await client.login()

    received: list[OldMessagesEvent] = []
    client.on("old_messages", lambda evt: received.append(evt))
    await client.start_listening()

    await client.conversations.request_old_messages(ThreadType.USER)
    await client.events.request_old_messages(ThreadType.GROUP, "m-100")
    assert transport.old_msg_requests == [
        (ThreadType.USER, None),
        (ThreadType.GROUP, "m-100"),
    ]

    transport.handlers["old_messages"](
        {
            "threadType": int(ThreadType.USER),
            "messages": [
                {
                    "threadId": "u-55",
                    "isSelf": False,
                    "data": {
                        "msgId": "old-1",
                        "cliMsgId": "old-c1",
                        "uidFrom": "u-55",
                        "idTo": "u-me",
                        "ts": "1700000000000",
                        "content": "Historic DM",
                    },
                }
            ],
        }
    )
    assert len(received) == 1
    assert received[0].thread_type == ThreadType.USER
    assert len(received[0].messages) == 1
    assert received[0].messages[0].text == "Historic DM"


def test_zapind_py_005_quote_sender_name_and_seen_ids() -> None:
    msg_kwargs = map_message_kwargs(
        {
            "type": "1",
            "threadId": "g-1",
            "data": {
                "msgId": "m-1",
                "cliMsgId": "c-1",
                "uidFrom": "u-2",
                "idTo": "g-1",
                "ts": "1700000000000",
                "content": "Reply",
                "quote": {
                    "ownerId": "u-1",
                    "fromD": "Bob",
                    "cliMsgId": "c-0",
                    "globalMsgId": "m-0",
                    "msg": "Hi",
                    "ts": "1699999990000",
                },
            },
        }
    )
    assert msg_kwargs["thread_type"] == ThreadType.GROUP
    assert msg_kwargs["reply_to"].sender_name == "Bob"

    seen = map_seen_event(
        {
            "threadId": "g-1",
            "isGroup": True,
            "data": {"realMsgId": "real-99", "seenUids": ["u-2", "u-3"]},
        }
    )
    assert seen.thread_type == ThreadType.GROUP
    assert seen.message_ids == ["real-99"]
    assert seen.seen_by == ["u-2", "u-3"]


async def test_zapind_py_006_session_only_login() -> None:
    store = MemorySessionStore()
    reasons: list[str] = []

    c1 = Zapind(session=store, transport=MockTransport(), rate_limit=False)
    c1.on("auth.required", lambda e: reasons.append(e.reason))
    with pytest.raises(SessionError):
        await c1.login(session_only=True)
    assert reasons == ["no_session"]

    with pytest.raises(ValidationError):
        await c1.login(session_only=True, force_qr=True)

    # Save a valid session, then restore cleanly with session_only=True
    await c1.login()
    assert reasons == ["no_session", "no_session"]
    c2 = Zapind(session=store, transport=MockTransport(), rate_limit=False)
    restored = await c2.login(session_only=True)
    assert restored.account_id == "u-me"

    # Expired session raises SessionError immediately instead of falling back to QR
    t3 = MockTransport()
    t3.fail_credential_login = True
    c3 = Zapind(session=store, transport=t3, rate_limit=False)
    c3.on("auth.required", lambda e: reasons.append(e.reason))
    with pytest.raises(SessionError):
        await c3.login(session_only=True)
    assert reasons == ["no_session", "no_session", "session_expired"]


async def test_zapind_py_007_dm_conversation_get_messages_guard() -> None:
    transport = _PaginationMockTransport()
    client = Zapind(transport=transport, rate_limit=False)
    await client.login()

    dm = await client.conversations.get("u-42", ThreadType.USER)
    with pytest.raises(ValidationError, match="request_old_messages"):
        await dm.get_messages()

    grp = await client.conversations.get("g-100", ThreadType.GROUP)
    page = await grp.get_messages(10)
    assert len(page.items) == 1


async def test_zapind_py_008_async_once_dedup_cancel_and_start_retry() -> None:
    # 1. EventBus.once() must not fire twice on synchronous double-emit
    bus = EventBus(RedactingLogger())
    calls = 0

    async def _only_once(_: Any) -> None:
        nonlocal calls
        calls += 1

    bus.once("ev", _only_once)
    bus.emit("ev", 1)
    bus.emit("ev", 2)
    await asyncio.sleep(0.02)
    assert calls == 1

    # 2. InFlightDeduplicator: cancelling waiter 1 must not cancel waiter 2
    dedup = InFlightDeduplicator()
    started = asyncio.Event()

    async def _slow() -> str:
        started.set()
        await asyncio.sleep(0.05)
        return "value"

    t1 = asyncio.create_task(dedup.run("k", _slow))
    await started.wait()
    t2 = asyncio.create_task(dedup.run("k", _slow))
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
    assert await t2 == "value"

    # 3. start_listening() resets _listening when transport fails, allowing retry
    transport = _PaginationMockTransport()
    transport.fail_start_once = True
    client = Zapind(transport=transport, rate_limit=False)
    await client.login()

    with pytest.raises(NetworkError):
        await client.start_listening()
    await client.start_listening()
    assert "message" in transport.handlers

    # 4. Async QR callback is awaited cleanly
    qr_seen: list[str] = []

    async def _on_qr(qr: Any) -> None:
        await asyncio.sleep(0.005)
        qr_seen.append(qr.image_base64)

    c_qr = Zapind(transport=MockTransport(), rate_limit=False)
    await c_qr.login(force_qr=True, on_qr_code=_on_qr)
    await asyncio.sleep(0.02)
    assert qr_seen == ["AAA"]


async def test_zapind_py_002_node_bridge_transport_end_to_end(tmp_path: Path) -> None:
    worker = tmp_path / "mock_bridge.py"
    worker.write_text(
        """
import json, sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    req_id = req["id"]
    method = req["method"]
    params = req.get("params") or {}

    if method == "loginWithCredentials":
        sys.stderr.write("bridge log line\\n" * 200)
        sys.stderr.flush()
        print(json.dumps({"id": req_id, "result": {"ownId": "u-bridge"}}), flush=True)
    elif method == "fetchAccountInfo":
        print(json.dumps({"id": req_id, "result": {"userId": "u-bridge", "displayName": "Bridge User"}}), flush=True)
    elif method == "getUnreadMarks":
        print(json.dumps({
            "id": req_id,
            "result": {
                "groups": [{"threadId": "g-1", "clientMsgId": "c1", "senderId": "u-2", "ts": 1700000000000}],
                "users": [],
            }
        }), flush=True)
    elif method == "sendMessage":
        assert params.get("replyTo", {}).get("id") == "m-orig"
        assert params.get("mentions") == [{"userId": "u-2", "pos": 0, "len": 4}]
        assert params.get("urgency") == 1
        assert params.get("attachment", {}).get("filename") == "note.txt"
        print(json.dumps({"id": req_id, "result": {"messageId": "m-sent-1", "attachmentMessageIds": ["a-1"]}}), flush=True)
    elif method == "uploadAttachment":
        assert isinstance(params["sources"][0], dict)
        assert params["sources"][0]["filename"] == "doc.bin"
        print(json.dumps({"id": req_id, "result": [{"fileType": "others", "fileId": "f-9", "fileUrl": "https://cdn/doc.bin", "fileName": "doc.bin", "totalSize": 3}]}), flush=True)
    elif method == "startListening":
        print(json.dumps({"id": req_id, "result": None}), flush=True)
        print(json.dumps({"event": "connected", "data": {"connected": True}}), flush=True)
        print(json.dumps({"event": "reaction", "data": {"threadId": "g-1", "threadType": 1, "senderId": "u-2", "icon": "/-heart", "targetMessageIds": ["m-1"], "isSelf": False, "timestamp": 1700000000000}}), flush=True)
    elif method == "requestOldMessages":
        print(json.dumps({"id": req_id, "result": None}), flush=True)
        print(json.dumps({"event": "old_messages", "data": {"threadType": params["threadType"], "messages": [{"threadId": "u-2", "isSelf": False, "data": {"msgId": "om-1", "cliMsgId": "oc-1", "uidFrom": "u-2", "idTo": "u-bridge", "ts": "1700000000000", "content": "Old"}}]}}), flush=True)
    elif method == "stopListening":
        print(json.dumps({"id": req_id, "result": None}), flush=True)
    elif method == "crashNow":
        sys.exit(2)
    else:
        print(json.dumps({"id": req_id, "result": {}}), flush=True)
""",
        encoding="utf-8",
    )

    transport = NodeBridgeTransport(node_executable=sys.executable, worker_script=str(worker))
    client = Zapind(transport=transport, rate_limit=False)

    creds = SessionCredentials(cookie=[{"name": "k", "value": "v"}], imei="imei-1", user_agent="UA")
    info = await client.login(credentials=creds)
    assert info.account_id == "u-bridge"

    # get_unread_marks returns mapped data instead of empty list
    unread = await client.conversations.get_unread()
    assert len(unread.groups) == 1
    assert unread.groups[0].thread_id == "g-1"

    # send_message forwards reply_to, mentions, styles, urgency, and MediaBuffer attachment
    dummy_msg = Message(
        id="m-orig",
        client_msg_id="c-orig",
        thread_id="g-1",
        thread_type=ThreadType.GROUP,
        sender=MessageSenderRef(id="u-2", name="Bob"),
        recipient_id="g-1",
        is_self=False,
        msg_type="webchat",
        text="orig",
        attachments=[],
        mentions=[],
        timestamp=info.authenticated_at,
        actions=client._actions,
    )
    receipt = await client.messages.send(
        "g-1",
        SendMessagePayload(
            text="@Bob hi",
            reply_to=dummy_msg,
            mentions=[Mention(user_id="u-2", pos=0, length=4)],
            styles=[MessageStyle(start=0, length=4, style=TextStyle.BOLD)],
            urgency=Urgency.IMPORTANT,
            attachment=MediaBuffer(data=b"abc", filename="note.txt"),
        ),
        thread_type=ThreadType.GROUP,
    )
    assert receipt.message_id == "m-sent-1"

    # upload_attachment serializes MediaBuffer
    uploaded = await client.media.upload(
        "g-1", MediaBuffer(data=b"xyz", filename="doc.bin"), thread_type=ThreadType.GROUP
    )
    assert uploaded[0].id == "f-9"

    # Typed events over JSON-RPC do not crash with AttributeError
    reactions: list[ReactionEvent] = []
    old_batches: list[OldMessagesEvent] = []
    disconnected: list[ConnectionEvent] = []
    client.on("reaction", lambda e: reactions.append(e))
    client.on("old_messages", lambda e: old_batches.append(e))
    client.on("disconnected", lambda e: disconnected.append(e))

    await client.start_listening()
    await client.conversations.request_old_messages(ThreadType.USER)
    await asyncio.sleep(0.05)

    assert len(reactions) == 1
    assert isinstance(reactions[0], ReactionEvent)
    assert reactions[0].icon == Reaction.HEART.value
    assert len(old_batches) == 1
    assert old_batches[0].messages[0].text == "Old"

    # stop_listening removes realtime handlers from transport
    await client.stop_listening()
    assert "reaction" not in transport._handlers
    assert "old_messages" not in transport._handlers

    # Subprocess crash fails pending RPC immediately (no hang!) and emits disconnected
    await client.start_listening()
    with pytest.raises(NetworkError, match="exited unexpectedly"):
        await transport._rpc("crashNow")
    await asyncio.sleep(0.02)
    assert client.is_authenticated() is False
    assert any(d.code == CloseReason.ABNORMAL_CLOSURE for d in disconnected)
    await client.destroy()
