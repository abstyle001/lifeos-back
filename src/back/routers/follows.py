from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import FollowActionOut, FollowRelationOut, FollowUserOut
from ..security import get_current_user
from ..services.follows import (
    SelfFollowError,
    TargetNotFound,
    follow_user,
    get_relation,
    list_related,
    unfollow_user,
)

router = APIRouter(prefix="/follows", tags=["follows"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在或不可见"
    )


@router.post("/{username}", response_model=FollowActionOut)
def follow(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> FollowActionOut:
    try:
        return follow_user(db, current, username)
    except TargetNotFound as exc:
        raise _not_found() from exc
    except SelfFollowError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能关注自己"
        ) from exc


@router.delete("/{username}", response_model=FollowActionOut)
def unfollow(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> FollowActionOut:
    try:
        return unfollow_user(db, current, username)
    except TargetNotFound as exc:
        raise _not_found() from exc


@router.get("/{username}/relation", response_model=FollowRelationOut)
def relation(
    username: str,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> FollowRelationOut:
    try:
        return get_relation(db, current, username)
    except TargetNotFound as exc:
        raise _not_found() from exc


@router.get("/{username}/{kind}", response_model=list[FollowUserOut])
def related_list(
    username: str,
    kind: Literal["following", "followers"],
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> list[FollowUserOut]:
    try:
        return list_related(db, current, username, kind)
    except TargetNotFound as exc:
        raise _not_found() from exc
