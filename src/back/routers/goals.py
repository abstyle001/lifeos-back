from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Goal, User
from ..schemas import GoalIn, GoalOut, GoalUpdate
from ..security import get_current_user

router = APIRouter(prefix="/goals", tags=["goals"])


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(
    payload: GoalIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Goal:
    goal = Goal(user_id=current.id, **payload.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


@router.get("", response_model=list[GoalOut])
def list_goals(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> list[Goal]:
    return list(
        db.scalars(select(Goal).where(Goal.user_id == current.id).order_by(Goal.id))
    )


@router.patch("/{goal_id}", response_model=GoalOut)
def update_goal(
    goal_id: int,
    payload: GoalUpdate,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Goal:
    goal = db.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == current.id))
    if goal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目标不存在")
    if payload.title is not None:
        goal.title = payload.title
    if payload.done is not None:
        goal.done = payload.done
    db.commit()
    db.refresh(goal)
    return goal


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(
    goal_id: int,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Response:
    goal = db.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == current.id))
    if goal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目标不存在")
    db.delete(goal)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
