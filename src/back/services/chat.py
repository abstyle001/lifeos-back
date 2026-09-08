"""用户私聊（Direct Chat）业务逻辑。

设计原则（详见 docs/direct-chat-design.md）：
- 门槛：发起会话要求「我已关注对方」；建立会话后即使取关仍可读历史，
  但发新消息要求「至少一方仍关注另一方」，防止取关后被继续骚扰。
- 私密用户 / 不存在用户一律按 404 处理（沿用 follows.resolve_visible_target 口径，
  不泄露存在性）。
- 会话对同一对用户唯一（models.Conversation 用 user_a_id < user_b_id 规约化 + UniqueConstraint）。
- 软删除按成员维度：hidden_at_a / hidden_at_b 分别记录；对方发新消息时接收方的
  hidden_at_* 会被清空（自动"取消隐藏"）。
- 未读计数走「最后已读消息 id」而非独立计数字段，避免并发漂移。
- 所有会改变状态的操作在 commit 之后调 chat_hub.hub.publish 推送 SSE 事件；
  hub 是进程内内存对象，跨实例漏推由前端兜底轮询弥补（Vercel Serverless 妥协）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from ..models import Conversation, DirectMessage, Follow, ProfileSettings, User
from ..schemas import ChatPeerOut, ConversationOut, DirectMessageOut
from .chat_hub import hub

# ---------------------------------------------------------------------------
# 常量与异常
# ---------------------------------------------------------------------------

CONVERSATION_LIST_LIMIT = 50
MESSAGE_PAGE_LIMIT = 50
MESSAGE_PAGE_MAX = 200

# 频控：单用户全局 60 条/分钟、单会话 2 条/秒（内存字典实现，Vercel 冷启动会重置）
GLOBAL_RATE_LIMIT = 60
GLOBAL_RATE_WINDOW_SECONDS = 60.0
CONVERSATION_RATE_LIMIT = 2
CONVERSATION_RATE_WINDOW_SECONDS = 1.0


class ChatError(Exception):
    """私聊模块业务异常基类，router 层统一翻译为 HTTP。"""


class SelfChatError(ChatError):
    """不能与自己聊天。"""


class NeedFollowError(ChatError):
    """发起会话前需要先关注对方。"""


class PeerNotVisibleError(ChatError):
    """对方不存在或已转私密；对外统一 404，不泄露存在性。"""


class ConversationNotFoundError(ChatError):
    """会话不存在。"""


class NotConversationMemberError(ChatError):
    """当前用户不是该会话成员。"""


class MutualUnfollowError(ChatError):
    """双方已互取关，不能再发新消息（历史仍可读）。"""


class EmptyContentError(ChatError):
    """消息内容去除首尾空白后为空。"""


class RateLimitError(ChatError):
    """触发频控。"""


# ---------------------------------------------------------------------------
# 频控（进程内内存；测试可用 reset_rate_limits() 清理）
# ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_rate_global: dict[int, deque[float]] = {}
_rate_conv: dict[tuple[int, int], deque[float]] = {}


def reset_rate_limits() -> None:
    """清空频控状态。测试用例之间调用，避免模块级字典污染。"""
    with _rate_lock:
        _rate_global.clear()
        _rate_conv.clear()


def check_rate_limit(user_id: int, conversation_id: int) -> None:
    """滑动窗口频控：超限抛 RateLimitError；未超限则记录本次时间戳。"""
    now = time.monotonic()
    with _rate_lock:
        g = _rate_global.setdefault(user_id, deque())
        while g and now - g[0] > GLOBAL_RATE_WINDOW_SECONDS:
            g.popleft()
        c = _rate_conv.setdefault((user_id, conversation_id), deque())
        while c and now - c[0] > CONVERSATION_RATE_WINDOW_SECONDS:
            c.popleft()
        if len(g) >= GLOBAL_RATE_LIMIT or len(c) >= CONVERSATION_RATE_LIMIT:
            raise RateLimitError
        g.append(now)
        c.append(now)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

Slot = Literal["a", "b"]


def _my_slot(conv: Conversation, me_id: int) -> Slot:
    """返回我在会话中占的槽位（"a" 或 "b"）。"""
    if conv.user_a_id == me_id:
        return "a"
    if conv.user_b_id == me_id:
        return "b"
    # 理论上 resolve_membership 已拦截；这里再兜一层
    raise NotConversationMemberError


def _peer_slot(conv: Conversation, me_id: int) -> Slot:
    return "b" if _my_slot(conv, me_id) == "a" else "a"


def _peer_id(conv: Conversation, me_id: int) -> int:
    return conv.user_b_id if conv.user_a_id == me_id else conv.user_a_id


def _peer_user(db: Session, conv: Conversation, me_id: int) -> User:
    peer_id = _peer_id(conv, me_id)
    user = db.get(User, peer_id)
    if user is None:
        # FK cascade 应该已经清理会话；走到这里说明数据不一致
        raise PeerNotVisibleError
    return user


def _is_public(db: Session, user_id: int) -> bool:
    """无 ProfileSettings 行按私密处理（与 follows.is_public_profile 口径一致）。"""
    return bool(
        db.scalar(select(ProfileSettings.is_public).where(ProfileSettings.user_id == user_id))
    )


def _is_following(db: Session, follower_id: int, followee_id: int) -> bool:
    return (
        db.scalar(
            select(Follow.id).where(
                Follow.follower_id == follower_id,
                Follow.followee_id == followee_id,
            )
        )
        is not None
    )


def _to_peer_out(user: User) -> ChatPeerOut:
    return ChatPeerOut(username=user.username, avatar=user.avatar, level=user.level)


def _to_message_out(msg: DirectMessage, sender_username: str) -> DirectMessageOut:
    return DirectMessageOut(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        sender_username=sender_username,
        content=msg.content,
        created_at=msg.created_at,
        client_message_id=msg.client_message_id,
    )


def _username_map(db: Session, ids: set[int]) -> dict[int, str]:
    """批量取 username，避免 N+1 查询。"""
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.username).where(User.id.in_(ids))).all()
    return {row[0]: row[1] for row in rows}


def _serialize_event(payload: dict[str, Any]) -> dict[str, Any]:
    """SSE 事件必须是 JSON 可序列化的 dict；此函数只做类型标注，实际序列化在 router 层。"""
    return payload


def _last_read_column(conv: Conversation, slot: Slot) -> int | None:
    return conv.last_read_message_id_a if slot == "a" else conv.last_read_message_id_b


def _set_last_read(conv: Conversation, slot: Slot, message_id: int | None) -> None:
    if slot == "a":
        conv.last_read_message_id_a = message_id
    else:
        conv.last_read_message_id_b = message_id


def _hidden_at(conv: Conversation, slot: Slot) -> datetime | None:
    return conv.hidden_at_a if slot == "a" else conv.hidden_at_b


def _set_hidden_at(conv: Conversation, slot: Slot, value: datetime | None) -> None:
    if slot == "a":
        conv.hidden_at_a = value
    else:
        conv.hidden_at_b = value


def _utcnow() -> datetime:
    # SQLite 的 server_default=func.now() 返回的是 UTC naive；此处保持一致，
    # 应用层用 naive UTC，前端负责本地化。
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 会话生命周期
# ---------------------------------------------------------------------------


def _find_conversation(db: Session, user_a_id: int, user_b_id: int) -> Conversation | None:
    """按规约化 (min, max) 查找已存在的会话。"""
    lo, hi = (user_a_id, user_b_id) if user_a_id < user_b_id else (user_b_id, user_a_id)
    return db.scalar(
        select(Conversation).where(
            Conversation.user_a_id == lo,
            Conversation.user_b_id == hi,
        )
    )


def get_or_create_conversation(db: Session, me: User, peer_username: str) -> Conversation:
    """发起或获取与某人的会话（幂等）。

    - peer 不存在或私密 → PeerNotVisibleError（router 翻 404）
    - peer 是自己 → SelfChatError
    - 我未关注 peer → NeedFollowError
    """
    peer = db.scalar(select(User).where(User.username == peer_username))
    if peer is None:
        raise PeerNotVisibleError
    if peer.id == me.id:
        raise SelfChatError
    if not _is_public(db, peer.id):
        raise PeerNotVisibleError
    if not _is_following(db, me.id, peer.id):
        raise NeedFollowError

    existing = _find_conversation(db, me.id, peer.id)
    if existing is not None:
        # 重新发起时清掉自己的 hidden_at（"我又想聊了"），不影响对方
        slot = _my_slot(existing, me.id)
        if _hidden_at(existing, slot) is not None:
            _set_hidden_at(existing, slot, None)
            db.commit()
        return existing

    lo, hi = (me.id, peer.id) if me.id < peer.id else (peer.id, me.id)
    conv = Conversation(user_a_id=lo, user_b_id=hi)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def resolve_membership(db: Session, me: User, conversation_id: int) -> Conversation:
    """按 id 取会话并校验我是成员；否则抛 ConversationNotFoundError。

    与 follows 一致：不存在与非成员用同一个异常，不泄露存在性。
    """
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise ConversationNotFoundError
    if conv.user_a_id != me.id and conv.user_b_id != me.id:
        raise ConversationNotFoundError
    return conv


def can_send_message(db: Session, me: User, conv: Conversation) -> None:
    """发消息前的门槛校验。

    - 对方转私密 → PeerNotVisibleError（对外 404，不泄露存在性）
    - 双方互取关 → MutualUnfollowError（对外 403）
    """
    peer = _peer_user(db, conv, me.id)
    if not _is_public(db, peer.id):
        raise PeerNotVisibleError
    me_follows_peer = _is_following(db, me.id, peer.id)
    peer_follows_me = _is_following(db, peer.id, me.id)
    if not (me_follows_peer or peer_follows_me):
        raise MutualUnfollowError


# ---------------------------------------------------------------------------
# 消息 CRUD
# ---------------------------------------------------------------------------


def send_message(
    db: Session,
    me: User,
    conv: Conversation,
    content: str,
    client_message_id: str | None = None,
) -> DirectMessage:
    """发送一条消息（幂等：同 client_message_id 已存在则返回既有消息）。

    顺序：门槛 → 频控 → 幂等查重 → 插入 → 清对方 hidden_at → commit → SSE 广播。
    """
    can_send_message(db, me, conv)
    check_rate_limit(me.id, conv.id)

    stripped = content.strip()
    if not stripped:
        raise EmptyContentError

    # 幂等：同一 (conversation_id, sender_id, client_message_id) 已存在则复用
    if client_message_id:
        existing = db.scalar(
            select(DirectMessage).where(
                DirectMessage.conversation_id == conv.id,
                DirectMessage.sender_id == me.id,
                DirectMessage.client_message_id == client_message_id,
            )
        )
        if existing is not None:
            return existing

    msg = DirectMessage(
        conversation_id=conv.id,
        sender_id=me.id,
        content=stripped,
        client_message_id=client_message_id,
    )
    db.add(msg)

    # 接收方如果之前隐藏了会话，收到新消息自动"取消隐藏"（微信/Telegram 直觉）
    peer_slot = _peer_slot(conv, me.id)
    if _hidden_at(conv, peer_slot) is not None:
        _set_hidden_at(conv, peer_slot, None)

    db.commit()
    db.refresh(msg)

    # SSE 广播：推给对方 + 推给自己（多标签同步）
    peer_id = _peer_id(conv, me.id)
    event = _serialize_event(
        {
            "type": "message.new",
            "conversation_id": conv.id,
            "message": _to_message_out(msg, me.username).model_dump(mode="json"),
        }
    )
    hub.publish(peer_id, event)
    hub.publish(me.id, event)

    return msg


def list_messages(
    db: Session,
    me: User,
    conv: Conversation,
    before_id: int | None = None,
    limit: int = MESSAGE_PAGE_LIMIT,
) -> list[DirectMessageOut]:
    """游标分页拉历史消息；返回按 id 正序（前端直接渲染）。

    调用方需先经 resolve_membership 校验成员身份。
    """
    limit = max(1, min(limit, MESSAGE_PAGE_MAX))
    stmt: Select[tuple[DirectMessage]] = select(DirectMessage).where(
        DirectMessage.conversation_id == conv.id
    )
    if before_id is not None:
        stmt = stmt.where(DirectMessage.id < before_id)
    stmt = stmt.order_by(DirectMessage.id.desc()).limit(limit)
    rows = list(db.scalars(stmt))
    rows.reverse()  # 反转为正序

    peer = _peer_user(db, conv, me.id)
    names = {me.id: me.username, peer.id: peer.username}
    return [_to_message_out(m, names.get(m.sender_id, "")) for m in rows]


def mark_read(db: Session, me: User, conv: Conversation, message_id: int | None = None) -> int | None:
    """标记会话已读；返回本次更新到的 message_id（None 表示无需更新）。

    - message_id 为空时读到会话最新消息。
    - 单调递增：不会把 last_read 往回退。
    - 更新后广播 message.read 事件给对方（用于将来的已读回执 UI）。
    """
    slot = _my_slot(conv, me.id)
    if message_id is None:
        latest = db.scalar(
            select(func.max(DirectMessage.id)).where(DirectMessage.conversation_id == conv.id)
        )
        message_id = int(latest) if latest is not None else None

    if message_id is None:
        return None

    current = _last_read_column(conv, slot)
    if current is not None and current >= message_id:
        return None

    _set_last_read(conv, slot, message_id)
    db.commit()

    peer_id = _peer_id(conv, me.id)
    hub.publish(
        peer_id,
        _serialize_event(
            {
                "type": "message.read",
                "conversation_id": conv.id,
                "reader_id": me.id,
                "message_id": message_id,
            }
        ),
    )
    return message_id


def hide_conversation(db: Session, me: User, conv: Conversation) -> None:
    """软删除会话（仅对自己隐藏）；对方不受影响。

    同时广播 conversation.hidden 事件给自己的其他标签页做同步。
    """
    slot = _my_slot(conv, me.id)
    _set_hidden_at(conv, slot, _utcnow())
    db.commit()
    hub.publish(
        me.id,
        _serialize_event({"type": "conversation.hidden", "conversation_id": conv.id}),
    )


# ---------------------------------------------------------------------------
# 会话列表与未读计数
# ---------------------------------------------------------------------------


def _unread_count_for(db: Session, conv: Conversation, me_id: int) -> int:
    """我在该会话的未读数 = 对方发的且 id > 我的 last_read 的消息数。"""
    slot = _my_slot(conv, me_id)
    last_read = _last_read_column(conv, slot)
    peer_id = _peer_id(conv, me_id)
    stmt = select(func.count()).select_from(DirectMessage).where(
        DirectMessage.conversation_id == conv.id,
        DirectMessage.sender_id == peer_id,
    )
    if last_read is not None:
        stmt = stmt.where(DirectMessage.id > last_read)
    return int(db.scalar(stmt) or 0)


def _my_conversations_stmt(me_id: int, include_hidden: bool = False) -> Select[tuple[Conversation]]:
    """构造"我参与的所有会话"的查询；默认排除我已隐藏的。"""
    cond = or_(Conversation.user_a_id == me_id, Conversation.user_b_id == me_id)
    stmt = select(Conversation).where(cond)
    if not include_hidden:
        # (我是 a 且 hidden_at_a IS NULL) OR (我是 b 且 hidden_at_b IS NULL)
        stmt = stmt.where(
            or_(
                (Conversation.user_a_id == me_id) & (Conversation.hidden_at_a.is_(None)),
                (Conversation.user_b_id == me_id) & (Conversation.hidden_at_b.is_(None)),
            )
        )
    return stmt


def _latest_message_subquery():
    """子查询：每个会话的最新消息 id。"""
    return (
        select(
            DirectMessage.conversation_id.label("conv_id"),
            func.max(DirectMessage.id).label("max_id"),
        )
        .group_by(DirectMessage.conversation_id)
        .subquery()
    )


def list_conversations(db: Session, me: User, limit: int = CONVERSATION_LIST_LIMIT) -> list[ConversationOut]:
    """会话列表，按 last_message_at DESC 排序（无消息的会话按 created_at 兜底）。"""
    limit = max(1, min(limit, CONVERSATION_LIST_LIMIT))
    latest_sq = _latest_message_subquery()
    stmt = (
        _my_conversations_stmt(me.id)
        .outerjoin(latest_sq, latest_sq.c.conv_id == Conversation.id)
        .outerjoin(DirectMessage, DirectMessage.id == latest_sq.c.max_id)
        .order_by(
            # NULL 排最后：Coalesce(last_message.created_at, conversation.created_at) DESC
            func.coalesce(DirectMessage.created_at, Conversation.created_at).desc(),
            Conversation.id.desc(),
        )
        .limit(limit)
    )
    convs = list(db.scalars(stmt))
    if not convs:
        return []

    # 批量取对方 User + 最新消息
    peer_ids = {_peer_id(c, me.id) for c in convs}
    peers = {u.id: u for u in db.scalars(select(User).where(User.id.in_(peer_ids)))}

    latest_ids = [
        db.scalar(
            select(func.max(DirectMessage.id)).where(DirectMessage.conversation_id == c.id)
        )
        for c in convs
    ]
    latest_msgs: dict[int, DirectMessage] = {}
    valid_ids = [i for i in latest_ids if i is not None]
    if valid_ids:
        for m in db.scalars(select(DirectMessage).where(DirectMessage.id.in_(valid_ids))):
            latest_msgs[m.id] = m

    sender_ids = {m.sender_id for m in latest_msgs.values()}
    names = _username_map(db, sender_ids)

    result: list[ConversationOut] = []
    for c, latest_id in zip(convs, latest_ids, strict=True):
        peer = peers.get(_peer_id(c, me.id))
        if peer is None:
            # 对方账号被删除；FK cascade 应该已经清理会话，跳过以防脏数据
            continue
        last_msg = latest_msgs.get(latest_id) if latest_id is not None else None
        last_msg_out = (
            _to_message_out(last_msg, names.get(last_msg.sender_id, "")) if last_msg else None
        )
        result.append(
            ConversationOut(
                id=c.id,
                peer=_to_peer_out(peer),
                last_message=last_msg_out,
                last_message_at=last_msg.created_at if last_msg else None,
                unread_count=_unread_count_for(db, c, me.id),
                created_at=c.created_at,
            )
        )
    return result


def unread_total(db: Session, me: User) -> int:
    """侧边栏徽标用的全局未读数（跨所有未隐藏会话求和）。"""
    convs = list(db.scalars(_my_conversations_stmt(me.id)))
    if not convs:
        return 0
    return sum(_unread_count_for(db, c, me.id) for c in convs)


def build_conversation_out(db: Session, me: User, conv: Conversation) -> ConversationOut:
    """把单个 Conversation ORM 对象包装成 ConversationOut（发起会话返回值用）。"""
    peer = _peer_user(db, conv, me.id)
    latest_id = db.scalar(
        select(func.max(DirectMessage.id)).where(DirectMessage.conversation_id == conv.id)
    )
    last_msg: DirectMessage | None = None
    if latest_id is not None:
        last_msg = db.get(DirectMessage, int(latest_id))
    last_msg_out = None
    if last_msg is not None:
        sender_name = (
            me.username if last_msg.sender_id == me.id else peer.username
        )
        last_msg_out = _to_message_out(last_msg, sender_name)
    return ConversationOut(
        id=conv.id,
        peer=_to_peer_out(peer),
        last_message=last_msg_out,
        last_message_at=last_msg.created_at if last_msg else None,
        unread_count=_unread_count_for(db, conv, me.id),
        created_at=conv.created_at,
    )
