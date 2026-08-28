from __future__ import annotations

from ..models import DailyRecord, SocialInteraction


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, round(value)))


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_attributes(
    records: list[DailyRecord],
    social_records: list[SocialInteraction] | None = None,
) -> dict[str, int]:
    """由最近记录聚合出 RPG 属性（0-100）。空记录返回基线值。

    初始公式，集中在此便于后续调参；前端不做任何硬编码。
    CHA 在有真实社交记录时由社交数据计算，否则退化为情绪/精力/压力代理。
    """
    recent = records[-14:] if len(records) > 14 else records
    if not recent:
        return {"INT": 30, "VIT": 30, "FOCUS": 30, "CHA": 30}

    study = [r.study_time for r in recent]
    skill = [r.skill_time for r in recent]
    reading = [r.reading_count for r in recent]
    sleep = [min(r.sleep, 10) for r in recent]
    exercise = [r.exercise for r in recent]
    diet = [r.diet for r in recent]
    focus = [r.focus for r in recent]
    stress = [r.stress for r in recent]
    mood = [r.mood for r in recent]
    energy = [r.energy for r in recent]

    INT = _clamp(30 + _avg(study) * 6 + _avg(skill) * 5 + _avg(reading) * 4)
    VIT = _clamp(30 + _avg(sleep) * 6 + _avg(exercise) * 5 + _avg(diet) * 4)
    FOCUS = _clamp(30 + _avg(focus) * 8 - _avg(stress) * 2 + (5 if _avg(study) >= 2 else 0))

    if social_records:
        recent_social = social_records[-14:] if len(social_records) > 14 else social_records
        if recent_social:
            interactions = [s.interactions for s in recent_social]
            social_time = [s.social_time for s in recent_social]
            quality = [s.quality for s in recent_social]
            CHA = _clamp(30 + _avg(interactions) * 6 + _avg(social_time) * 3 + _avg(quality) * 4)
        else:
            CHA = _clamp(30 + _avg(mood) * 8 + _avg(energy) * 6 - _avg(stress) * 2)
    else:
        CHA = _clamp(30 + _avg(mood) * 8 + _avg(energy) * 6 - _avg(stress) * 2)

    return {"INT": INT, "VIT": VIT, "FOCUS": FOCUS, "CHA": CHA}


def today_score(record: DailyRecord) -> int:
    """今日综合评分（0-100）。"""
    score = (
        record.focus * 3
        + record.mood * 3
        + record.energy * 2
        + min(record.study_time, 8) * 2.5
        + min(record.exercise, 3) * 4
        + min(record.sleep, 10) * 2
        + (5 if 7 <= record.sleep <= 9 else 0)
        - record.stress * 2
    )
    return _clamp(score)
