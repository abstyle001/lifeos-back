from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import AchievementOut, AchievementsOut
from ..security import get_current_user
from ..services.achievements import DEFINITIONS

router = APIRouter(prefix="/achievements", tags=["achievements"])


@router.get("", response_model=AchievementsOut)
def achievements(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> AchievementsOut:
    unlocked = [AchievementOut.model_validate(a) for a in current.achievements]
    unlocked_codes = {a.code for a in unlocked}
    locked = [
        AchievementOut(code=code, title=title, description=desc, unlocked_at=None)
        for code, (title, desc) in DEFINITIONS.items()
        if code not in unlocked_codes
    ]
    return AchievementsOut(unlocked=unlocked, locked=locked)
