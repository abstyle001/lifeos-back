from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import DailyRecord, User
from ..schemas import ChatIn, ChatOut, WeeklyReportOut, WeeklyStatsOut
from ..security import get_current_user
from ..services.ai import (
    build_chat_context,
    build_weekly_stats,
    chat as ai_chat,
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


@router.get("/weekly-stats", response_model=WeeklyStatsOut)
def weekly_stats(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> WeeklyStatsOut:
    """确定性统计（不调 LLM，快速返回），供前端先渲染框架。"""
    records = _load_records(db, current.id)
    today = date.today()
    week_start, week_end = today - timedelta(days=6), today
    stats = build_weekly_stats(records, today, current.level, current.experience)
    return WeeklyStatsOut(week_start=week_start, week_end=week_end, stats=stats)


@router.get("/weekly-report", response_model=WeeklyReportOut)
def weekly_report(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    refresh: bool = False,
) -> WeeklyReportOut:
    """AI 周报。默认读取当天缓存；refresh=true 时强制重新生成并更新缓存。"""
    records = _load_records(db, current.id)
    today = date.today()
    week_start, week_end = today - timedelta(days=6), today
    stats = build_weekly_stats(records, today, current.level, current.experience)

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
    # 只缓存真实 AI 结果；fallback（未配置/失败）每次都现算，避免将来配置 AI 后被旧降级挡住
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
    records = _load_records(db, current.id)
    today = date.today()
    stats = build_weekly_stats(records, today, current.level, current.experience)
    context = build_chat_context(stats)
    reply = ai_chat(payload.messages, context, settings)
    if reply is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI 服务调用失败，请稍后重试。",
        )
    return ChatOut(reply=reply)
