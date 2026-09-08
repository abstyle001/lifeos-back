"""用户私聊 REST + SSE 路由。

- REST 端点同步实现（FastAPI 自动跑在 threadpool）。
- SSE `/stream` 端点异步实现：订阅 chat_hub，每 25 秒发一个 keepalive 注释帧
  防止 CDN/Vercel 提前断流；客户端断开时在 finally 里注销订阅。
- 异常翻译集中在 `_translate_chat_error`，保持与 follows.py 一致的风格
  （私密 / 不存在统一 404，不泄露存在性）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import (
    ConversationCreate,
    ConversationOut,
    DirectMessageOut,
    MarkReadIn,
    SendMessageIn,
    UnreadCountOut,
)
from ..security import get_current_user
from ..services import chat as chat_service
from ..services.chat import (
    ChatError,
    ConversationNotFoundError,
    EmptyContentError,
    MutualUnfollowError,
    NeedFollowError,
    NotConversationMemberError,
    PeerNotVisibleError,
    RateLimitError,
    SelfChatError,
)
from ..services.chat_hub import QUEUE_MAX_SIZE, hub

router = APIRouter(prefix="/chat", tags=["chat"])

# SSE keepalive 间隔（秒）；小于 Vercel/CDN 常见的 30s 空闲断流阈值
_KEEPALIVE_SECONDS = 25.0


def _translate_chat_error(exc: ChatError) -> HTTPException:
    """把业务异常统一翻译成 HTTP 异常，与 follows.py 的中文 detail 风格一致。"""
    if isinstance(exc, PeerNotVisibleError) or isinstance(exc, ConversationNotFoundError):
        # 会话不存在与对方不可见用同一 detail，避免通过错误消息推断状态
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在或不可见"
        )
    if isinstance(exc, SelfChatError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能与自己聊天")
    if isinstance(exc, NeedFollowError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="需要先关注对方才能发起聊天"
        )
    if isinstance(exc, MutualUnfollowError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要至少一方关注另一方才能继续对话",
        )
    if isinstance(exc, NotConversationMemberError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
    if isinstance(exc, EmptyContentError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="消息内容不能为空")
    if isinstance(exc, RateLimitError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="发送过于频繁，请稍后再试"
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求无效")


# ---------------------------------------------------------------------------
# REST 端点
# ---------------------------------------------------------------------------


@router.post("/conversations", response_model=ConversationOut)
def create_conversation(
    payload: ConversationCreate,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> ConversationOut:
    """发起或获取与某人的会话（幂等）。"""
    try:
        conv = chat_service.get_or_create_conversation(db, current, payload.peer)
        return chat_service.build_conversation_out(db, current, conv)
    except ChatError as exc:
        raise _translate_chat_error(exc) from exc


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> list[ConversationOut]:
    """我的会话列表（按 last_message_at DESC，最多 50 条）。"""
    return chat_service.list_conversations(db, current, limit=limit)


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> UnreadCountOut:
    """侧边栏未读徽标初始化用的全局未读数（SSE 建连前先拉一次）。"""
    return UnreadCountOut(total=chat_service.unread_total(db, current))


@router.get(
    "/conversations/{conversation_id}/messages", response_model=list[DirectMessageOut]
)
def list_messages(
    conversation_id: int,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DirectMessageOut]:
    """消息历史（游标分页，返回按 id 正序）。非成员 404。"""
    try:
        conv = chat_service.resolve_membership(db, current, conversation_id)
        return chat_service.list_messages(db, current, conv, before_id=before_id, limit=limit)
    except ChatError as exc:
        raise _translate_chat_error(exc) from exc


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=DirectMessageOut,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    conversation_id: int,
    payload: SendMessageIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> DirectMessageOut:
    """发送消息；成功后通过 SSE 广播给对方（和自己的其他标签）。"""
    try:
        conv = chat_service.resolve_membership(db, current, conversation_id)
        msg = chat_service.send_message(
            db, current, conv, payload.content, payload.client_message_id
        )
        return chat_service._to_message_out(msg, current.username)
    except ChatError as exc:
        raise _translate_chat_error(exc) from exc


@router.post("/conversations/{conversation_id}/read", response_model=UnreadCountOut)
def mark_read(
    conversation_id: int,
    payload: MarkReadIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> UnreadCountOut:
    """标记会话已读到指定消息（默认读到最新）；返回更新后的全局未读数。"""
    try:
        conv = chat_service.resolve_membership(db, current, conversation_id)
        chat_service.mark_read(db, current, conv, payload.message_id)
        return UnreadCountOut(total=chat_service.unread_total(db, current))
    except ChatError as exc:
        raise _translate_chat_error(exc) from exc


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def hide_conversation(
    conversation_id: int,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> None:
    """软删除（隐藏）会话，仅对自己生效；对方不受影响。"""
    try:
        conv = chat_service.resolve_membership(db, current, conversation_id)
        chat_service.hide_conversation(db, current, conv)
    except ChatError as exc:
        raise _translate_chat_error(exc) from exc


# ---------------------------------------------------------------------------
# SSE 推送
# ---------------------------------------------------------------------------


def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/stream")
async def chat_stream(
    request: Request,
    current: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """私聊事件长连接。

    事件类型：
    - `{"type": "connected", "user_id": <int>}`：建连成功首帧
    - `{"type": "message.new", "conversation_id": <int>, "message": <DirectMessageOut>}`
    - `{"type": "message.read", "conversation_id": <int>, "reader_id": <int>, "message_id": <int>}`
    - `{"type": "conversation.hidden", "conversation_id": <int>}`

    每 25 秒发一个 SSE 注释帧（`: keepalive`）防止 CDN 空闲断流；客户端断开或
    Vercel 函数超时时，前端负责重连（指数退避，见 lib/api.ts streamSSE）。
    """
    user_id = current.id
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX_SIZE)
    loop = asyncio.get_running_loop()
    hub.register(user_id, queue, loop)

    async def event_gen() -> AsyncIterator[str]:
        try:
            yield _sse_frame({"type": "connected", "user_id": user_id})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                    yield _sse_frame(event)
                except asyncio.TimeoutError:
                    # SSE 注释帧，客户端解析器会忽略，仅用于保活
                    yield ": keepalive\n\n"
        finally:
            hub.unregister(user_id, queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
