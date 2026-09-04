from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import (
    ProfileSearchResult,
    ProfileSettingsOut,
    ProfileSettingsUpdate,
    PublicProfileOut,
)
from ..security import get_current_user
from ..services.public_profiles import (
    build_public_profile,
    get_profile_settings,
    search_public_profiles,
    set_profile_visibility,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("/search", response_model=list[ProfileSearchResult])
def search_profiles(
    # The service validates the trimmed value so surrounding whitespace does
    # not incorrectly consume the username length budget.
    q: Annotated[str, Query(min_length=1)],
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[ProfileSearchResult]:
    try:
        users = search_public_profiles(db, q)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return [
        ProfileSearchResult(
            username=user.username,
            avatar=user.avatar,
            level=user.level,
            experience=user.experience,
        )
        for user in users
    ]


@router.get("/me/settings", response_model=ProfileSettingsOut)
def read_my_profile_settings(
    current: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProfileSettingsOut:
    return get_profile_settings(db, current.id)


@router.patch("/me/settings", response_model=ProfileSettingsOut)
def update_my_profile_settings(
    payload: ProfileSettingsUpdate,
    current: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProfileSettingsOut:
    return set_profile_visibility(db, current.id, payload.is_public)


@router.get("/{username}", response_model=PublicProfileOut)
def read_public_profile(
    username: str,
    current: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PublicProfileOut:
    target = db.scalar(select(User).where(User.username == username))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在或不可见")
    profile = build_public_profile(db, target, current)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在或不可见")
    return profile
