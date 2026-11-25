from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Awaitable, Callable, Protocol, Union


class ThreadType(IntEnum):
    USER = 0
    GROUP = 1


class ClientStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    QR_PENDING = "qr_pending"
    SCANNED = "scanned"
    CONNECTED = "connected"
    ERROR = "error"


class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class GroupType(str, Enum):
    GROUP = "group"
    COMMUNITY = "community"


class CloseReason(IntEnum):
    MANUAL_CLOSURE = 1000
    ABNORMAL_CLOSURE = 1006
    DUPLICATE_CONNECTION = 3000
    KICK_CONNECTION = 3003


class TextStyle(str, Enum):
    BOLD = "b"
    ITALIC = "i"
    UNDERLINE = "u"
    STRIKE_THROUGH = "s"
    RED = "c_db342e"
    ORANGE = "c_f27806"
    YELLOW = "c_f7b503"
    GREEN = "c_15a85f"
    SMALL = "f_13"
    BIG = "f_18"
    UNORDERED_LIST = "lst_1"
    ORDERED_LIST = "lst_2"
    INDENT = "ind_$"


class Urgency(IntEnum):
    DEFAULT = 0
    IMPORTANT = 1
    URGENT = 2


class Reaction(str, Enum):
    HEART = "/-heart"
    LIKE = "/-strong"
    HAHA = ":>"
    WOW = ":o"
    CRY = ":-(( "[:-1]
    ANGRY = ":-h"
    NONE = ""


@dataclass(frozen=True)
class SessionCredentials:
    cookie: list[dict[str, Any]]
    imei: str
    user_agent: str
    language: str = "vi"


@dataclass(frozen=True)
class SessionInfo:
    account_id: str
    display_name: str
    avatar_url: str
    status: ClientStatus
    authenticated_at: datetime


@dataclass(frozen=True)
class StoredSessionRecord:
    version: int
    saved_at: int
    credentials: SessionCredentials
    account: dict[str, str] | None = None


@dataclass(frozen=True)
class QRCodeData:
    image_base64: str
    image_data_url: str
    code: str | None = None


@dataclass(frozen=True)
class QRScannedData:
    display_name: str
    avatar_url: str


@dataclass(frozen=True)
class MediaBuffer:
    data: bytes
    filename: str
    total_size: int | None = None
    width: int | None = None
    height: int | None = None


MediaInput = Union[str, MediaBuffer]


@dataclass(frozen=True)
class UploadedMedia:
    id: str
    kind: str
    url: str
    total_size: int = 0
    hd_url: str | None = None
    thumbnail_url: str | None = None
    file_name: str | None = None
    width: int | None = None
    height: int | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class Attachment:
    kind: str
    url: str | None = None
    thumbnail_url: str | None = None
    title: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class Mention:
    user_id: str
    pos: int
    length: int


@dataclass(frozen=True)
class MessageStyle:
    start: int
    length: int
    style: TextStyle
    indent_size: int | None = None


@dataclass(frozen=True)
class MessageQuote:
    owner_id: str
    client_msg_id: str
    global_msg_id: str
    text: str
    timestamp: datetime


@dataclass(frozen=True)
class SendMessagePayload:
    text: str | None = None
    attachment: MediaInput | None = None
    attachments: list[MediaInput] | None = None
    reply_to: Message | None = None
    mentions: list[Mention] | None = None
    styles: list[MessageStyle] | None = None
    urgency: Urgency | None = None
    ttl_ms: int | None = None
    link: dict[str, str] | None = None
    sticker: dict[str, int] | None = None
    voice: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    thread_type: ThreadType | None = None


SendMessageContent = Union[str, SendMessagePayload]


@dataclass(frozen=True)
class SendMessageReceipt:
    message_id: str | None
    attachment_message_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MessageSenderRef:
    id: str
    name: str | None = None


@dataclass(frozen=True)
class MessagePage:
    items: list[Message]
    has_more: bool
    next_cursor: str | None = None


@dataclass(frozen=True)
class GroupSummary:
    id: str
    version: str


@dataclass(frozen=True)
class GroupSettings:
    block_name: bool = False
    sign_admin_msg: bool = False
    add_member_only: bool = False
    set_topic_only: bool = False
    enable_msg_history: bool = False
    join_approval_required: bool = False
    lock_create_post: bool = False
    lock_create_poll: bool = False
    lock_send_msg: bool = False
    lock_view_member: bool = False


@dataclass(frozen=True)
class GroupMember:
    id: str
    display_name: str
    zalo_name: str
    avatar_url: str
    global_id: str | None = None
    account_status: int = 0


@dataclass(frozen=True)
class OnlineStatus:
    user_id: str
    status: str


@dataclass(frozen=True)
class LastOnlineInfo:
    user_id: str
    last_online_at: datetime | None
    show_online_status: bool


@dataclass(frozen=True)
class AccountSettings:
    show_online_status: bool = False
    display_seen_status: bool = False
    add_friend_via_phone: bool = False
    add_friend_via_contact: bool = False
    add_friend_via_group: bool = False
    add_friend_via_qr: bool = False
    accept_stranger_call: bool = False
    two_factor_enabled: bool = False


@dataclass(frozen=True)
class PinnedConversations:
    conversation_ids: list[str]
    version: int


@dataclass(frozen=True)
class UnreadMark:
    thread_id: str
    thread_type: ThreadType
    client_msg_id: str
    sender_id: str
    timestamp: datetime


@dataclass(frozen=True)
class UnreadMarks:
    groups: list[UnreadMark]
    users: list[UnreadMark]


@dataclass(frozen=True)
class ConnectionEvent:
    connected: bool
    code: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ReactionEvent:
    thread_id: str
    thread_type: ThreadType
    sender_id: str
    icon: str
    target_message_ids: list[str]
    is_self: bool
    timestamp: datetime


@dataclass(frozen=True)
class TypingEvent:
    thread_id: str
    thread_type: ThreadType
    user_id: str
    is_self: bool


@dataclass(frozen=True)
class SeenEvent:
    thread_id: str
    thread_type: ThreadType
    message_ids: list[str]
    seen_by: list[str]


@dataclass(frozen=True)
class UndoEvent:
    thread_id: str
    thread_type: ThreadType
    sender_id: str
    message_id: str
    client_msg_id: str
    is_self: bool


@dataclass(frozen=True)
class GroupUpdateEvent:
    group_id: str
    type: str
    actor_id: str | None = None
    target_ids: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserUpdateEvent:
    user_id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthRequiredEvent:
    reason: str
    error: Exception | None = None


class ModelActions(Protocol):
    async def send_message(
        self, target_id: str, content: SendMessageContent, thread_type: ThreadType
    ) -> SendMessageReceipt: ...
    async def reply_message(
        self, message: Message, content: SendMessageContent
    ) -> SendMessageReceipt: ...
    async def react_message(self, message: Message, reaction: Reaction | str) -> None: ...
    async def undo_message(self, message: Message) -> None: ...
    async def delete_message(self, message: Message, only_me: bool = True) -> None: ...
    async def mark_seen(self, messages: list[Message]) -> None: ...
    async def get_group_messages(self, group_id: str, count: int | None = None) -> MessagePage: ...
    async def send_typing(self, thread_id: str, thread_type: ThreadType) -> None: ...
    async def pin_conversation(
        self, thread_id: str, pinned: bool, thread_type: ThreadType
    ) -> None: ...
    async def mute_conversation(
        self, thread_id: str, duration_seconds: int, thread_type: ThreadType
    ) -> None: ...
    async def send_friend_request(self, user_id: str, message: str) -> None: ...
    async def block_user(self, user_id: str) -> None: ...
    async def unblock_user(self, user_id: str) -> None: ...
    async def set_friend_alias(self, user_id: str, alias: str) -> None: ...
    async def remove_friend(self, user_id: str) -> None: ...
    async def add_group_members(self, group_id: str, user_ids: list[str]) -> None: ...
    async def remove_group_members(self, group_id: str, user_ids: list[str]) -> None: ...
    async def rename_group(self, group_id: str, name: str) -> None: ...
    async def leave_group(self, group_id: str) -> None: ...


class User:
    """Contextual User model with direct messaging and relationship actions."""

    thread_type: ThreadType = ThreadType.USER

    def __init__(
        self,
        *,
        id: str,
        display_name: str,
        zalo_name: str,
        avatar_url: str,
        actions: ModelActions,
        username: str | None = None,
        cover_url: str | None = None,
        gender: Gender = Gender.UNKNOWN,
        birthday: str | None = None,
        status_message: str | None = None,
        phone_number: str | None = None,
        is_friend: bool = False,
        is_blocked: bool = False,
        last_active_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.display_name = display_name
        self.zalo_name = zalo_name
        self.avatar_url = avatar_url
        self.username = username
        self.cover_url = cover_url
        self.gender = gender
        self.birthday = birthday
        self.status_message = status_message
        self.phone_number = phone_number
        self.is_friend = is_friend
        self.is_blocked = is_blocked
        self.last_active_at = last_active_at
        self._actions = actions

    async def send(self, content: SendMessageContent) -> SendMessageReceipt:
        return await self._actions.send_message(self.id, content, ThreadType.USER)

    async def send_friend_request(self, message: str = "Hello!") -> None:
        await self._actions.send_friend_request(self.id, message)

    async def block(self) -> None:
        await self._actions.block_user(self.id)

    async def unblock(self) -> None:
        await self._actions.unblock_user(self.id)


class Profile(User):
    def __init__(
        self,
        *,
        global_id: str | None = None,
        is_business: bool = False,
        created_at: datetime | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.global_id = global_id
        self.is_business = is_business
        self.created_at = created_at


class Contact(User):
    def __init__(self, *, alias: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.alias = alias

    async def set_alias(self, alias: str) -> None:
        await self._actions.set_friend_alias(self.id, alias)

    async def remove(self) -> None:
        await self._actions.remove_friend(self.id)


class Group:
    """Contextual Group model with messaging and group management actions."""

    thread_type: ThreadType = ThreadType.GROUP

    def __init__(
        self,
        *,
        id: str,
        name: str,
        description: str,
        type: GroupType,
        creator_id: str,
        avatar_url: str,
        actions: ModelActions,
        full_avatar_url: str | None = None,
        member_count: int = 0,
        max_members: int = 0,
        member_ids: list[str] | None = None,
        admin_ids: list[str] | None = None,
        is_e2ee: bool = False,
        created_at: datetime | None = None,
        settings: GroupSettings | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.type = type
        self.creator_id = creator_id
        self.avatar_url = avatar_url
        self.full_avatar_url = full_avatar_url
        self.member_count = member_count
        self.max_members = max_members
        self.member_ids = member_ids or []
        self.admin_ids = admin_ids or []
        self.is_e2ee = is_e2ee
        self.created_at = created_at
        self.settings = settings
        self._actions = actions

    async def send(self, content: SendMessageContent) -> SendMessageReceipt:
        return await self._actions.send_message(self.id, content, ThreadType.GROUP)

    async def get_messages(self, count: int | None = None) -> MessagePage:
        return await self._actions.get_group_messages(self.id, count)

    async def add_members(self, user_ids: list[str]) -> None:
        await self._actions.add_group_members(self.id, user_ids)

    async def remove_members(self, user_ids: list[str]) -> None:
        await self._actions.remove_group_members(self.id, user_ids)

    async def rename(self, name: str) -> None:
        await self._actions.rename_group(self.id, name)

    async def leave(self) -> None:
        await self._actions.leave_group(self.id)


class Conversation:
    """Contextual Conversation handle for DM or Group threads."""

    def __init__(self, id: str, thread_type: ThreadType, actions: ModelActions) -> None:
        self.id = id
        self.thread_type = thread_type
        self._actions = actions

    @property
    def is_group(self) -> bool:
        return self.thread_type == ThreadType.GROUP

    async def send(self, content: SendMessageContent) -> SendMessageReceipt:
        return await self._actions.send_message(self.id, content, self.thread_type)

    async def get_messages(self, count: int | None = None) -> MessagePage:
        return await self._actions.get_group_messages(self.id, count)

    async def send_typing(self) -> None:
        await self._actions.send_typing(self.id, self.thread_type)

    async def pin(self, pinned: bool = True) -> None:
        await self._actions.pin_conversation(self.id, pinned, self.thread_type)

    async def mute(self, duration_seconds: int) -> None:
        await self._actions.mute_conversation(self.id, duration_seconds, self.thread_type)


class Message:
    """Normalized contextual Message model."""

    def __init__(
        self,
        *,
        id: str,
        client_msg_id: str,
        thread_id: str,
        thread_type: ThreadType,
        sender: MessageSenderRef,
        recipient_id: str,
        is_self: bool,
        msg_type: str,
        text: str,
        attachments: list[Attachment],
        mentions: list[Mention],
        timestamp: datetime,
        actions: ModelActions,
        reply_to: MessageQuote | None = None,
        ttl_ms: int = 0,
        st: int = 0,
        at: int = 0,
        cmd: int = 0,
    ) -> None:
        self.id = id
        self.client_msg_id = client_msg_id
        self.thread_id = thread_id
        self.thread_type = thread_type
        self.sender = sender
        self.recipient_id = recipient_id
        self.is_self = is_self
        self.msg_type = msg_type
        self.text = text
        self.attachments = attachments
        self.mentions = mentions
        self.reply_to = reply_to
        self.timestamp = timestamp
        self.ttl_ms = ttl_ms
        self.st = st
        self.at = at
        self.cmd = cmd
        self._actions = actions
        self.conversation = Conversation(thread_id, thread_type, actions)

    @property
    def is_group(self) -> bool:
        return self.thread_type == ThreadType.GROUP

    async def reply(self, content: SendMessageContent) -> SendMessageReceipt:
        return await self._actions.reply_message(self, content)

    async def react(self, reaction: Reaction | str) -> None:
        await self._actions.react_message(self, reaction)

    async def undo(self) -> None:
        await self._actions.undo_message(self)

    async def delete(self, only_me: bool = True) -> None:
        await self._actions.delete_message(self, only_me)

    async def mark_seen(self) -> None:
        await self._actions.mark_seen([self])


ThreadTarget = Union[str, User, Contact, Group, Conversation, Message]
EventHandler = Callable[[Any], Union[None, Awaitable[None]]]
UnsubscribeFn = Callable[[], None]
