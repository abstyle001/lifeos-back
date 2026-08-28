from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import SocialInteraction, User
from ..schemas import SocialIn, SocialOut
from ..security import get_current_user

router = APIRouter(prefix="/social", tags=["social"])


@router.post("", response_model=SocialOut)
def upsert_social(
    payload: SocialIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> SocialInteraction:
    """按日期 upsert 社交记录（同一天覆盖）。"""
    existing = db.scalar(
        select(SocialInteraction).where(
            SocialInteraction.user_id == current.id,
            SocialInteraction.date == payload.date,
        )
    )
    if existing is None:
        existing = SocialInteraction(user_id=current.id, **payload.model_dump())
        db.add(existing)
    else:
        for key, value in payload.model_dump().items():
            setattr(existing, key, value)
    db.commit()
    db.refresh(existing)
    return existing


@router.get("", response_model=list[SocialOut])
def list_social(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> list[SocialInteraction]:
    stmt = (
        select(SocialInteraction)
        .where(SocialInteraction.user_id == current.id)
        .order_by(SocialInteraction.date)
    )
    if from_date:
        stmt = stmt.where(SocialInteraction.date >= from_date)
    if to_date:
        stmt = stmt.where(SocialInteraction.date <= to_date)
    return list(db.scalars(stmt))


@router.delete("/{social_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_social(
    social_id: int,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Response:
    row = db.scalar(
        select(SocialInteraction).where(
            SocialInteraction.id == social_id,
            SocialInteraction.user_id == current.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="社交记录不存在")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
