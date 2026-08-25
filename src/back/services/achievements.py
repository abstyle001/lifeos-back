from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Achievement, DailyRecord, User
from .experience import calc_streak

# 成就定义：code -> (title, description)
DEFINITIONS: dict[str, tuple[str, str]] = {
    "first_record": ("初来乍到", "完成第一条每日记录"),
    "streak_7": ("坚持不懈", "连续打卡 7 天"),
    "bookworm": ("读书破万卷", "累计阅读 10 本书"),
    "athlete": ("运动达人", "累计运动 50 小时"),
}


def _check(user: User, records: list[DailyRecord]) -> set[str]:
    unlocked: set[str] = set()
    if records:
        unlocked.add("first_record")
    if calc_streak(records, date.today()) >= 7:
        unlocked.add("streak_7")
    if sum(r.reading_count for r in records) >= 10:
        unlocked.add("bookworm")
    if sum(r.exercise for r in records) >= 50:
        unlocked.add("athlete")
    return unlocked


def check_and_unlock(db: Session, user: User) -> list[Achievement]:
    """根据用户全部记录判定成就，落库并返回本次新解锁的成就。

    幂等：直接从数据库读取已解锁的 code，同会话内多次调用也不会重复落库。
    """
    records = (
        db.query(DailyRecord).filter(DailyRecord.user_id == user.id).all()
    )
    earned = _check(user, records)
    existing = set(
        db.scalars(select(Achievement.code).where(Achievement.user_id == user.id))
    )
    new = [code for code in earned if code not in existing]

    created: list[Achievement] = []
    for code in new:
        title, desc = DEFINITIONS[code]
        ach = Achievement(user_id=user.id, code=code, title=title, description=desc)
        db.add(ach)
        created.append(ach)
    if created:
        db.flush()
    return created
