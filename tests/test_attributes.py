from datetime import date

from back.models import DailyRecord, SocialInteraction
from back.services.attributes import compute_attributes, today_score


def _rec(**kw):
    defaults = dict(
        date=date(2026, 8, 1),
        sleep=7,
        study_time=2,
        exercise=0.5,
        mood=7,
        focus=7,
        reading_count=1,
        skill_time=1,
        diet=7,
        stress=4,
        energy=7,
        tasks_completed=3,
        tasks_total=5,
    )
    defaults.update(kw)
    return DailyRecord(**defaults)


def test_empty_returns_baseline():
    assert compute_attributes([]) == {"INT": 30, "VIT": 30, "FOCUS": 30, "CHA": 30}


def test_more_study_raises_int():
    low = [_rec(study_time=0.5, skill_time=0, reading_count=0) for _ in range(5)]
    high = [_rec(study_time=4, skill_time=3, reading_count=3) for _ in range(5)]
    assert compute_attributes(high)["INT"] > compute_attributes(low)["INT"]


def test_more_sleep_exercise_raises_vit():
    low = [_rec(sleep=5, exercise=0, diet=3) for _ in range(5)]
    high = [_rec(sleep=8, exercise=2, diet=9) for _ in range(5)]
    assert compute_attributes(high)["VIT"] > compute_attributes(low)["VIT"]


def test_today_score_range():
    assert 0 <= today_score(_rec()) <= 100
    assert today_score(_rec(focus=0, mood=0, energy=0, stress=10)) < 50


def test_cha_uses_social_when_present():
    records = [_rec() for _ in range(3)]
    empty_social = [
        SocialInteraction(
            user_id=1, date=date(2026, 8, 1), interactions=0, social_time=0, quality=0
        )
    ]
    # 有社交记录时 CHA 走社交公式（基线 30），而非情绪/精力/压力代理（更高）
    assert compute_attributes(records, empty_social)["CHA"] < compute_attributes(records)["CHA"]
