from __future__ import annotations

import math
from datetime import date, timedelta

from ..models import DailyRecord


def _task_xp(completed: int, total: int) -> int:
    """按任务完成率计分（0..20），修复「完成数」而非「完成率」计分的问题。"""
    if total <= 0:
        return 0
    return round(20 * (completed / total))


def record_xp(record: DailyRecord) -> int:
    """单条记录贡献的经验值（初始公式，后续可调）。"""
    sleep_bonus = 10 if 7 <= record.sleep <= 9 else 5
    xp = (
        record.study_time * 10
        + record.skill_time * 8
        + record.reading_count * 5
        + record.exercise * 6
        + sleep_bonus
        + _task_xp(record.tasks_completed, record.tasks_total)
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
