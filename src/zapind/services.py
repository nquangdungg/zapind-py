from __future__ import annotations

from dataclasses import dataclass

from .core import InFlightDeduplicator, PolicyExecutor, ThreadTypeRegistry, TTLCache
from .errors import AuthenticationError, NotFoundError, ValidationError
from .mappers import (
    map_account_settings,
    map_group_kwargs,
    map_group_member,
    map_group_summaries,
    map_last_online,
    map_message_kwargs,
    map_online_friends,
    map_profile_kwargs,
    map_uploaded_media,
    map_user_kwargs,
)
from .models import (
    AccountSettings,
    Contact,
    Conversation,
    Group,
    GroupMember,
    GroupSummary,
    LastOnlineInfo,
    MediaInput,
    Message,
    MessagePage,
    ModelActions,
    OnlineStatus,
    PinnedConversations,
    Profile,
    Reaction,
    SendMessageContent,
    SendMessagePayload,
    SendMessageReceipt,
    ThreadTarget,
    ThreadType,
    UnreadMarks,
    UploadedMedia,
    User,
)
from .transport import TransportAdapter


@dataclass
class ServiceDeps:
    transport: TransportAdapter
    executor: PolicyExecutor
    registry: ThreadTypeRegistry
    actions: ModelActions
    user_cache: TTLCache[str, User]
    group_cache: TTLCache[str, Group]
    dedup: InFlightDeduplicator


def _ensure_connected(transport: TransportAdapter) -> None:
    if not transport.is_connected():
        raise AuthenticationError(
            "Zapind client is not authenticated. Call await client.login() first."
        )


def _require_non_empty(value: str, field_name: str) -> str:
    cleaned = value.strip() if isinstance(value, str) else ""
    if not cleaned:
        raise ValidationError(f"{field_name} must be a non-empty string")
    return cleaned


def _require_unique_ids(values: list[str], field_name: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        cleaned = v.strip() if isinstance(v, str) else ""
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    if not out:
        raise ValidationError(f"{field_name} must contain at least one non-empty ID")
    return out


def _assert_optional_positive_int(value: int | None, field_name: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
        raise ValidationError(f"{field_name} must be a positive integer")


async def _resolve_thread_target(
    deps: ServiceDeps,
    target: ThreadTarget,
    explicit_type: ThreadType | None = None,
) -> tuple[str, ThreadType]:
    async def _fetch_group_ids() -> list[str]:
        grid = await deps.executor.execute(
            deps.transport.get_all_groups,
            operation="registry.fetch_group_ids",
            idempotent=True,
        )
        return list(grid.keys())

    tid, ttype = await deps.registry.resolve(target, explicit_type, _fetch_group_ids)
    if not tid:
        raise ValidationError("Target thread_id must be a non-empty string")
    return tid, ttype


class AccountService:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps

    def get_own_id(self) -> str:
        _ensure_connected(self._deps.transport)
        return self._deps.transport.get_own_id()

    async def get_me(self) -> Profile:
        _ensure_connected(self._deps.transport)
        raw = await self._deps.executor.execute(
            self._deps.transport.fetch_account_info,
            operation="account.get_me",
            idempotent=True,
        )
        kwargs = map_profile_kwargs(raw or {"userId": self._deps.transport.get_own_id()})
        self._deps.registry.register_user(kwargs["id"])
        return Profile(actions=self._deps.actions, **kwargs)

    async def update_profile(self, *, name: str, dob: str, gender: str) -> None:
        _ensure_connected(self._deps.transport)
        cleaned = _require_non_empty(name, "name")
        gender_code = 0 if gender == "male" else 1
        await self._deps.executor.execute(
            lambda: self._deps.transport.update_profile(
                name=cleaned, dob=dob, gender=gender_code
            ),
            operation="account.update_profile",
            idempotent=True,
        )

    async def update_bio(self, bio: str) -> None:
        _ensure_connected(self._deps.transport)
        await self._deps.executor.execute(
            lambda: self._deps.transport.update_bio(bio),
            operation="account.update_bio",
            idempotent=True,
        )

    async def keep_alive(self) -> dict[str, int]:
        _ensure_connected(self._deps.transport)
        return await self._deps.executor.execute(
            self._deps.transport.keep_alive,
            operation="account.keep_alive",
            idempotent=True,
        )


class UsersService:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps

    async def get(self, user_id: str, *, skip_cache: bool = False) -> User | None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        if not skip_cache:
            cached = self._deps.user_cache.get(uid)
            if cached is not None:
                return cached

        async def _fetch() -> User | None:
            profiles = await self._deps.executor.execute(
                lambda: self._deps.transport.get_user_info(uid),
                operation="users.get",
                idempotent=True,
            )
            raw = profiles.get(uid) or (next(iter(profiles.values())) if profiles else None)
            if not raw:
                return None
            user = User(actions=self._deps.actions, **map_user_kwargs(raw))
            self._deps.registry.register_user(user.id)
            self._deps.user_cache.set(user.id, user)
            return user

        return await self._deps.dedup.run(f"user:{uid}", _fetch)

    async def get_many(
        self, user_ids: list[str], *, skip_cache: bool = False
    ) -> dict[str, User]:
        _ensure_connected(self._deps.transport)
        ids = _require_unique_ids(user_ids, "user_ids")
        out: dict[str, User] = {}
        missing: list[str] = []

        for uid in ids:
            cached = None if skip_cache else self._deps.user_cache.get(uid)
            if cached is not None:
                out[uid] = cached
            else:
                missing.append(uid)

        if not missing:
            return out

        raw_map = await self._deps.executor.execute(
            lambda: self._deps.transport.get_user_info(missing),
            operation="users.get_many",
            idempotent=True,
        )
        for uid, raw in raw_map.items():
            user = User(actions=self._deps.actions, **map_user_kwargs(raw))
            self._deps.registry.register_user(user.id)
            self._deps.user_cache.set(user.id, user)
            out[uid] = user
        return out

    async def find_by_phone(self, phone_number: str) -> User | None:
        _ensure_connected(self._deps.transport)
        phone = _require_non_empty(phone_number, "phone_number")
        raw = await self._deps.executor.execute(
            lambda: self._deps.transport.find_user_by_phone(phone),
            operation="users.find_by_phone",
            idempotent=True,
        )
        if not raw:
            return None
        user = User(actions=self._deps.actions, **map_user_kwargs(raw))
        self._deps.registry.register_user(user.id)
        self._deps.user_cache.set(user.id, user)
        return user

    async def find_by_username(self, username: str) -> User | None:
        _ensure_connected(self._deps.transport)
        cleaned = _require_non_empty(username, "username")
        raw = await self._deps.executor.execute(
            lambda: self._deps.transport.find_user_by_username(cleaned),
            operation="users.find_by_username",
            idempotent=True,
        )
        if not raw:
            return None
        user = User(actions=self._deps.actions, **map_user_kwargs(raw))
        self._deps.registry.register_user(user.id)
        self._deps.user_cache.set(user.id, user)
        return user

    async def get_last_online(self, user_id: str) -> LastOnlineInfo:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        raw = await self._deps.executor.execute(
            lambda: self._deps.transport.get_last_online(uid),
            operation="users.get_last_online",
            idempotent=True,
        )
        return map_last_online(uid, raw)


class ContactsService:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps

    async def list(self, *, count: int | None = None, page: int | None = None) -> list[Contact]:
        _ensure_connected(self._deps.transport)
        _assert_optional_positive_int(count, "count")
        _assert_optional_positive_int(page, "page")
        raw_list = await self._deps.executor.execute(
            lambda: self._deps.transport.get_all_friends(count, page),
            operation="contacts.list",
            idempotent=True,
        )
        out: list[Contact] = []
        for raw in raw_list:
            kwargs = map_user_kwargs(raw)
            kwargs["is_friend"] = True
            contact = Contact(actions=self._deps.actions, **kwargs)
            self._deps.registry.register_user(contact.id)
            self._deps.user_cache.set(contact.id, contact)
            out.append(contact)
        return out

    async def send_request(self, user_id: str, message: str = "Hello!") -> None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.send_friend_request(uid, message),
            operation="contacts.send_request",
            idempotent=False,
        )

    async def accept_request(self, user_id: str) -> None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.accept_friend_request(uid),
            operation="contacts.accept_request",
            idempotent=True,
        )

    async def reject_request(self, user_id: str) -> None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.reject_friend_request(uid),
            operation="contacts.reject_request",
            idempotent=True,
        )

    async def remove(self, user_id: str) -> None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.remove_friend(uid),
            operation="contacts.remove",
            idempotent=True,
        )
        self._deps.user_cache.delete(uid)

    async def block(self, user_id: str) -> None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.block_user(uid),
            operation="contacts.block",
            idempotent=True,
        )
        self._deps.user_cache.delete(uid)

    async def unblock(self, user_id: str) -> None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.unblock_user(uid),
            operation="contacts.unblock",
            idempotent=True,
        )
        self._deps.user_cache.delete(uid)

    async def set_alias(self, user_id: str, alias: str) -> None:
        _ensure_connected(self._deps.transport)
        uid = _require_non_empty(user_id, "user_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.set_friend_alias(uid, alias),
            operation="contacts.set_alias",
            idempotent=True,
        )

    async def get_online(self) -> list[OnlineStatus]:
        _ensure_connected(self._deps.transport)
        raw = await self._deps.executor.execute(
            self._deps.transport.get_online_friends,
            operation="contacts.get_online",
            idempotent=True,
        )
        return map_online_friends(raw)


class GroupsService:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps

    async def list(self) -> list[GroupSummary]:
        _ensure_connected(self._deps.transport)
        grid = await self._deps.executor.execute(
            self._deps.transport.get_all_groups,
            operation="groups.list",
            idempotent=True,
        )
        summaries = map_group_summaries(grid)
        self._deps.registry.register_groups(g.id for g in summaries)
        return summaries

    async def get(self, group_id: str, *, skip_cache: bool = False) -> Group:
        _ensure_connected(self._deps.transport)
        gid = _require_non_empty(group_id, "group_id")
        if not skip_cache:
            cached = self._deps.group_cache.get(gid)
            if cached is not None:
                return cached

        async def _fetch() -> Group:
            raw_map = await self._deps.executor.execute(
                lambda: self._deps.transport.get_group_info(gid),
                operation="groups.get",
                idempotent=True,
            )
            raw = raw_map.get(gid)
            if not raw:
                raise NotFoundError(f'Group "{gid}" was not found')
            group = Group(actions=self._deps.actions, **map_group_kwargs(raw, gid))
            self._deps.registry.register_group(group.id)
            self._deps.group_cache.set(group.id, group)
            return group

        return await self._deps.dedup.run(f"group:{gid}", _fetch)

    async def get_many(
        self, group_ids: list[str], *, skip_cache: bool = False
    ) -> dict[str, Group]:
        _ensure_connected(self._deps.transport)
        ids = _require_unique_ids(group_ids, "group_ids")
        out: dict[str, Group] = {}
        missing: list[str] = []

        for gid in ids:
            cached = None if skip_cache else self._deps.group_cache.get(gid)
            if cached is not None:
                out[gid] = cached
            else:
                missing.append(gid)

        if not missing:
            return out

        raw_map = await self._deps.executor.execute(
            lambda: self._deps.transport.get_group_info(missing),
            operation="groups.get_many",
            idempotent=True,
        )
        for gid, raw in raw_map.items():
            group = Group(actions=self._deps.actions, **map_group_kwargs(raw, gid))
            self._deps.registry.register_group(group.id)
            self._deps.group_cache.set(group.id, group)
            out[gid] = group
        return out

    async def create(self, *, members: list[str], name: str | None = None) -> Group:
        _ensure_connected(self._deps.transport)
        clean_members = _require_unique_ids(members, "members")
        created = await self._deps.executor.execute(
            lambda: self._deps.transport.create_group(name=name, members=clean_members),
            operation="groups.create",
            idempotent=False,
        )
        gid = str(created.get("groupId") or "")
        self._deps.registry.register_group(gid)
        return await self.get(gid, skip_cache=True)

    async def get_members(self, member_ids: list[str]) -> list[GroupMember]:
        _ensure_connected(self._deps.transport)
        ids = _require_unique_ids(member_ids, "member_ids")
        profiles = await self._deps.executor.execute(
            lambda: self._deps.transport.get_group_members_info(ids),
            operation="groups.get_members",
            idempotent=True,
        )
        return [map_group_member(raw) for raw in profiles.values()]

    async def add_members(self, group_id: str, user_ids: list[str]) -> None:
        _ensure_connected(self._deps.transport)
        gid = _require_non_empty(group_id, "group_id")
        ids = _require_unique_ids(user_ids, "user_ids")
        await self._deps.executor.execute(
            lambda: self._deps.transport.add_group_members(gid, ids),
            operation="groups.add_members",
            idempotent=True,
        )
        self._deps.group_cache.delete(gid)

    async def remove_members(self, group_id: str, user_ids: list[str]) -> None:
        _ensure_connected(self._deps.transport)
        gid = _require_non_empty(group_id, "group_id")
        ids = _require_unique_ids(user_ids, "user_ids")
        await self._deps.executor.execute(
            lambda: self._deps.transport.remove_group_members(gid, ids),
            operation="groups.remove_members",
            idempotent=True,
        )
        self._deps.group_cache.delete(gid)

    async def rename(self, group_id: str, name: str) -> None:
        _ensure_connected(self._deps.transport)
        gid = _require_non_empty(group_id, "group_id")
        cleaned = _require_non_empty(name, "name")
        await self._deps.executor.execute(
            lambda: self._deps.transport.rename_group(gid, cleaned),
            operation="groups.rename",
            idempotent=True,
        )
        self._deps.group_cache.delete(gid)

    async def leave(self, group_id: str) -> None:
        _ensure_connected(self._deps.transport)
        gid = _require_non_empty(group_id, "group_id")
        await self._deps.executor.execute(
            lambda: self._deps.transport.leave_group(gid),
            operation="groups.leave",
            idempotent=False,
        )
        self._deps.group_cache.delete(gid)


class ConversationsService:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps

    async def get(self, conversation_id: str, thread_type: ThreadType | None = None) -> Conversation:
        _ensure_connected(self._deps.transport)
        cid = _require_non_empty(conversation_id, "conversation_id")
        tid, ttype = await _resolve_thread_target(self._deps, cid, thread_type)
        return Conversation(tid, ttype, self._deps.actions)

    async def get_messages(self, conversation_id: str, count: int | None = None) -> MessagePage:
        _ensure_connected(self._deps.transport)
        cid = _require_non_empty(conversation_id, "conversation_id")
        _assert_optional_positive_int(count, "count")
        res = await self._deps.executor.execute(
            lambda: self._deps.transport.get_group_chat_history(cid, count),
            operation="conversations.get_messages",
            idempotent=True,
        )
        items = [
            Message(actions=self._deps.actions, **map_message_kwargs(raw))
            for raw in (res.get("messages") or [])
        ]
        return MessagePage(
            items=items,
            has_more=bool(res.get("hasMore")),
            next_cursor=res.get("nextCursor"),
        )

    async def get_pinned(self) -> PinnedConversations:
        _ensure_connected(self._deps.transport)
        return await self._deps.executor.execute(
            self._deps.transport.get_pinned_conversations,
            operation="conversations.get_pinned",
            idempotent=True,
        )

    async def get_unread(self) -> UnreadMarks:
        _ensure_connected(self._deps.transport)
        return await self._deps.executor.execute(
            self._deps.transport.get_unread_marks,
            operation="conversations.get_unread",
            idempotent=True,
        )

    async def pin(
        self,
        target: ThreadTarget,
        pinned: bool = True,
        *,
        thread_type: ThreadType | None = None,
    ) -> None:
        _ensure_connected(self._deps.transport)
        tid, ttype = await _resolve_thread_target(self._deps, target, thread_type)
        await self._deps.executor.execute(
            lambda: self._deps.transport.set_pinned_conversations([tid], pinned, ttype),
            operation="conversations.pin",
            idempotent=True,
        )

    async def mute(
        self,
        target: ThreadTarget,
        duration_seconds: int,
        *,
        thread_type: ThreadType | None = None,
    ) -> None:
        _ensure_connected(self._deps.transport)
        tid, ttype = await _resolve_thread_target(self._deps, target, thread_type)
        await self._deps.executor.execute(
            lambda: self._deps.transport.set_mute(tid, duration_seconds, ttype),
            operation="conversations.mute",
            idempotent=True,
        )

    async def send_typing(
        self, target: ThreadTarget, *, thread_type: ThreadType | None = None
    ) -> None:
        _ensure_connected(self._deps.transport)
        tid, ttype = await _resolve_thread_target(self._deps, target, thread_type)
        await self._deps.executor.execute(
            lambda: self._deps.transport.send_typing(tid, ttype),
            operation="conversations.send_typing",
            idempotent=True,
        )


class MessagesService:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps

    async def send(
        self,
        target: ThreadTarget,
        content: SendMessageContent,
        *,
        thread_type: ThreadType | None = None,
    ) -> SendMessageReceipt:
        _ensure_connected(self._deps.transport)
        payload = SendMessagePayload(text=content) if isinstance(content, str) else content
        has_media = bool(
            payload.attachment
            or payload.attachments
            or payload.link
            or payload.sticker
            or payload.voice
            or payload.video
        )
        if not payload.text and not has_media:
            raise ValidationError("Message payload must contain text or media/link/sticker")

        tid, ttype = await _resolve_thread_target(
            self._deps,
            target,
            thread_type if thread_type is not None else payload.thread_type,
        )

        return await self._deps.executor.execute(
            lambda: self._deps.transport.send_message(tid, payload, ttype),
            operation="messages.send",
            idempotent=False,
        )

    async def reply(self, message: Message, content: SendMessageContent) -> SendMessageReceipt:
        if isinstance(content, str):
            payload = SendMessagePayload(text=content, reply_to=message)
        else:
            payload = SendMessagePayload(
                text=content.text,
                attachment=content.attachment,
                attachments=content.attachments,
                reply_to=message,
                mentions=content.mentions,
                styles=content.styles,
                urgency=content.urgency,
                ttl_ms=content.ttl_ms,
                link=content.link,
                sticker=content.sticker,
                voice=content.voice,
                video=content.video,
                thread_type=message.thread_type,
            )
        return await self.send(message, payload, thread_type=message.thread_type)

    async def react(self, message: Message, reaction: Reaction | str) -> None:
        _ensure_connected(self._deps.transport)
        await self._deps.executor.execute(
            lambda: self._deps.transport.add_reaction(message, reaction),
            operation="messages.react",
            idempotent=True,
        )

    async def undo(self, message: Message) -> None:
        _ensure_connected(self._deps.transport)
        await self._deps.executor.execute(
            lambda: self._deps.transport.undo_message(message),
            operation="messages.undo",
            idempotent=True,
        )

    async def delete(self, message: Message, *, only_me: bool = True) -> None:
        _ensure_connected(self._deps.transport)
        await self._deps.executor.execute(
            lambda: self._deps.transport.delete_message(message, only_me),
            operation="messages.delete",
            idempotent=True,
        )

    async def mark_seen(self, messages: list[Message]) -> None:
        _ensure_connected(self._deps.transport)
        if not messages:
            raise ValidationError("mark_seen requires at least one message")
        ttype = messages[0].thread_type
        await self._deps.executor.execute(
            lambda: self._deps.transport.mark_seen(messages, ttype),
            operation="messages.mark_seen",
            idempotent=True,
        )


class MediaService:
    def __init__(self, deps: ServiceDeps, messages: MessagesService) -> None:
        self._deps = deps
        self._messages = messages

    async def upload(
        self,
        target: ThreadTarget,
        sources: MediaInput | list[MediaInput],
        *,
        thread_type: ThreadType | None = None,
    ) -> list[UploadedMedia]:
        _ensure_connected(self._deps.transport)
        items = sources if isinstance(sources, list) else [sources]
        if not items:
            raise ValidationError("upload requires at least one media item")
        tid, ttype = await _resolve_thread_target(self._deps, target, thread_type)
        raw = await self._deps.executor.execute(
            lambda: self._deps.transport.upload_attachment(items, tid, ttype),
            operation="media.upload",
            idempotent=False,
        )
        return [map_uploaded_media(item) for item in raw]

    async def send(
        self,
        target: ThreadTarget,
        media: MediaInput | list[MediaInput],
        *,
        caption: str | None = None,
    ) -> SendMessageReceipt:
        attachments = media if isinstance(media, list) else [media]
        return await self._messages.send(
            target, SendMessagePayload(text=caption, attachments=attachments)
        )


class SettingsService:
    def __init__(self, deps: ServiceDeps) -> None:
        self._deps = deps

    async def get(self) -> AccountSettings:
        _ensure_connected(self._deps.transport)
        raw = await self._deps.executor.execute(
            self._deps.transport.get_account_settings,
            operation="settings.get",
            idempotent=True,
        )
        return map_account_settings(raw)

    async def update_language(self, lang: str) -> None:
        _ensure_connected(self._deps.transport)
        normalized = lang.upper()
        if normalized not in ("VI", "EN"):
            raise ValidationError('lang must be "VI" or "EN"')
        await self._deps.executor.execute(
            lambda: self._deps.transport.update_language(normalized),
            operation="settings.update_language",
            idempotent=True,
        )
