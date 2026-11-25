from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Callable

from .errors import AuthenticationError, NetworkError, normalize_error
from .models import (
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
        self, group_id: str, count: int | None = None
    ) -> dict[str, Any]: ...

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
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._connected = False
        self._own_id = ""
        self._handlers: dict[str, Callable[[Any], Any]] = {}

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        if not self._worker_script:
            raise AuthenticationError(
                "NodeBridgeTransport requires a worker_script (or pass a custom TransportAdapter). "
                "See Zapind Python documentation for setting up the Node.js bridge worker."
            )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._node,
                self._worker_script,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as err:
            raise NetworkError(f"Failed to start Node.js bridge process ({self._node})", cause=err) from err
        self._reader_task = asyncio.create_task(self._read_loop())
        return self._proc

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            try:
                frame = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if "id" in frame and frame["id"] in self._pending:
                fut = self._pending.pop(frame["id"])
                if not fut.done():
                    if "error" in frame and frame["error"] is not None:
                        fut.set_exception(normalize_error(frame["error"]))
                    else:
                        fut.set_result(frame.get("result"))
            elif "event" in frame:
                handler = self._handlers.get(str(frame["event"]))
                if handler:
                    handler(frame.get("data"))

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        proc = await self._ensure_process()
        assert proc.stdin is not None
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = fut
        payload = json.dumps({"id": req_id, "method": method, "params": params or {}}) + "\n"
        proc.stdin.write(payload.encode("utf-8"))
        await proc.stdin.drain()
        return await fut

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

        res = await self._rpc("loginWithQR", {"userAgent": user_agent, "language": language})
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
        self._connected = False
        self._own_id = ""
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except Exception:
                self._proc.kill()
        if self._reader_task is not None:
            self._reader_task.cancel()
        self._proc = None
        self._reader_task = None

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
        await self._rpc("getUnreadMarks")
        return UnreadMarks(groups=[], users=[])

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
        res = await self._rpc(
            "sendMessage",
            {
                "threadId": thread_id,
                "threadType": int(thread_type),
                "text": payload.text,
                "link": payload.link,
                "sticker": payload.sticker,
                "voice": payload.voice,
                "video": payload.video,
                "ttlMs": payload.ttl_ms,
            },
        )
        return SendMessageReceipt(
            message_id=res.get("messageId") if isinstance(res, dict) else None,
            attachment_message_ids=list(res.get("attachmentMessageIds") or [])
            if isinstance(res, dict)
            else [],
        )

    async def get_group_chat_history(
        self, group_id: str, count: int | None = None
    ) -> dict[str, Any]:
        return await self._rpc("getGroupChatHistory", {"groupId": group_id, "count": count}) or {
            "messages": [],
            "hasMore": False,
            "nextCursor": None,
        }

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
        paths = [s for s in sources if isinstance(s, str)]
        return await self._rpc(
            "uploadAttachment",
            {"sources": paths, "threadId": thread_id, "threadType": int(thread_type)},
        ) or []

    async def get_account_settings(self) -> dict[str, Any]:
        return await self._rpc("getAccountSettings") or {}

    async def update_language(self, lang: str) -> None:
        await self._rpc("updateLanguage", {"lang": lang})

    async def start_listening(
        self, handlers: dict[str, Callable[[Any], Any]], *, retry_on_close: bool = False
    ) -> None:
        self._handlers.update(handlers)
        await self._rpc("startListening", {"retryOnClose": retry_on_close})

    async def stop_listening(self) -> None:
        await self._rpc("stopListening")
