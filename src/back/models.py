from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    avatar: Mapped[str | None] = mapped_column(String(255), default=None)
    level: Mapped[int] = mapped_column(Integer, default=1)
    experience: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    records: Mapped[list[DailyRecord]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    achievements: Mapped[list[Achievement]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    ai_report_caches: Mapped[list[AiReportCache]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    social_interactions: Mapped[list[SocialInteraction]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class DailyRecord(Base):
    __tablename__ = "daily_records"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_record_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    sleep: Mapped[float] = mapped_column(Float, default=0)  # 小时
    study_time: Mapped[float] = mapped_column(Float, default=0)  # 小时
    exercise: Mapped[float] = mapped_column(Float, default=0)  # 小时
    mood: Mapped[int] = mapped_column(Integer, default=0)  # 1-10
    focus: Mapped[int] = mapped_column(Integer, default=0)  # 1-10
    reading_count: Mapped[int] = mapped_column(Integer, default=0)  # 本
    skill_time: Mapped[float] = mapped_column(Float, default=0)  # 小时
    diet: Mapped[int] = mapped_column(Integer, default=0)  # 1-10
    stress: Mapped[int] = mapped_column(Integer, default=0)  # 1-10
    energy: Mapped[int] = mapped_column(Integer, default=0)  # 1-10
    tasks_completed: Mapped[int] = mapped_column(Integer, default=0)
    tasks_total: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="records")


class Achievement(Base):
    __tablename__ = "achievements"
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_achievement_user_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(255))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="achievements")


class AiReportCache(Base):
    """AI 周报缓存：同用户同一天（week_end）只生成一次，节省模型额度。"""

    __tablename__ = "ai_report_cache"
    __table_args__ = (UniqueConstraint("user_id", "week_end", name="uq_ai_report_user_week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text)
    highlights: Mapped[str] = mapped_column(Text)  # JSON 数组
    concerns: Mapped[str] = mapped_column(Text)  # JSON 数组
    suggestions: Mapped[str] = mapped_column(Text)  # JSON 数组
    next_goal: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="ai_report_caches")


class SocialInteraction(Base):
    """每日社交互动记录，作为 CHA 属性的真实数据来源（替换情绪/精力/压力代理）。"""

    __tablename__ = "social_interactions"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_social_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    interactions: Mapped[int] = mapped_column(Integer, default=0)  # 有意义互动次数
    social_time: Mapped[float] = mapped_column(Float, default=0)  # 社交时长（小时）
    quality: Mapped[int] = mapped_column(Integer, default=0)  # 社交质量 0-10
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="social_interactions")


class ChatMessage(Base):
    """AI 教练对话历史（用户消息 + 助手回复），跨页面持久化。"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="chat_messages")
