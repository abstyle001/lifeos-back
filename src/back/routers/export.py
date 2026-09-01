from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Achievement, DailyRecord, Goal, SocialInteraction, Task, User
from ..schemas import (
    AchievementOut,
    ExportOut,
    GoalOut,
    ImportIn,
    RecordOut,
    SocialOut,
    TaskOut,
    UserOut,
)
from ..security import get_current_user
from ..services.achievements import check_and_unlock
from ..services.experience import level_for_xp, record_xp

router = APIRouter(tags=["data"])


@router.get("/export", response_model=ExportOut)
def export_data(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> ExportOut:
    records = list(
        db.scalars(
            select(DailyRecord)
            .where(DailyRecord.user_id == current.id)
            .order_by(DailyRecord.date)
        )
    )
    social = list(
        db.scalars(
            select(SocialInteraction)
            .where(SocialInteraction.user_id == current.id)
            .order_by(SocialInteraction.date)
        )
    )
    achievements = list(
        db.scalars(
            select(Achievement)
            .where(Achievement.user_id == current.id)
            .order_by(Achievement.id)
        )
    )
    goals = list(
        db.scalars(select(Goal).where(Goal.user_id == current.id).order_by(Goal.id))
    )
    tasks = list(
        db.scalars(
            select(Task)
            .where(Task.user_id == current.id)
            .order_by(Task.date, Task.id)
        )
    )
    return ExportOut(
        exported_at=datetime.now(),
        user=UserOut.model_validate(current),
        records=[RecordOut.model_validate(r) for r in records],
        social=[SocialOut.model_validate(s) for s in social],
        achievements=[AchievementOut.model_validate(a) for a in achievements],
        goals=[GoalOut.model_validate(g) for g in goals],
        tasks=[TaskOut.model_validate(t) for t in tasks],
    )


@router.post("/import")
def import_data(
    payload: ImportIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict[str, int]:
    imported = {"records": 0, "social": 0, "goals": 0, "tasks": 0}

    for r in payload.records:
        existing = db.scalar(
            select(DailyRecord).where(
                DailyRecord.user_id == current.id, DailyRecord.date == r.date
            )
        )
        if existing is None:
            db.add(DailyRecord(user_id=current.id, **r.model_dump()))
        else:
            for key, value in r.model_dump().items():
                setattr(existing, key, value)
        imported["records"] += 1

    for s in payload.social:
        existing = db.scalar(
            select(SocialInteraction).where(
                SocialInteraction.user_id == current.id,
                SocialInteraction.date == s.date,
            )
        )
        if existing is None:
            db.add(SocialInteraction(user_id=current.id, **s.model_dump()))
        else:
            for key, value in s.model_dump().items():
                setattr(existing, key, value)
        imported["social"] += 1

    existing_goal_titles = set(
        db.scalars(select(Goal.title).where(Goal.user_id == current.id))
    )
    for g in payload.goals:
        if g.title in existing_goal_titles:
            continue
        db.add(Goal(user_id=current.id, title=g.title, done=g.done))
        existing_goal_titles.add(g.title)
        imported["goals"] += 1

    existing_task_keys = {
        (t.date, t.title)
        for t in db.scalars(select(Task).where(Task.user_id == current.id))
    }
    for t in payload.tasks:
        key = (t.date, t.title)
        if key in existing_task_keys:
            continue
        db.add(Task(user_id=current.id, date=t.date, title=t.title, done=t.done))
        existing_task_keys.add(key)
        imported["tasks"] += 1

    records = list(
        db.scalars(select(DailyRecord).where(DailyRecord.user_id == current.id))
    )
    current.experience = sum(record_xp(r) for r in records)
    current.level = level_for_xp(current.experience)
    db.flush()
    check_and_unlock(db, current)
    db.commit()
    return imported
