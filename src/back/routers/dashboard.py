from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DailyRecord, SocialInteraction, User
from ..schemas import (
    Attributes,
    DashboardOut,
    RecordOut,
    TodayStatus,
    TrendPoint,
    UserOut,
)
from ..security import get_current_user
from ..services.attributes import compute_attributes, today_score
from ..services.experience import calc_streak

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def dashboard(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> DashboardOut:
    records = list(
        db.scalars(
            select(DailyRecord)
            .where(DailyRecord.user_id == current.id)
            .order_by(DailyRecord.date)
        )
    )
    today = date.today()
    today_record = next((r for r in records if r.date == today), None)

    social = list(
        db.scalars(
            select(SocialInteraction)
            .where(SocialInteraction.user_id == current.id)
            .order_by(SocialInteraction.date)
        )
    )
    attributes = compute_attributes(records, social)
    streak = calc_streak(records, today)
    trend = [
        TrendPoint(
            date=r.date,
            study_time=r.study_time,
            sleep=r.sleep,
            exercise=r.exercise,
            reading_time=r.reading_time,
            skill_time=r.skill_time,
            mood=r.mood,
            focus=r.focus,
            diet=r.diet,
            stress=r.stress,
            energy=r.energy,
            tasks_completed=r.tasks_completed,
            tasks_total=r.tasks_total,
        )
        for r in records[-30:]
    ]
    recent = list(reversed(records[-7:]))

    return DashboardOut(
        user=UserOut.model_validate(current),
        attributes=Attributes(**attributes),
        today=(
            TodayStatus(
                score=today_score(today_record),
                tasks_completed=today_record.tasks_completed,
                tasks_total=today_record.tasks_total,
            )
            if today_record
            else None
        ),
        streak=streak,
        total_days=len(records),
        total_study_hours=round(sum(r.study_time for r in records), 1),
        total_exercise_hours=round(sum(r.exercise for r in records), 1),
        total_reading_hours=round(sum(r.reading_time for r in records), 1),
        recent_records=[RecordOut.model_validate(r) for r in recent],
        trend=trend,
    )
