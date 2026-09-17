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
    reading = [r.reading_time for r in recent]
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
            quality = [s.quality for s in recent_social]
            CHA = _clamp(30 + _avg(interactions) * 8 + _avg(quality) * 4)
        else:
            CHA = _clamp(30 + _avg(mood) * 8 + _avg(energy) * 6 - _avg(stress) * 2)
    else:
        CHA = _clamp(30 + _avg(mood) * 8 + _avg(energy) * 6 - _avg(stress) * 2)

    return {"INT": INT, "VIT": VIT, "FOCUS": FOCUS, "CHA": CHA}


_FACTOR_LABELS: dict[str, tuple[str, str]] = {
    "study_time": ("学习时长", "小时"),
    "skill_time": ("技能练习", "小时"),
    "reading_time": ("阅读时长", "小时"),
    "sleep": ("睡眠", "小时"),
    "exercise": ("运动", "小时"),
    "diet": ("饮食", "分"),
    "focus": ("专注", "分"),
    "stress": ("压力", "分"),
    "mood": ("心情", "分"),
    "energy": ("精力", "分"),
    "interactions": ("社交互动", "次"),
    "quality": ("社交质量", "分"),
}


def _factor(
    key: str,
    avg: float | None,
    weight: float | None,
    contribution: float,
    *,
    kind: str = "linear",
    detail: str | None = None,
    cap: float | None = None,
) -> dict:
    label, unit = _FACTOR_LABELS[key]
    if detail is None and kind == "linear" and avg is not None and weight is not None:
        detail = f"{label} {avg:.1f} {unit} × {weight:g}"
        if cap is not None:
            detail += f"（超过 {cap:g} 按 {cap:g} 截断）"
        elif weight < 0:
            detail += "（负向影响）"
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "avg": round(avg, 2) if avg is not None else None,
        "weight": weight,
        "contribution": round(contribution, 1),
        "kind": kind,
        "detail": detail,
        "cap": cap,
    }


def explain_attributes(
    records: list[DailyRecord],
    social_records: list[SocialInteraction] | None = None,
) -> dict:
    """生成属性可解释数据：每个属性的基线、因子贡献与最终值。

    公式与 compute_attributes 完全同步（修改需两处同改，见上方 compute_attributes）。
    最终 value 直接复用 compute_attributes 结果，保证与仪表盘展示一致。
    返回结构对应 schemas.AttributesExplainOut（attributes 为 list，按 INT/VIT/FOCUS/CHA 顺序）。
    """
    recent = records[-14:] if len(records) > 14 else records
    base = 30.0
    window_days = 14

    def linear(key: str, values: list[float], weight: float, cap: float | None = None) -> dict:
        capped = [min(v, cap) for v in values] if cap is not None else values
        return _factor(key, _avg(capped), weight, _avg(capped) * weight, cap=cap)

    if not recent:
        empty = [
            {
                "key": key,
                "label": label,
                "zh": zh,
                "value": base,
                "base": base,
                "factors": [],
                "source": "formula",
                "note": "暂无记录，属性使用基线值 30。记录数据后即可计算。",
            }
            for key, label, zh in (
                ("INT", "INT", "智力"),
                ("VIT", "VIT", "体力"),
                ("FOCUS", "FOCUS", "专注"),
                ("CHA", "CHA", "社交"),
            )
        ]
        return {
            "attributes": empty,
            "window_days": window_days,
            "record_count": 0,
            "has_social": bool(social_records),
        }

    study = [r.study_time for r in recent]
    skill = [r.skill_time for r in recent]
    reading = [r.reading_time for r in recent]
    sleep = [min(r.sleep, 10) for r in recent]
    exercise = [r.exercise for r in recent]
    diet = [r.diet for r in recent]
    focus = [r.focus for r in recent]
    stress = [r.stress for r in recent]
    mood = [r.mood for r in recent]
    energy = [r.energy for r in recent]

    def build(
        key: str, zh: str, factors: list[dict], note: str | None = None, source: str = "formula"
    ) -> dict:
        return {
            "key": key,
            "label": key,
            "zh": zh,
            "value": compute_attributes(records, social_records)[key],
            "base": base,
            "factors": factors,
            "source": source,
            "note": note,
        }

    int_factors = [
        linear("study_time", study, 6),
        linear("skill_time", skill, 5),
        linear("reading_time", reading, 4),
    ]
    vit_factors = [
        linear("sleep", sleep, 6, cap=10),
        linear("exercise", exercise, 5),
        linear("diet", diet, 4),
    ]
    focus_factors = [
        linear("focus", focus, 8),
        linear("stress", stress, -2),
        _factor(
            "study_time",
            None,
            None,
            5.0 if _avg(study) >= 2 else 0.0,
            kind="bonus",
            detail="日均学习 ≥ 2 小时触发 +5",
        ),
    ]

    # CHA：有真实社交记录时由社交数据计算，否则退化为情绪/精力/压力代理
    cha_source = "formula"
    cha_note = None
    recent_social: list[SocialInteraction] = []
    if social_records:
        recent_social = (
            social_records[-14:] if len(social_records) > 14 else social_records
        )
    if recent_social:
        interactions = [s.interactions for s in recent_social]
        quality = [s.quality for s in recent_social]
        cha_factors = [linear("interactions", interactions, 8), linear("quality", quality, 4)]
        cha_source = "social"
        cha_note = "基于最近 14 天的社交记录（互动频率 × 8 + 社交质量 × 4）计算。"
    else:
        cha_factors = [
            linear("mood", mood, 8),
            linear("energy", energy, 6),
            linear("stress", stress, -2),
        ]
        cha_source = "proxy"
        cha_note = "暂无社交记录，使用心情 / 精力 / 压力作为社交能力代理。记录社交互动后可切换为真实社交数据。"

    attributes = [
        build("INT", "智力", int_factors, "学习时长 ×6 + 技能练习 ×5 + 阅读时长 ×4，再加 30 基线。"),
        build("VIT", "体力", vit_factors, "睡眠 ×6 + 运动 ×5 + 饮食 ×4，再加 30 基线。"),
        build("FOCUS", "专注", focus_factors, "专注 ×8 - 压力 ×2，日均学习 ≥2 小时额外 +5，再加 30 基线。"),
        build("CHA", "社交", cha_factors, cha_note, source=cha_source),
    ]

    return {
        "attributes": attributes,
        "window_days": window_days,
        "record_count": len(recent),
        "has_social": bool(recent_social),
    }


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
