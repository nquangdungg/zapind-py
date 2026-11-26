from __future__ import annotations

import asyncio
import base64
import json
from abc import ABC, abstractmethod
from typing import Any, Callable

from .errors import AuthenticationError, NetworkError, ValidationError, normalize_error
from .mappers import (
    map_connection_event,
    map_group_update_event,
    map_reaction_event,
    map_seen_event,
    map_typing_event,
    map_undo_event,
    map_unread_marks,
    map_user_update_event,
)
from .models import (
    ConnectionEvent,
    MediaBuffer,
    MediaInput,
    Message,
    PinnedConversations,
    QRCodeData,
    QRScannedData,
    Reaction,
    SendMessagePayload,
    SendMessageReceipt,
    SessionCredentials,
    ThreadType,
    UnreadMarks,
)


class TransportAdapter(ABC):
    """Abstract transport interface implemented by NodeBridgeTransport or test mocks."""

    @abstractmethod
    async def login_with_credentials(self, credentials: SessionCredentials) -> None: ...

    @abstractmethod
    async def login_with_qr(
        self,
        *,
        user_agent: str,
        language: str,
        on_qr_code: Callable[[QRCodeData], Any] | None = None,
        on_scanned: Callable[[QRScannedData], Any] | None = None,
        on_expired: Callable[[], Any] | None = None,
        on_declined: Callable[[], Any] | None = None,
    ) -> SessionCredentials: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_own_id(self) -> str: ...

    @abstractmethod
    async def fetch_account_info(self) -> dict[str, Any] | None: ...

    @abstractmethod
    async def update_profile(self, *, name: str, dob: str, gender: int) -> None: ...

    @abstractmethod
    async def update_bio(self, bio: str) -> None: ...

    @abstractmethod
    async def keep_alive(self) -> dict[str, int]: ...

    @abstractmethod
    async def get_user_info(self, user_ids: str | list[str]) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    async def find_user_by_phone(self, phone: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def find_user_by_username(self, username: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_last_online(self, user_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def get_all_friends(
        self, count: int | None = None, page: int | None = None
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def send_friend_request(self, user_id: str, message: str) -> None: ...

    @abstractmethod
    async def accept_friend_request(self, user_id: str) -> None: ...

    @abstractmethod
    async def reject_friend_request(self, user_id: str) -> None: ...

    @abstractmethod
    async def remove_friend(self, user_id: str) -> None: ...

    @abstractmethod
    async def block_user(self, user_id: str) -> None: ...

    @abstractmethod
    async def unblock_user(self, user_id: str) -> None: ...

    @abstractmethod
    async def set_friend_alias(self, user_id: str, alias: str) -> None: ...

    @abstractmethod
    async def get_online_friends(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_all_groups(self) -> dict[str, str]: ...

    @abstractmethod
    async def get_group_info(self, group_ids: str | list[str]) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    async def get_group_members_info(
        self, member_ids: str | list[str]
    ) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    async def create_group(self, *, name: str | None, members: list[str]) -> dict[str, str]: ...

    @abstractmethod
    async def add_group_members(self, group_id: str, user_ids: list[str]) -> None: ...

    @abstractmethod
    async def remove_group_members(self, group_id: str, user_ids: list[str]) -> None: ...

    @abstractmethod
    async def rename_group(self, group_id: str, name: str) -> None: ...

    @abstractmethod
    async def leave_group(self, group_id: str) -> None: ...

    @abstractmethod
    async def get_pinned_conversations(self) -> PinnedConversations: ...

    @abstractmethod
    async def get_unread_marks(self) -> UnreadMarks: ...

    @abstractmethod
    async def set_pinned_conversations(
        self, ids: list[str], pinned: bool, thread_type: ThreadType
    ) -> None: ...

    @abstractmethod
    async def set_mute(
        self, thread_id: str, duration_seconds: int, thread_type: ThreadType
    ) -> None: ...

    @abstractmethod
    async def send_typing(self, thread_id: str, thread_type: ThreadType) -> None: ...

    @abstractmethod
    async def send_message(
        self, thread_id: str, payload: SendMessagePayload, thread_type: ThreadType
    ) -> SendMessageReceipt: ...

    @abstractmethod
    async def get_group_chat_history(
        self,
        group_id: str,
        count: int | None = None,
        *,
        cursor: str | None = None,
        is_old: bool = False,
    ) -> dict[str, Any]: ...

    async def request_old_messages(
        self, thread_type: ThreadType, last_msg_id: str | None = None
    ) -> None:
        raise ValidationError("Current transport does not support request_old_messages")

    @abstractmethod
    async def undo_message(self, message: Message) -> None: ...

    @abstractmethod
    async def delete_message(self, message: Message, only_me: bool) -> None: ...

    @abstractmethod
    async def add_reaction(self, message: Message, reaction: Reaction | str) -> None: ...

    @abstractmethod
    async def mark_seen(self, messages: list[Message], thread_type: ThreadType) -> None: ...

    @abstractmethod
    async def upload_attachment(
        self, sources: list[MediaInput], thread_id: str, thread_type: ThreadType
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_account_settings(self) -> dict[str, Any]: ...

    @abstractmethod
    async def update_language(self, lang: str) -> None: ...

    @abstractmethod
    async def start_listening(
        self, handlers: dict[str, Callable[[Any], Any]], *, retry_on_close: bool = False
    ) -> None: ...

    @abstractmethod
    async def stop_listening(self) -> None: ...


_REALTIME_EVENT_KEYS = (
    "message",
    "old_messages",
    "reaction",
    "group.update",
    "user.update",
    "typing",
    "seen",
    "undo",
    "connected",
    "disconnected",
    "error",
)


def _serialize_media_input(item: MediaInput) -> str | dict[str, Any]:
    if isinstance(item, MediaBuffer):
        return {
            "dataBase64": base64.b64encode(item.data).decode("ascii"),
            "filename": item.filename,
            "totalSize": item.total_size if item.total_size is not None else len(item.data),
            "width": item.width,
            "height": item.height,
        }
    return item


def _map_bridge_event(event_name: str, data: Any) -> Any:
    if event_name == "reaction":
        return map_reaction_event(data)
    if event_name == "typing":
        return map_typing_event(data)
    if event_name == "seen":
        return map_seen_event(data)
    if event_name == "undo":
        return map_undo_event(data)
    if event_name == "group.update":
        return map_group_update_event(data)
    if event_name == "user.update":
        return map_user_update_event(data)
    if event_name in ("connected", "disconnected"):
        return map_connection_event(data)
    return data


def _is_stop_paging_error(err: BaseException) -> bool:
    raw_code = getattr(err, "raw_code", None)
    if raw_code == -69:
        return True
    return "-69" in str(err)


class NodeBridgeTransport(TransportAdapter):
    """
    Stdio JSON-RPC transport that delegates Zalo protocol operations to a Node.js (>= 20)
    worker running the `zapind` npm package.
    """

    def __init__(
        self,
        *,
        node_executable: str = "node",
        worker_script: str | None = None,
    ) -> None:
        self._node = node_executable
        self._worker_script = worker_script
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._connected = False
        self._own_id = ""
        self._handlers: dict[str, Callable[[Any], Any]] = {}
        self._disconnecting = False

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        if not self._worker_script:
            raise AuthenticationError(
                "NodeBridgeTransport requires a worker_script (or pass a custom TransportAdapter). "
                "See Zapind Python documentation for setting up the Node.js bridge worker."
            )
        self._disconnecting = False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._node,
                self._worker_script,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as err:
            raise NetworkError(
                f"Failed to start Node.js bridge process ({self._node})", cause=err
            ) from err
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        return self._proc

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _fail_all_pending(self, exc: BaseException) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(exc)

    async def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    frame = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(frame, dict):
                    continue
                if "id" in frame and frame["id"] in self._pending:
                    fut = self._pending.pop(frame["id"])
                    if not fut.done():
                        if "error" in frame and frame["error"] is not None:
                            fut.set_exception(normalize_error(frame["error"]))
                        else:
                            fut.set_result(frame.get("result"))
                elif "event" in frame:
                    event_name = str(frame["event"])
                    handler = self._handlers.get(event_name)
                    if handler:
                        handler(_map_bridge_event(event_name, frame.get("data")))
        except asyncio.CancelledError:
            raise
        finally:
            if not self._disconnecting:
                self._connected = False
                self._fail_all_pending(
                    NetworkError("Node.js bridge process exited unexpectedly")
                )
                disconnect_handler = self._handlers.get("disconnected")
                if disconnect_handler:
                    disconnect_handler(
                        ConnectionEvent(
                            connected=False,
                            code=1006,
                            reason="Node.js bridge process closed",
                        )
                    )

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        proc = await self._ensure_process()
        assert proc.stdin is not None
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = fut
        payload = json.dumps({"id": req_id, "method": method, "params": params or {}}) + "\n"
        try:
            async with self._write_lock:
                proc.stdin.write(payload.encode("utf-8"))
                await proc.stdin.drain()
        except Exception as err:
            self._pending.pop(req_id, None)
            raise NetworkError(f"Failed to send RPC request '{method}' to bridge", cause=err) from err
        try:
            return await fut
        except asyncio.CancelledError:
            self._pending.pop(req_id, None)
            raise

    def is_connected(self) -> bool:
        return self._connected

    def get_own_id(self) -> str:
        if not self._connected:
            raise AuthenticationError()
        return self._own_id

    async def login_with_credentials(self, credentials: SessionCredentials) -> None:
        res = await self._rpc(
            "loginWithCredentials",
            {
                "cookie": credentials.cookie,
                "imei": credentials.imei,
                "userAgent": credentials.user_agent,
                "language": credentials.language,
            },
        )
        self._connected = True
        self._own_id = str((res or {}).get("ownId") or "")

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
            self._handlers["qr_code"] = lambda d: on_qr_code(
                QRCodeData(
                    image_base64=str(d.get("imageBase64", "")),
                    image_data_url=str(d.get("imageDataUrl", "")),
                    code=d.get("code"),
                )
            )
        if on_scanned:
            self._handlers["qr_scanned"] = lambda d: on_scanned(
                QRScannedData(
                    display_name=str(d.get("displayName", "")),
                    avatar_url=str(d.get("avatarUrl", "")),
                )
            )
        if on_expired:
            self._handlers["qr_expired"] = lambda _: on_expired()
        if on_declined:
            self._handlers["qr_declined"] = lambda _: on_declined()

        try:
            res = await self._rpc("loginWithQR", {"userAgent": user_agent, "language": language})
        finally:
            for key in ("qr_code", "qr_scanned", "qr_expired", "qr_declined"):
                self._handlers.pop(key, None)

        self._connected = True
        self._own_id = str((res or {}).get("ownId") or "")
        creds = (res or {}).get("credentials") or {}
        return SessionCredentials(
            cookie=list(creds.get("cookie") or []),
            imei=str(creds.get("imei") or ""),
            user_agent=str(creds.get("userAgent") or user_agent),
            language=str(creds.get("language") or language),
        )

    async def disconnect(self) -> None:
        self._disconnecting = True
        self._connected = False
        self._own_id = ""
        self._handlers.clear()
        self._fail_all_pending(NetworkError("Bridge transport disconnected"))
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except Exception:
                self._proc.kill()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._proc = None
        self._reader_task = None
        self._stderr_task = None

    async def fetch_account_info(self) -> dict[str, Any] | None:
        return await self._rpc("fetchAccountInfo")

    async def update_profile(self, *, name: str, dob: str, gender: int) -> None:
        await self._rpc("updateProfile", {"name": name, "dob": dob, "gender": gender})

    async def update_bio(self, bio: str) -> None:
        await self._rpc("updateBio", {"bio": bio})

    async def keep_alive(self) -> dict[str, int]:
        res = await self._rpc("keepAlive")
        return {"config_version": int((res or {}).get("configVersion") or 0)}

    async def get_user_info(self, user_ids: str | list[str]) -> dict[str, dict[str, Any]]:
        return await self._rpc("getUserInfo", {"userIds": user_ids}) or {}

    async def find_user_by_phone(self, phone: str) -> dict[str, Any] | None:
        return await self._rpc("findUserByPhone", {"phone": phone})

    async def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        return await self._rpc("findUserByUsername", {"username": username})

    async def get_last_online(self, user_id: str) -> dict[str, Any]:
        return await self._rpc("getLastOnline", {"userId": user_id}) or {}

    async def get_all_friends(
        self, count: int | None = None, page: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._rpc("getAllFriends", {"count": count, "page": page}) or []

    async def send_friend_request(self, user_id: str, message: str) -> None:
        await self._rpc("sendFriendRequest", {"userId": user_id, "message": message})

    async def accept_friend_request(self, user_id: str) -> None:
        await self._rpc("acceptFriendRequest", {"userId": user_id})

    async def reject_friend_request(self, user_id: str) -> None:
        await self._rpc("rejectFriendRequest", {"userId": user_id})

    async def remove_friend(self, user_id: str) -> None:
        await self._rpc("removeFriend", {"userId": user_id})

    async def block_user(self, user_id: str) -> None:
        await self._rpc("blockUser", {"userId": user_id})

    async def unblock_user(self, user_id: str) -> None:
        await self._rpc("unblockUser", {"userId": user_id})

    async def set_friend_alias(self, user_id: str, alias: str) -> None:
        await self._rpc("setFriendAlias", {"userId": user_id, "alias": alias})

    async def get_online_friends(self) -> list[dict[str, Any]]:
        return await self._rpc("getOnlineFriends") or []

    async def get_all_groups(self) -> dict[str, str]:
        return await self._rpc("getAllGroups") or {}

    async def get_group_info(self, group_ids: str | list[str]) -> dict[str, dict[str, Any]]:
        return await self._rpc("getGroupInfo", {"groupIds": group_ids}) or {}

    async def get_group_members_info(
        self, member_ids: str | list[str]
    ) -> dict[str, dict[str, Any]]:
        return await self._rpc("getGroupMembersInfo", {"memberIds": member_ids}) or {}

    async def create_group(self, *, name: str | None, members: list[str]) -> dict[str, str]:
        return await self._rpc("createGroup", {"name": name, "members": members}) or {"groupId": ""}

    async def add_group_members(self, group_id: str, user_ids: list[str]) -> None:
        await self._rpc("addGroupMembers", {"groupId": group_id, "userIds": user_ids})

    async def remove_group_members(self, group_id: str, user_ids: list[str]) -> None:
        await self._rpc("removeGroupMembers", {"groupId": group_id, "userIds": user_ids})

    async def rename_group(self, group_id: str, name: str) -> None:
        await self._rpc("renameGroup", {"groupId": group_id, "name": name})

    async def leave_group(self, group_id: str) -> None:
        await self._rpc("leaveGroup", {"groupId": group_id})

    async def get_pinned_conversations(self) -> PinnedConversations:
        res = await self._rpc("getPinnedConversations") or {}
        return PinnedConversations(
            conversation_ids=list(res.get("conversationIds") or []),
            version=int(res.get("version") or 0),
        )

    async def get_unread_marks(self) -> UnreadMarks:
        res = await self._rpc("getUnreadMarks")
        return map_unread_marks(res if isinstance(res, dict) else None)

    async def set_pinned_conversations(
        self, ids: list[str], pinned: bool, thread_type: ThreadType
    ) -> None:
        await self._rpc(
            "setPinnedConversations",
            {"ids": ids, "pinned": pinned, "threadType": int(thread_type)},
        )

    async def set_mute(
        self, thread_id: str, duration_seconds: int, thread_type: ThreadType
    ) -> None:
        await self._rpc(
            "setMute",
            {
                "threadId": thread_id,
                "durationSeconds": duration_seconds,
                "threadType": int(thread_type),
            },
        )

    async def send_typing(self, thread_id: str, thread_type: ThreadType) -> None:
        await self._rpc("sendTyping", {"threadId": thread_id, "threadType": int(thread_type)})

    async def send_message(
        self, thread_id: str, payload: SendMessagePayload, thread_type: ThreadType
    ) -> SendMessageReceipt:
        rpc_params: dict[str, Any] = {
            "threadId": thread_id,
            "threadType": int(thread_type),
            "text": payload.text,
            "link": payload.link,
            "sticker": payload.sticker,
            "voice": payload.voice,
            "video": payload.video,
            "ttlMs": payload.ttl_ms,
        }
        if payload.reply_to is not None:
            rpc_params["replyTo"] = {
                "id": payload.reply_to.id,
                "clientMsgId": payload.reply_to.client_msg_id,
                "senderId": payload.reply_to.sender.id,
                "senderName": payload.reply_to.sender.name,
                "text": payload.reply_to.text,
                "msgType": payload.reply_to.msg_type,
                "timestamp": int(payload.reply_to.timestamp.timestamp() * 1000),
            }
        if payload.mentions:
            rpc_params["mentions"] = [
                {"userId": m.user_id, "pos": m.pos, "len": m.length} for m in payload.mentions
            ]
        if payload.styles:
            rpc_params["styles"] = [
                {
                    "start": s.start,
                    "len": s.length,
                    "style": s.style.value,
                    **({"indentSize": s.indent_size} if s.indent_size is not None else {}),
                }
                for s in payload.styles
            ]
        if payload.urgency is not None:
            rpc_params["urgency"] = int(payload.urgency)
        if payload.attachment is not None:
            rpc_params["attachment"] = _serialize_media_input(payload.attachment)
        if payload.attachments:
            rpc_params["attachments"] = [_serialize_media_input(a) for a in payload.attachments]

        res = await self._rpc("sendMessage", rpc_params)
        return SendMessageReceipt(
            message_id=res.get("messageId") if isinstance(res, dict) else None,
            attachment_message_ids=list(res.get("attachmentMessageIds") or [])
            if isinstance(res, dict)
            else [],
        )

    async def get_group_chat_history(
        self,
        group_id: str,
        count: int | None = None,
        *,
        cursor: str | None = None,
        is_old: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "groupId": group_id,
            "count": count,
            "cursor": cursor,
            "isOld": is_old,
        }
        try:
            res = await self._rpc("getGroupChatHistory", params)
        except Exception as err:
            if _is_stop_paging_error(err):
                return {
                    "messages": [],
                    "hasMore": False,
                    "nextCursor": None,
                    "isOld": is_old,
                }
            raise
        if not isinstance(res, dict):
            return {"messages": [], "hasMore": False, "nextCursor": None, "isOld": is_old}
        return {
            "messages": list(res.get("messages") or []),
            "hasMore": bool(res.get("hasMore")),
            "nextCursor": res.get("nextCursor"),
            "isOld": bool(res.get("isOld", is_old)),
        }

    async def request_old_messages(
        self, thread_type: ThreadType, last_msg_id: str | None = None
    ) -> None:
        await self._rpc(
            "requestOldMessages",
            {"threadType": int(thread_type), "lastMsgId": last_msg_id},
        )

    async def undo_message(self, message: Message) -> None:
        await self._rpc(
            "undoMessage",
            {
                "msgId": message.id,
                "cliMsgId": message.client_msg_id,
                "threadId": message.thread_id,
                "threadType": int(message.thread_type),
            },
        )

    async def delete_message(self, message: Message, only_me: bool) -> None:
        await self._rpc(
            "deleteMessage",
            {
                "msgId": message.id,
                "cliMsgId": message.client_msg_id,
                "senderId": message.sender.id,
                "threadId": message.thread_id,
                "threadType": int(message.thread_type),
                "onlyMe": only_me,
            },
        )

    async def add_reaction(self, message: Message, reaction: Reaction | str) -> None:
        await self._rpc(
            "addReaction",
            {
                "msgId": message.id,
                "cliMsgId": message.client_msg_id,
                "threadId": message.thread_id,
                "threadType": int(message.thread_type),
                "reaction": str(reaction.value if isinstance(reaction, Reaction) else reaction),
            },
        )

    async def mark_seen(self, messages: list[Message], thread_type: ThreadType) -> None:
        await self._rpc(
            "markSeen",
            {
                "messageIds": [m.id for m in messages],
                "threadType": int(thread_type),
            },
        )

    async def upload_attachment(
        self, sources: list[MediaInput], thread_id: str, thread_type: ThreadType
    ) -> list[dict[str, Any]]:
        serialized = [_serialize_media_input(s) for s in sources]
        return await self._rpc(
            "uploadAttachment",
            {"sources": serialized, "threadId": thread_id, "threadType": int(thread_type)},
        ) or []

    async def get_account_settings(self) -> dict[str, Any]:
        return await self._rpc("getAccountSettings") or {}

    async def update_language(self, lang: str) -> None:
        await self._rpc("updateLanguage", {"lang": lang})

    async def start_listening(
        self, handlers: dict[str, Callable[[Any], Any]], *, retry_on_close: bool = False
    ) -> None:
        self._handlers.update(handlers)
        try:
            await self._rpc("startListening", {"retryOnClose": retry_on_close})
        except BaseException:
            for key in _REALTIME_EVENT_KEYS:
                self._handlers.pop(key, None)
            raise

    async def stop_listening(self) -> None:
        try:
            if self._proc is not None and self._proc.returncode is None:
                await self._rpc("stopListening")
        finally:
            for key in _REALTIME_EVENT_KEYS:
                self._handlers.pop(key, None)
