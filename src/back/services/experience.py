from __future__ import annotations

import math
from datetime import date, timedelta

from ..models import DailyRecord


def record_xp(record: DailyRecord) -> int:
    """单条记录贡献的经验值（初始公式，后续可调）。"""
    sleep_bonus = 10 if 7 <= record.sleep <= 9 else 5
    xp = (
        record.study_time * 10
        + record.skill_time * 8
        + record.reading_count * 5
        + record.exercise * 6
        + sleep_bonus
        + record.tasks_completed * 5
        + record.mood * 2
    )
    return round(xp)


def level_for_xp(experience: int) -> int:
    """经验值 -> 等级曲线（初始公式）。"""
    return 1 + math.floor(math.sqrt(experience / 100))


def calc_streak(records: list[DailyRecord], today: date) -> int:
    """连续打卡天数：从今天（或昨天）往前数连续有记录的天数。"""
    recorded = {r.date for r in records}
    streak = 0
    cursor = today
    if cursor not in recorded:
        cursor -= timedelta(days=1)
    while cursor in recorded:
        streak += 1
        cursor -= timedelta(days=1)
    return streak
