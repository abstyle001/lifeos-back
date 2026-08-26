from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import DailyRecord, User
from ..schemas import ChatIn, ChatOut, WeeklyReportOut
from ..security import get_current_user
from ..services.ai import (
    build_chat_context,
    build_weekly_stats,
    chat as ai_chat,
    generate_report,
)

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/weekly-report", response_model=WeeklyReportOut)
def weekly_report(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> WeeklyReportOut:
    records = list(
        db.scalars(
            select(DailyRecord)
            .where(DailyRecord.user_id == current.id)
            .order_by(DailyRecord.date)
        )
    )
    today = date.today()
    week_start, week_end = today - timedelta(days=6), today
    stats = build_weekly_stats(records, today, current.level, current.experience)
    content, source = generate_report(stats, week_start, week_end)
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
    records = list(
        db.scalars(
            select(DailyRecord)
            .where(DailyRecord.user_id == current.id)
            .order_by(DailyRecord.date)
        )
    )
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
