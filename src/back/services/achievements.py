from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Achievement, DailyRecord, SocialInteraction, User
from .attributes import compute_attributes
from .experience import calc_streak


@dataclass(frozen=True)
class AchievementDef:
    code: str
    title: str
    description: str
    requirement: str
    target: float
    measure: Callable[[User, list[DailyRecord], list[SocialInteraction]], float]


def _streak(user: User, records: list[DailyRecord], social: list[SocialInteraction]) -> float:
    return float(calc_streak(records, date.today()))


def _streak_perfect(
    user: User, records: list[DailyRecord], social: list[SocialInteraction]
) -> float:
    """连续「任务全部完成」的天数。"""
    perfect = {
        r.date
        for r in records
        if r.tasks_total > 0 and r.tasks_completed >= r.tasks_total
    }
    today = date.today()
    cursor = today if today in perfect else today - timedelta(days=1)
    streak = 0
    while cursor in perfect:
        streak += 1
        cursor -= timedelta(days=1)
    return float(streak)


def _task_completion_rate(
    user: User, records: list[DailyRecord], social: list[SocialInteraction]
) -> float:
    days = [r for r in records if r.tasks_total > 0]
    if not days:
        return 0.0
    return sum(r.tasks_completed / r.tasks_total for r in days) / len(days)


def _social_total(
    user: User, records: list[DailyRecord], social: list[SocialInteraction]
) -> float:
    return float(sum(s.interactions for s in social))


def _level(user: User, records: list[DailyRecord], social: list[SocialInteraction]) -> float:
    return float(user.level)


def _xp(user: User, records: list[DailyRecord], social: list[SocialInteraction]) -> float:
    return float(user.experience)


def _attr_max(
    user: User, records: list[DailyRecord], social: list[SocialInteraction]
) -> float:
    if not records:
        return 30.0
    return float(max(compute_attributes(records, social).values()))


def _attr_balanced(
    user: User, records: list[DailyRecord], social: list[SocialInteraction]
) -> float:
    if not records:
        return 0.0
    return float(min(compute_attributes(records, social).values()))


def _sum(attr: str) -> Callable[[User, list[DailyRecord], list[SocialInteraction]], float]:
    def measure(
        user: User, records: list[DailyRecord], social: list[SocialInteraction]
    ) -> float:
        return float(sum(getattr(r, attr) for r in records))

    return measure


DEFINITIONS: list[AchievementDef] = [
    AchievementDef(
        "first_record", "初来乍到", "完成第一条每日记录", "记录 1 天", 1,
        lambda u, r, s: float(len(r)),
    ),
    AchievementDef("streak_7", "坚持不懈", "连续打卡 7 天", "连续打卡 7 天", 7, _streak),
    AchievementDef("streak_30", "铁人", "连续打卡 30 天", "连续打卡 30 天", 30, _streak),
    AchievementDef("bookworm", "读书破万卷", "累计阅读 10 本书", "累计阅读 10 本", 10, _sum("reading_count")),
    AchievementDef("athlete", "运动达人", "累计运动 50 小时", "累计运动 50 小时", 50, _sum("exercise")),
    AchievementDef("scholar", "学霸", "累计学习 100 小时", "累计学习 100 小时", 100, _sum("study_time")),
    AchievementDef("level_5", "初出茅庐", "达到等级 5", "达到等级 5", 5, _level),
    AchievementDef("level_10", "登峰造极", "达到等级 10", "达到等级 10", 10, _level),
    AchievementDef("xp_1000", "经验大师", "累计获得 1000 经验", "累计 1000 经验", 1000, _xp),
    AchievementDef("task_master", "效率之王", "日均任务完成率 80%", "日均任务完成率 80%", 0.8, _task_completion_rate),
    AchievementDef("perfect_week", "完美一周", "连续 7 天完成全部任务", "连续 7 天任务全勤", 7, _streak_perfect),
    AchievementDef("social_butterfly", "社交达人", "累计 30 次社交互动", "累计 30 次互动", 30, _social_total),
    AchievementDef("attr_70", "属性觉醒", "任一属性达到 70", "任一属性达到 70", 70, _attr_max),
    AchievementDef("balanced", "全面发展", "四项属性均达到 50", "四项属性 ≥ 50", 50, _attr_balanced),
]

_BY_CODE: dict[str, AchievementDef] = {d.code: d for d in DEFINITIONS}


def _clamp_progress(measure: float, target: float) -> float:
    if target <= 0:
        return 0.0
    return max(0.0, min(1.0, measure / target))


def check_and_unlock(
    db: Session, user: User, social: list[SocialInteraction] | None = None
) -> list[Achievement]:
    """根据用户全部记录判定成就，落库并返回本次新解锁的成就。

    幂等：直接从数据库读取已解锁的 code，同会话内多次调用也不会重复落库。
    """
    records = db.query(DailyRecord).filter(DailyRecord.user_id == user.id).all()
    if social is None:
        social = (
            db.query(SocialInteraction).filter(SocialInteraction.user_id == user.id).all()
        )
    earned = {d.code for d in DEFINITIONS if d.measure(user, records, social) >= d.target}
    existing = set(
        db.scalars(select(Achievement.code).where(Achievement.user_id == user.id))
    )
    new = [code for code in earned if code not in existing]

    created: list[Achievement] = []
    for code in new:
        d = _BY_CODE[code]
        ach = Achievement(user_id=user.id, code=d.code, title=d.title, description=d.description)
        db.add(ach)
        created.append(ach)
    if created:
        db.flush()
    return created


def progress_map(db: Session, user: User) -> dict[str, float]:
    """返回所有成就 code -> 完成度（0..1）。"""
    records = db.query(DailyRecord).filter(DailyRecord.user_id == user.id).all()
    social = db.query(SocialInteraction).filter(SocialInteraction.user_id == user.id).all()
    return {
        d.code: _clamp_progress(d.measure(user, records, social), d.target)
        for d in DEFINITIONS
    }
