from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- Auth ---
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    avatar: str | None
    level: int
    experience: int


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Records ---
class RecordIn(BaseModel):
    date: date
    sleep: float = Field(ge=0, le=24)
    study_time: float = Field(ge=0, le=24)
    exercise: float = Field(ge=0, le=24)
    mood: int = Field(ge=0, le=10)
    focus: int = Field(ge=0, le=10)
    reading_count: int = Field(ge=0)
    skill_time: float = Field(ge=0, le=24)
    diet: int = Field(ge=0, le=10)
    stress: int = Field(ge=0, le=10)
    energy: int = Field(ge=0, le=10)
    tasks_completed: int = Field(ge=0)
    tasks_total: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_task_progress(self) -> Self:
        if self.tasks_completed > self.tasks_total:
            raise ValueError("已完成任务数不能大于总任务数")
        return self


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    sleep: float
    study_time: float
    exercise: float
    mood: int
    focus: int
    reading_count: int
    skill_time: float
    diet: int
    stress: int
    energy: int
    tasks_completed: int
    tasks_total: int
    note: str | None


# --- Attributes / dashboard ---
class Attributes(BaseModel):
    INT: int
    VIT: int
    FOCUS: int
    CHA: int


class TodayStatus(BaseModel):
    score: int
    tasks_completed: int
    tasks_total: int


class TrendPoint(BaseModel):
    date: date
    study_time: float
    sleep: float
    exercise: float


class DashboardOut(BaseModel):
    user: UserOut
    attributes: Attributes
    today: TodayStatus | None
    streak: int
    total_days: int
    total_study_hours: float
    total_exercise_hours: float
    books_read: int
    recent_records: list[RecordOut]
    trend: list[TrendPoint]


# --- Achievements ---
class AchievementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    description: str
    unlocked_at: datetime | None = None


class AchievementsOut(BaseModel):
    unlocked: list[AchievementOut]
    locked: list[AchievementOut]
