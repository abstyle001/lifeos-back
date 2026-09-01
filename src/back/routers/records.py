from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DailyRecord, User
from ..schemas import AchievementOut, RecordIn, RecordOut, RecordSaveOut
from ..security import get_current_user
from ..services.achievements import check_and_unlock
from ..services.experience import level_for_xp, record_xp

router = APIRouter(prefix="/records", tags=["records"])


def _recalc_xp(db: Session, user: User) -> None:
    """按用户全部记录重算经验与等级，避免编辑记录造成经验漂移。"""
    records = db.scalars(
        select(DailyRecord).where(DailyRecord.user_id == user.id)
    ).all()
    user.experience = sum(record_xp(r) for r in records)
    user.level = level_for_xp(user.experience)


@router.post("", response_model=RecordSaveOut)
def upsert_record(
    payload: RecordIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> RecordSaveOut:
    record = db.scalar(
        select(DailyRecord).where(
            DailyRecord.user_id == current.id, DailyRecord.date == payload.date
        )
    )
    if record is None:
        record = DailyRecord(user_id=current.id, **payload.model_dump())
        db.add(record)
    else:
        for key, value in payload.model_dump().items():
            setattr(record, key, value)
    db.flush()
    _recalc_xp(db, current)
    db.commit()
    db.refresh(record)
    new_achievements = check_and_unlock(db, current)
    db.commit()
    return RecordSaveOut(
        **RecordOut.model_validate(record).model_dump(),
        new_achievements=[AchievementOut.model_validate(a) for a in new_achievements],
    )


@router.get("", response_model=list[RecordOut])
def list_records(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> list[DailyRecord]:
    stmt = (
        select(DailyRecord)
        .where(DailyRecord.user_id == current.id)
        .order_by(DailyRecord.date)
    )
    if from_date:
        stmt = stmt.where(DailyRecord.date >= from_date)
    if to_date:
        stmt = stmt.where(DailyRecord.date <= to_date)
    return list(db.scalars(stmt))


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_record(
    record_id: int,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Response:
    record = db.scalar(
        select(DailyRecord).where(
            DailyRecord.id == record_id, DailyRecord.user_id == current.id
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记录不存在")

    db.delete(record)
    db.flush()
    _recalc_xp(db, current)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
