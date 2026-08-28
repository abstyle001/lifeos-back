from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Annotated, Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal, get_db
from ..models import ChatMessage, DailyRecord, SocialInteraction, User
from ..schemas import (
    ChatIn,
    ChatMessageIn,
    ChatMessageOut,
    ChatOut,
    MonthlyReportOut,
    MonthlyStatsOut,
    WeeklyReportOut,
    WeeklyStatsOut,
)
from ..security import get_current_user
from ..services.ai import (
    build_chat_context,
    build_monthly_stats,
    build_weekly_stats,
    chat as ai_chat,
    chat_stream as ai_chat_stream,
    generate_monthly_report,
    generate_report,
    get_cached_report,
    upsert_report_cache,
)

router = APIRouter(prefix="/ai", tags=["ai"])


def _load_records(db: Session, user_id: int) -> list[DailyRecord]:
    return list(
        db.scalars(
            select(DailyRecord)
            .where(DailyRecord.user_id == user_id)
            .order_by(DailyRecord.date)
        )
    )


def _load_social(db: Session, user_id: int) -> list[SocialInteraction]:
    return list(
        db.scalars(
            select(SocialInteraction)
            .where(SocialInteraction.user_id == user_id)
            .order_by(SocialInteraction.date)
        )
    )


def _load_chat_history(db: Session, user_id: int) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.id)
        )
    )


def _chat_context(db: Session, current: User) -> str:
    records = _load_records(db, current.id)
    social = _load_social(db, current.id)
    stats = build_weekly_stats(
        records, date.today(), current.level, current.experience, social
    )
    return build_chat_context(stats)


@router.get("/weekly-stats", response_model=WeeklyStatsOut)
def weekly_stats(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> WeeklyStatsOut:
    """确定性统计（不调 LLM，快速返回），供前端先渲染框架。"""
    records = _load_records(db, current.id)
    social = _load_social(db, current.id)
    today = date.today()
    week_start, week_end = today - timedelta(days=6), today
    stats = build_weekly_stats(records, today, current.level, current.experience, social)
    return WeeklyStatsOut(week_start=week_start, week_end=week_end, stats=stats)


@router.get("/weekly-report", response_model=WeeklyReportOut)
def weekly_report(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    refresh: bool = False,
) -> WeeklyReportOut:
    """AI 周报。默认读取当天缓存；refresh=true 时强制重新生成并更新缓存。"""
    records = _load_records(db, current.id)
    social = _load_social(db, current.id)
    today = date.today()
    week_start, week_end = today - timedelta(days=6), today
    stats = build_weekly_stats(records, today, current.level, current.experience, social)

    if not refresh:
        cached = get_cached_report(db, current.id, week_end)
        if cached is not None:
            content, source, created_at = cached
            return WeeklyReportOut(
                generated_at=created_at,
                week_start=week_start,
                week_end=week_end,
                stats=stats,
                summary=content.summary,
                highlights=content.highlights,
                concerns=content.concerns,
                suggestions=content.suggestions,
                next_goal=content.next_goal,
                source=source,
            )

    content, source = generate_report(stats, week_start, week_end)
    if source == "ai":
        upsert_report_cache(db, current.id, week_start, week_end, content, source)
        db.commit()

    return WeeklyReportOut(
        generated_at=datetime.now(),
        week_start=week_start,
        week_end=week_end,
        stats=stats,
        summary=content.summary,
        highlights=content.highlights,
        concerns=content.concerns,
        suggestions=content.suggestions,
        next_goal=content.next_goal,
        source=source,
    )


@router.get("/monthly-stats", response_model=MonthlyStatsOut)
def monthly_stats(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> MonthlyStatsOut:
    """月度确定性统计（本月 vs 上月同期，不调 LLM）。"""
    records = _load_records(db, current.id)
    social = _load_social(db, current.id)
    today = date.today()
    month_start, month_end = today.replace(day=1), today
    stats = build_monthly_stats(
        records, today, current.level, current.experience, social
    )
    return MonthlyStatsOut(month_start=month_start, month_end=month_end, stats=stats)


@router.get("/monthly-report", response_model=MonthlyReportOut)
def monthly_report(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> MonthlyReportOut:
    """AI 月报（AI 生成或内置规则降级）。"""
    records = _load_records(db, current.id)
    social = _load_social(db, current.id)
    today = date.today()
    month_start, month_end = today.replace(day=1), today
    stats = build_monthly_stats(
        records, today, current.level, current.experience, social
    )
    content, source = generate_monthly_report(stats, month_start, month_end)
    return MonthlyReportOut(
        generated_at=datetime.now(),
        month_start=month_start,
        month_end=month_end,
        stats=stats,
        summary=content.summary,
        highlights=content.highlights,
        concerns=content.concerns,
        suggestions=content.suggestions,
        next_goal=content.next_goal,
        source=source,
    )


@router.get("/chat/messages", response_model=list[ChatMessageOut])
def chat_history(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> list[ChatMessage]:
    """返回当前用户的对话历史（按时间正序）。"""
    return _load_chat_history(db, current.id)


@router.post("/chat", response_model=ChatOut)
def chat(
    payload: ChatIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> ChatOut:
    settings = get_settings()
    if not (settings.ai_base_url and settings.ai_model):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI 服务未配置，请在 back/.env 中设置 AI_BASE_URL 与 AI_MODEL。",
        )
    context = _chat_context(db, current)
    history = _load_chat_history(db, current.id)
    messages = [ChatMessageIn(role=m.role, content=m.content) for m in history] + [
        ChatMessageIn(role="user", content=payload.message)
    ]
    reply = ai_chat(messages, context, settings)
    if reply is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI 服务调用失败，请稍后重试。",
        )
    db.add(ChatMessage(user_id=current.id, role="user", content=payload.message))
    db.add(ChatMessage(user_id=current.id, role="assistant", content=reply))
    db.commit()
    return ChatOut(reply=reply)


@router.post("/chat/stream")
def chat_stream(
    payload: ChatIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    settings = get_settings()
    if not (settings.ai_base_url and settings.ai_model):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI 服务未配置，请在 back/.env 中设置 AI_BASE_URL 与 AI_MODEL。",
        )
    context = _chat_context(db, current)
    history = _load_chat_history(db, current.id)
    user_content = payload.message
    user_id = current.id
    messages = [ChatMessageIn(role=m.role, content=m.content) for m in history] + [
        ChatMessageIn(role="user", content=user_content)
    ]

    def event(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    def generate() -> Iterator[str]:
        buffer: list[str] = []
        try:
            for token in ai_chat_stream(messages, context, settings):
                buffer.append(token)
                yield event({"delta": token})
        except Exception:
            yield event({"error": "AI 服务调用失败，请稍后重试。"})
            return
        reply = "".join(buffer).strip()
        if not reply:
            yield event({"error": "AI 服务调用失败，请稍后重试。"})
            return
        with SessionLocal() as s:
            s.add(ChatMessage(user_id=user_id, role="user", content=user_content))
            s.add(ChatMessage(user_id=user_id, role="assistant", content=reply))
            s.commit()
        yield event({"done": True})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
