from datetime import date

from back.models import DailyRecord, SocialInteraction
from back.services.attributes import (
    compute_attributes,
    explain_attributes,
    today_score,
)


def _rec(**kw):
    defaults = dict(
        date=date(2026, 8, 1),
        sleep=7,
        study_time=2,
        exercise=0.5,
        mood=7,
        focus=7,
        reading_time=1,
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
    low = [_rec(study_time=0.5, skill_time=0, reading_time=0) for _ in range(5)]
    high = [_rec(study_time=4, skill_time=3, reading_time=3) for _ in range(5)]
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


# --- explain_attributes ---

def test_explain_empty_returns_baseline():
    exp = explain_attributes([])
    assert exp["record_count"] == 0
    assert exp["window_days"] == 14
    assert exp["has_social"] is False
    assert [a["key"] for a in exp["attributes"]] == ["INT", "VIT", "FOCUS", "CHA"]
    for attr in exp["attributes"]:
        assert attr["value"] == 30
        assert attr["base"] == 30
        assert attr["factors"] == []
        assert attr["note"]


def test_explain_values_match_compute():
    records = [_rec() for _ in range(5)]
    exp = explain_attributes(records)
    computed = compute_attributes(records)
    for attr in exp["attributes"]:
        assert attr["value"] == computed[attr["key"]]
        assert attr["base"] == 30


def test_explain_int_factors_contribution():
    # study=2 → 2×6=12, skill=1 → 1×5=5, reading=1 → 1×4=4，期望 value = 30+21 = 51
    records = [_rec(study_time=2, skill_time=1, reading_time=1) for _ in range(5)]
    exp = explain_attributes(records)
    int_attr = next(a for a in exp["attributes"] if a["key"] == "INT")
    assert int_attr["value"] == 51
    by_key = {f["key"]: f for f in int_attr["factors"]}
    assert by_key["study_time"]["avg"] == 2.0
    assert by_key["study_time"]["weight"] == 6
    assert by_key["study_time"]["contribution"] == 12.0
    assert by_key["skill_time"]["contribution"] == 5.0
    assert by_key["reading_time"]["contribution"] == 4.0
    total = sum(f["contribution"] for f in int_attr["factors"])
    assert int_attr["base"] + total == 51.0


def test_explain_focus_bonus_trigger():
    hit = explain_attributes([_rec(study_time=3) for _ in range(3)])
    miss = explain_attributes([_rec(study_time=0.5) for _ in range(3)])
    focus_hit = next(a for a in hit["attributes"] if a["key"] == "FOCUS")
    focus_miss = next(a for a in miss["attributes"] if a["key"] == "FOCUS")
    bonus_hit = next(f for f in focus_hit["factors"] if f["kind"] == "bonus")
    bonus_miss = next(f for f in focus_miss["factors"] if f["kind"] == "bonus")
    assert bonus_hit["contribution"] == 5.0
    assert bonus_miss["contribution"] == 0.0
    assert bonus_miss["detail"]  # 说明触发条件


def test_explain_cha_source_switches():
    records = [_rec() for _ in range(3)]
    # 无社交记录 → proxy（心情/精力/压力）
    proxy = explain_attributes(records)
    cha_proxy = next(a for a in proxy["attributes"] if a["key"] == "CHA")
    assert cha_proxy["source"] == "proxy"
    assert [f["key"] for f in cha_proxy["factors"]] == ["mood", "energy", "stress"]
    assert proxy["has_social"] is False

    social = [
        SocialInteraction(
            user_id=1, date=date(2026, 8, 1), interactions=4, social_time=0, quality=8
        )
        for _ in range(3)
    ]
    exp = explain_attributes(records, social)
    cha_social = next(a for a in exp["attributes"] if a["key"] == "CHA")
    assert cha_social["source"] == "social"
    assert [f["key"] for f in cha_social["factors"]] == ["interactions", "quality"]
    assert exp["has_social"] is True


def test_explain_sleep_capped_at_10():
    records = [_rec(sleep=12) for _ in range(3)]
    exp = explain_attributes(records)
    vit = next(a for a in exp["attributes"] if a["key"] == "VIT")
    sleep_factor = next(f for f in vit["factors"] if f["key"] == "sleep")
    assert sleep_factor["avg"] == 10.0
    assert sleep_factor["cap"] == 10.0
    assert sleep_factor["contribution"] == 60.0
