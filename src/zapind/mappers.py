from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import (
    AccountSettings,
    Attachment,
    ConnectionEvent,
    Gender,
    GroupMember,
    GroupSettings,
    GroupSummary,
    GroupType,
    GroupUpdateEvent,
    LastOnlineInfo,
    Mention,
    MessageQuote,
    MessageSenderRef,
    OnlineStatus,
    ReactionEvent,
    SeenEvent,
    ThreadType,
    TypingEvent,
    UndoEvent,
    UnreadMark,
    UnreadMarks,
    UploadedMedia,
    UserUpdateEvent,
)


def _to_datetime(ts: Any) -> datetime:
    try:
        val = float(ts)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if val <= 0:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if val > 1e12:
        val /= 1000.0
    return datetime.fromtimestamp(val, tz=timezone.utc)


def _to_optional_datetime(ts: Any) -> datetime | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    val = float(ts)
    if val > 1e12:
        val /= 1000.0
    return datetime.fromtimestamp(val, tz=timezone.utc)


def map_user_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    uid = str(raw.get("userId") or raw.get("uid") or raw.get("id") or "")
    display_name = str(
        raw.get("displayName")
        or raw.get("display_name")
        or raw.get("zaloName")
        or raw.get("zalo_name")
        or ""
    )
    zalo_name = str(raw.get("zaloName") or raw.get("zalo_name") or display_name)
    raw_gender = raw.get("gender")
    if raw_gender in (0, "male"):
        gender = Gender.MALE
    elif raw_gender in (1, "female"):
        gender = Gender.FEMALE
    else:
        gender = Gender.UNKNOWN

    return {
        "id": uid,
        "username": raw.get("username") or None,
        "display_name": display_name,
        "zalo_name": zalo_name,
        "avatar_url": str(raw.get("avatar") or raw.get("avatarUrl") or ""),
        "cover_url": raw.get("cover") or raw.get("coverUrl") or None,
        "gender": gender,
        "birthday": raw.get("sdob") or raw.get("birthday") or None,
        "status_message": raw.get("status") or raw.get("statusMessage") or None,
        "phone_number": raw.get("phoneNumber") or raw.get("phone_number") or None,
        "is_friend": raw.get("isFr") == 1 or raw.get("isFriend") is True,
        "is_blocked": raw.get("isBlocked") in (1, True),
        "last_active_at": _to_optional_datetime(raw.get("lastActionTime")),
    }


def map_profile_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    base = map_user_kwargs(raw)
    biz = raw.get("bizPkg")
    is_biz = isinstance(biz, dict) and isinstance(biz.get("pkgId"), int) and biz["pkgId"] > 0
    return {
        **base,
        "global_id": raw.get("globalId") or None,
        "is_business": is_biz or bool(raw.get("isBusiness")),
        "created_at": _to_optional_datetime(raw.get("createdTs")),
    }


def map_group_kwargs(raw: dict[str, Any], fallback_id: str | None = None) -> dict[str, Any]:
    member_ids = [str(x) for x in (raw.get("memberIds") or [])]
    admin_ids = [str(x) for x in (raw.get("adminIds") or [])]
    raw_setting = raw.get("setting")
    settings: GroupSettings | None = None
    if isinstance(raw_setting, dict):
        settings = GroupSettings(
            block_name=raw_setting.get("blockName") == 1,
            sign_admin_msg=raw_setting.get("signAdminMsg") == 1,
            add_member_only=raw_setting.get("addMemberOnly") == 1,
            set_topic_only=raw_setting.get("setTopicOnly") == 1,
            enable_msg_history=raw_setting.get("enableMsgHistory") == 1,
            join_approval_required=raw_setting.get("joinAppr") == 1,
            lock_create_post=raw_setting.get("lockCreatePost") == 1,
            lock_create_poll=raw_setting.get("lockCreatePoll") == 1,
            lock_send_msg=raw_setting.get("lockSendMsg") == 1,
            lock_view_member=raw_setting.get("lockViewMember") == 1,
        )

    return {
        "id": str(raw.get("groupId") or raw.get("id") or fallback_id or ""),
        "name": str(raw.get("name") or ""),
        "description": str(raw.get("desc") or raw.get("description") or ""),
        "type": GroupType.COMMUNITY if raw.get("type") in (2, "community") else GroupType.GROUP,
        "creator_id": str(raw.get("creatorId") or ""),
        "avatar_url": str(raw.get("avt") or raw.get("avatarUrl") or ""),
        "full_avatar_url": raw.get("fullAvt") or raw.get("fullAvatarUrl") or None,
        "member_count": int(raw.get("totalMember") or len(member_ids)),
        "max_members": int(raw.get("maxMember") or 0),
        "member_ids": member_ids,
        "admin_ids": admin_ids,
        "is_e2ee": raw.get("e2ee") == 1 or raw.get("isE2EE") is True,
        "created_at": _to_optional_datetime(raw.get("createdTime")),
        "settings": settings,
    }


def map_group_summaries(grid_ver_map: dict[str, str] | None) -> list[GroupSummary]:
    if not isinstance(grid_ver_map, dict):
        return []
    return [GroupSummary(id=str(k), version=str(v or "")) for k, v in grid_ver_map.items()]


def map_group_member(raw: dict[str, Any]) -> GroupMember:
    return GroupMember(
        id=str(raw.get("id") or ""),
        display_name=str(raw.get("displayName") or raw.get("zaloName") or ""),
        zalo_name=str(raw.get("zaloName") or raw.get("displayName") or ""),
        avatar_url=str(raw.get("avatar") or ""),
        global_id=raw.get("globalId") or None,
        account_status=int(raw.get("accountStatus") or 0),
    )


def _infer_attachment_kind(msg_type: str, content_type: str | None) -> str:
    combined = f"{msg_type} {content_type or ''}".lower()
    if "photo" in combined or "image" in combined:
        return "image"
    if "video" in combined:
        return "video"
    if "voice" in combined or "audio" in combined:
        return "audio"
    if "sticker" in combined:
        return "sticker"
    if "link" in combined or "url" in combined:
        return "link"
    if "file" in combined:
        return "file"
    return "other"


def map_message_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    msg_type = str(data.get("msgType") or "webchat")
    content = data.get("content")
    text = ""
    attachments: list[Attachment] = []

    if isinstance(content, str):
        text = content
    elif isinstance(content, dict):
        text = str(content.get("title") or "")
        attachments.append(
            Attachment(
                kind=_infer_attachment_kind(msg_type, content.get("type")),
                url=content.get("href"),
                thumbnail_url=content.get("thumb"),
                title=text or None,
                description=content.get("description"),
            )
        )

    raw_mentions = data.get("mentions") or []
    mentions = [
        Mention(
            user_id=str(m.get("uid") or ""),
            pos=int(m.get("pos") or 0),
            length=int(m.get("len") or 0),
        )
        for m in raw_mentions
        if isinstance(m, dict)
    ]

    reply_to: MessageQuote | None = None
    raw_quote = data.get("quote")
    if isinstance(raw_quote, dict):
        sender_name_raw = raw_quote.get("fromD") or raw_quote.get("senderName")
        reply_to = MessageQuote(
            owner_id=str(raw_quote.get("ownerId") or ""),
            sender_name=str(sender_name_raw) if sender_name_raw else None,
            client_msg_id=str(raw_quote.get("cliMsgId") or ""),
            global_msg_id=str(raw_quote.get("globalMsgId") or ""),
            text=str(raw_quote.get("msg") or ""),
            timestamp=_to_datetime(raw_quote.get("ts")),
        )

    raw_type = raw.get("type")
    if raw_type is None:
        raw_type = raw.get("threadType")
    thread_type = (
        ThreadType.GROUP
        if raw_type in (ThreadType.GROUP, 1, "1") or raw.get("isGroup") is True
        else ThreadType.USER
    )

    return {
        "id": str(data.get("msgId") or ""),
        "client_msg_id": str(data.get("cliMsgId") or ""),
        "thread_id": str(raw.get("threadId") or data.get("idTo") or ""),
        "thread_type": thread_type,
        "sender": MessageSenderRef(
            id=str(data.get("uidFrom") or ""),
            name=data.get("dName") or None,
        ),
        "recipient_id": str(data.get("idTo") or ""),
        "is_self": bool(raw.get("isSelf")),
        "msg_type": msg_type,
        "text": text,
        "attachments": attachments,
        "mentions": mentions,
        "reply_to": reply_to,
        "timestamp": _to_datetime(data.get("ts")),
        "ttl_ms": int(data.get("ttl") or 0),
        "st": int(data.get("st") or 0),
        "at": int(data.get("at") or 0),
        "cmd": int(data.get("cmd") or 0),
    }


def map_uploaded_media(raw: dict[str, Any]) -> UploadedMedia:
    if raw.get("fileType") == "image":
        return UploadedMedia(
            id=str(raw.get("photoId") or ""),
            kind="image",
            url=str(raw.get("normalUrl") or raw.get("hdUrl") or ""),
            hd_url=raw.get("hdUrl"),
            thumbnail_url=raw.get("thumbUrl"),
            total_size=int(raw.get("totalSize") or 0),
            width=raw.get("width"),
            height=raw.get("height"),
        )
    return UploadedMedia(
        id=str(raw.get("fileId") or ""),
        kind="video" if raw.get("fileType") == "video" else "file",
        url=str(raw.get("fileUrl") or ""),
        file_name=raw.get("fileName"),
        total_size=int(raw.get("totalSize") or 0),
        checksum=raw.get("checksum"),
    )


def map_online_friends(entries: list[dict[str, Any]] | None) -> list[OnlineStatus]:
    if not isinstance(entries, list):
        return []
    return [
        OnlineStatus(user_id=str(e.get("userId") or ""), status=str(e.get("status") or ""))
        for e in entries
        if isinstance(e, dict)
    ]


def map_last_online(user_id: str, raw: dict[str, Any]) -> LastOnlineInfo:
    ts = raw.get("lastOnline")
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    return LastOnlineInfo(
        user_id=user_id,
        last_online_at=_to_optional_datetime(ts),
        show_online_status=bool(settings.get("show_online_status")),
    )


def map_account_settings(raw: dict[str, Any]) -> AccountSettings:
    return AccountSettings(
        show_online_status=bool(raw.get("show_online_status")),
        display_seen_status=raw.get("display_seen_status") == 1,
        add_friend_via_phone=raw.get("add_friend_via_phone") == 1,
        add_friend_via_contact=raw.get("add_friend_via_contact") == 1,
        add_friend_via_group=raw.get("add_friend_via_group") == 1,
        add_friend_via_qr=raw.get("add_friend_via_qr") == 1,
        accept_stranger_call=raw.get("accept_stranger_call") == 1,
        two_factor_enabled=raw.get("setting_2FA_status") == 1,
    )


def _parse_thread_type(raw_val: Any, *, is_group: bool = False) -> ThreadType:
    if is_group or raw_val in (ThreadType.GROUP, 1, "1"):
        return ThreadType.GROUP
    return ThreadType.USER


def map_unread_marks(raw: dict[str, Any] | None) -> UnreadMarks:
    if not isinstance(raw, dict):
        return UnreadMarks(groups=[], users=[])

    def _map_list(entries: Any, default_type: ThreadType) -> list[UnreadMark]:
        if not isinstance(entries, list):
            return []
        out: list[UnreadMark] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            out.append(
                UnreadMark(
                    thread_id=str(item.get("threadId") or item.get("id") or ""),
                    thread_type=_parse_thread_type(
                        item.get("threadType", item.get("type", default_type)),
                        is_group=default_type == ThreadType.GROUP,
                    ),
                    client_msg_id=str(item.get("clientMsgId") or item.get("cliMsgId") or ""),
                    sender_id=str(item.get("senderId") or item.get("fromUid") or ""),
                    timestamp=_to_datetime(item.get("timestamp") or item.get("ts")),
                )
            )
        return out

    return UnreadMarks(
        groups=_map_list(raw.get("groups") or raw.get("convsGroup"), ThreadType.GROUP),
        users=_map_list(raw.get("users") or raw.get("convsUser"), ThreadType.USER),
    )


def map_connection_event(raw: Any) -> ConnectionEvent:
    if isinstance(raw, ConnectionEvent):
        return raw
    d = raw if isinstance(raw, dict) else {}
    code_raw = d.get("code")
    code = int(code_raw) if isinstance(code_raw, int) and not isinstance(code_raw, bool) else None
    reason = str(d["reason"]) if d.get("reason") is not None else None
    return ConnectionEvent(connected=bool(d.get("connected")), code=code, reason=reason)


def map_reaction_event(raw: Any) -> ReactionEvent:
    if isinstance(raw, ReactionEvent):
        return raw
    d = raw if isinstance(raw, dict) else {}
    inner = d.get("data") if isinstance(d.get("data"), dict) else d
    content = inner.get("content") if isinstance(inner.get("content"), dict) else {}
    raw_targets = d.get("targetMessageIds") or d.get("target_message_ids")
    if isinstance(raw_targets, list):
        targets = [str(x) for x in raw_targets if x]
    else:
        r_msg = content.get("rMsg") or []
        targets = [str(x.get("gMsgID") or "") for x in r_msg if isinstance(x, dict) and x.get("gMsgID")]
    return ReactionEvent(
        thread_id=str(d.get("threadId") or d.get("thread_id") or ""),
        thread_type=_parse_thread_type(
            d.get("threadType", d.get("thread_type")),
            is_group=bool(d.get("isGroup")),
        ),
        sender_id=str(d.get("senderId") or d.get("sender_id") or inner.get("uidFrom") or ""),
        icon=str(d.get("icon") or content.get("rIcon") or ""),
        target_message_ids=targets,
        is_self=bool(d.get("isSelf") if "isSelf" in d else d.get("is_self")),
        timestamp=_to_datetime(d.get("timestamp") or inner.get("ts")),
    )


def map_typing_event(raw: Any) -> TypingEvent:
    if isinstance(raw, TypingEvent):
        return raw
    d = raw if isinstance(raw, dict) else {}
    inner = d.get("data") if isinstance(d.get("data"), dict) else d
    return TypingEvent(
        thread_id=str(d.get("threadId") or d.get("thread_id") or ""),
        thread_type=_parse_thread_type(
            d.get("threadType", d.get("thread_type")),
            is_group=bool(d.get("isGroup")),
        ),
        user_id=str(d.get("userId") or d.get("user_id") or inner.get("uid") or ""),
        is_self=bool(d.get("isSelf") if "isSelf" in d else d.get("is_self")),
    )


def map_seen_event(raw: Any) -> SeenEvent:
    if isinstance(raw, SeenEvent):
        return raw
    d = raw if isinstance(raw, dict) else {}
    inner = d.get("data") if isinstance(d.get("data"), dict) else d
    raw_ids = d.get("messageIds") or d.get("message_ids")
    if isinstance(raw_ids, list):
        message_ids = [str(x) for x in raw_ids if x]
    else:
        candidates = [
            inner.get("realMsgId"),
            inner.get("msgId"),
            inner.get("idMsg"),
        ]
        message_ids = [str(c) for c in candidates if c]
    raw_seen = d.get("seenBy") or d.get("seen_by") or inner.get("seenUids") or []
    seen_by = [str(u) for u in raw_seen if u] if isinstance(raw_seen, list) else []
    return SeenEvent(
        thread_id=str(d.get("threadId") or d.get("thread_id") or inner.get("idTo") or ""),
        thread_type=_parse_thread_type(
            d.get("threadType", d.get("thread_type")),
            is_group=bool(d.get("isGroup")),
        ),
        message_ids=message_ids,
        seen_by=seen_by,
    )


def map_undo_event(raw: Any) -> UndoEvent:
    if isinstance(raw, UndoEvent):
        return raw
    d = raw if isinstance(raw, dict) else {}
    inner = d.get("data") if isinstance(d.get("data"), dict) else d
    content = inner.get("content") if isinstance(inner.get("content"), dict) else {}
    return UndoEvent(
        thread_id=str(d.get("threadId") or d.get("thread_id") or ""),
        thread_type=_parse_thread_type(
            d.get("threadType", d.get("thread_type")),
            is_group=bool(d.get("isGroup")),
        ),
        sender_id=str(d.get("senderId") or d.get("sender_id") or inner.get("uidFrom") or ""),
        message_id=str(
            d.get("messageId")
            or d.get("message_id")
            or content.get("globalMsgId")
            or inner.get("msgId")
            or ""
        ),
        client_msg_id=str(
            d.get("clientMsgId")
            or d.get("client_msg_id")
            or content.get("cliMsgId")
            or inner.get("cliMsgId")
            or ""
        ),
        is_self=bool(d.get("isSelf") if "isSelf" in d else d.get("is_self")),
    )


def map_group_update_event(raw: Any) -> GroupUpdateEvent:
    if isinstance(raw, GroupUpdateEvent):
        return raw
    d = raw if isinstance(raw, dict) else {}
    data_obj = d.get("data") if isinstance(d.get("data"), dict) else {}
    raw_targets = d.get("targetIds") or d.get("target_ids") or []
    target_ids = [str(x) for x in raw_targets if x] if isinstance(raw_targets, list) else []
    actor = d.get("actorId") or d.get("actor_id") or data_obj.get("sourceId")
    return GroupUpdateEvent(
        group_id=str(d.get("groupId") or d.get("group_id") or d.get("threadId") or ""),
        type=str(d.get("type") or "unknown"),
        actor_id=str(actor) if actor else None,
        target_ids=target_ids,
        data=data_obj,
    )


def map_user_update_event(raw: Any) -> UserUpdateEvent:
    if isinstance(raw, UserUpdateEvent):
        return raw
    d = raw if isinstance(raw, dict) else {}
    data_obj = d.get("data") if isinstance(d.get("data"), dict) else {}
    return UserUpdateEvent(
        user_id=str(d.get("userId") or d.get("user_id") or d.get("threadId") or ""),
        type=str(d.get("type") or "unknown"),
        data=data_obj,
    )
