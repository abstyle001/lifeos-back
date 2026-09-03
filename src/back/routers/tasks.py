from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Task, User
from ..schemas import TaskIn, TaskOut, TaskUpdate
from ..security import get_current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskIn,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Task:
    task = Task(user_id=current.id, **payload.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("", response_model=list[TaskOut])
def list_tasks(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> list[Task]:
    stmt = (
        select(Task).where(Task.user_id == current.id).order_by(Task.date, Task.id)
    )
    if from_date:
        stmt = stmt.where(Task.date >= from_date)
    if to_date:
        stmt = stmt.where(Task.date <= to_date)
    return list(db.scalars(stmt))


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == current.id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if payload.title is not None:
        task.title = payload.title
    if payload.done is not None:
        task.done = payload.done
    if payload.importance is not None:
        task.importance = payload.importance
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Response:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == current.id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
