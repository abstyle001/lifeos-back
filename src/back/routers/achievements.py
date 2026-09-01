from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import AchievementOut, AchievementsOut
from ..security import get_current_user
from ..services.achievements import DEFINITIONS, progress_map

router = APIRouter(prefix="/achievements", tags=["achievements"])


@router.get("", response_model=AchievementsOut)
def achievements(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> AchievementsOut:
    progress = progress_map(db, current)
    unlocked_by_code = {a.code: a for a in current.achievements}

    unlocked: list[AchievementOut] = []
    locked: list[AchievementOut] = []
    for d in DEFINITIONS:
        row = unlocked_by_code.get(d.code)
        if row is not None:
            unlocked.append(
                AchievementOut(
                    code=d.code,
                    title=d.title,
                    description=d.description,
                    unlocked_at=row.unlocked_at,
                    requirement=d.requirement,
                    progress=1.0,
                )
            )
        else:
            locked.append(
                AchievementOut(
                    code=d.code,
                    title=d.title,
                    description=d.description,
                    unlocked_at=None,
                    requirement=d.requirement,
                    progress=progress[d.code],
                )
            )
    return AchievementsOut(unlocked=unlocked, locked=locked)
