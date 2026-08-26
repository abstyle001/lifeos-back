from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
from pydantic import ValidationError

from ..config import Settings, get_settings
from ..models import DailyRecord
from ..schemas import (
    AiReportContent,
    Attributes,
    ChatMessageIn,
    MetricStat,
    ReportItem,
    WeeklyStats,
)
from .attributes import compute_attributes
from .experience import calc_streak

# 指标清单：决定 WeeklyStats.metrics 的顺序（含中文标签与单位）
METRICS: list[tuple[str, str, str]] = [
    ("study_time", "学习时长", "小时"),
    ("sleep", "睡眠时长", "小时"),
    ("exercise", "运动时长", "小时"),
    ("reading_count", "阅读数量", "本"),
    ("skill_time", "技能练习", "小时"),
    ("focus", "专注度", "分"),
    ("mood", "心情", "分"),
    ("diet", "饮食质量", "分"),
    ("stress", "压力水平", "分"),
    ("energy", "精力水平", "分"),
    ("tasks_completed", "完成任务", "个"),
]

# 越高越好的指标（stress 为反向，单独处理）
POSITIVE = {
    "study_time",
    "sleep",
    "exercise",
    "reading_count",
    "skill_time",
    "focus",
    "mood",
    "diet",
    "energy",
    "tasks_completed",
}

SYSTEM_PROMPT = """你是一名专业、客观、务实的个人成长教练，擅长根据用户的生活数据给出具体、可执行的建议，语气友好、鼓励但不空洞。

你将收到用户最近两周的生活数据统计，包含：本周日均、上周日均、变化幅度、当前 RPG 属性、连续打卡天数、等级与经验。

你必须只输出一个 JSON 对象，不要输出任何多余文字、解释、前后缀或 Markdown 代码块。JSON 结构必须严格如下：

{
  "summary": "本周总结（2-3 句话，引用具体数字，客观友好）",
  "highlights": [{"title": "亮点标题", "detail": "亮点说明（1-2 句）"}],
  "concerns": [{"title": "问题标题", "detail": "问题说明（1-2 句，引用数据）"}],
  "suggestions": [{"title": "建议标题", "detail": "具体可执行建议（1-2 句）"}],
  "next_goal": "下周一个具体、可衡量的目标（一句话）"
}

规则：
1. highlights 1-3 条；concerns 1-3 条；suggestions 2-4 条。
2. 所有文字使用简体中文。
3. 引用的数字必须来自输入数据，禁止编造。
4. 建议必须具体可执行（如每天多少分钟、每周几次），避免"多运动""早点睡"这类空泛表述。
5. 压力（stress）越高越差，其余指标越高越好，判断变化方向时注意。
6. 若本周没有任何记录（days_recorded 为 0），summary 写"本周还没有记录"，highlights 与 concerns 返回空数组，suggestions 给出如何开始记录的建议。"""


CHAT_SYSTEM_PROMPT = """你是 LifeOS 的个人 AI 教练，友好、务实、鼓励但不空洞。你用简体中文与用户交流，基于用户的生活数据给出具体、可执行的建议。

以下是用户当前的数据概况，请据此个性化回答，引用这些真实数字，不要编造数据：

{context}

规则：
1. 回答简洁自然，一般 2-5 句话；用户要求展开时再详细说明。
2. 建议要具体可执行（给出数字、频次、行动），避免"多运动""早点睡"这类空泛表述。
3. 若用户问到数据里没有的信息，诚实说明你没有相关数据。
4. 保持鼓励、不评判的语气。"""


def build_weekly_stats(
    records: list[DailyRecord], today: date, level: int, experience: int
) -> WeeklyStats:
    """由全部记录计算本周/上周统计（纯函数，无 IO）。"""
    current_recs = [r for r in records if today - timedelta(days=6) <= r.date <= today]
    previous_recs = [
        r
        for r in records
        if today - timedelta(days=13) <= r.date <= today - timedelta(days=7)
    ]

    def avg(recs: list[DailyRecord], key: str) -> float:
        return round(sum(getattr(r, key) for r in recs) / len(recs), 2) if recs else 0.0

    metrics = []
    for key, label, unit in METRICS:
        cur = avg(current_recs, key)
        prev = avg(previous_recs, key)
        delta = round(cur - prev, 2)
        delta_pct = round(delta / prev * 100, 1) if prev else 0.0
        metrics.append(
            MetricStat(
                key=key,
                label=label,
                unit=unit,
                current=cur,
                previous=prev,
                delta=delta,
                delta_pct=delta_pct,
            )
        )

    attrs = compute_attributes(records)
    return WeeklyStats(
        days_recorded=len(current_recs),
        previous_days_recorded=len(previous_recs),
        total_days=len(records),
        streak=calc_streak(records, today),
        level=level,
        experience=experience,
        attributes=Attributes(**attrs),
        metrics=metrics,
    )


def build_prompt(stats: WeeklyStats, week_start: date, week_end: date) -> tuple[str, str]:
    payload = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "stats": stats.model_dump(),
    }
    user = (
        "以下是用户最近两周的数据统计（JSON）：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n请根据以上数据生成周报。"
    )
    return SYSTEM_PROMPT, user


def build_chat_context(stats: WeeklyStats) -> str:
    """把用户数据概况压缩成一段可注入聊天系统提示词的文本。"""
    attr = stats.attributes
    lines = [
        f"RPG 属性：INT {attr.INT} / VIT {attr.VIT} / FOCUS {attr.FOCUS} / CHA {attr.CHA}",
        (
            f"等级 LV.{stats.level}，经验 {stats.experience}，连续打卡 {stats.streak} 天，"
            f"累计记录 {stats.total_days} 天，本周记录 {stats.days_recorded} 天"
        ),
    ]
    metric_parts = []
    for m in stats.metrics:
        if m.previous:
            metric_parts.append(
                f"{m.label} {m.current}{m.unit}（上周 {m.previous}{m.unit}，环比 {m.delta_pct:+.1f}%）"
            )
        else:
            metric_parts.append(f"{m.label} {m.current}{m.unit}")
    lines.append("近 7 天日均：" + "、".join(metric_parts))
    return "\n".join(lines)


def _parse_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.removeprefix("```").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _completion_request(
    settings: Settings,
    messages: list[dict],
    temperature: float,
    response_format: dict | None = None,
) -> tuple[str, dict, dict]:
    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.ai_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_api_key}"
    body: dict = {
        "model": settings.ai_model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        body["response_format"] = response_format
    return url, headers, body


def _post(url: str, headers: dict, body: dict, timeout: float) -> str | None:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None


def _call_ai(user_prompt: str, settings: Settings) -> dict | None:
    url, headers, body = _completion_request(
        settings,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    content = _post(url, headers, body, settings.ai_timeout_seconds)
    if content is None:
        return None
    return _parse_json_object(content)


def chat(
    messages: list[ChatMessageIn], context: str, settings: Settings
) -> str | None:
    system = CHAT_SYSTEM_PROMPT.format(context=context)
    url, headers, body = _completion_request(
        settings,
        [{"role": "system", "content": system}]
        + [{"role": m.role, "content": m.content} for m in messages],
        temperature=0.7,
    )
    content = _post(url, headers, body, settings.ai_timeout_seconds)
    return content.strip() if content else None


def _build_fallback(stats: WeeklyStats) -> AiReportContent:
    if stats.total_days == 0:
        return AiReportContent(
            summary="本周还没有记录任何数据，欢迎开启你的成长之旅。",
            suggestions=[
                ReportItem(
                    title="开始记录",
                    detail="每天花 1 分钟记录睡眠、学习和运动，坚持 7 天即可解锁周报。",
                )
            ],
            next_goal="连续记录 7 天",
        )

    pos = [m for m in stats.metrics if m.key in POSITIVE]
    improved = max(pos, key=lambda m: m.delta_pct, default=None)
    declined = min(pos, key=lambda m: m.delta_pct, default=None)

    highlights: list[ReportItem] = []
    concerns: list[ReportItem] = []
    suggestions: list[ReportItem] = []

    if improved and improved.delta_pct > 0:
        highlights.append(
            ReportItem(
                title=f"{improved.label}提升",
                detail=(
                    f"日均{improved.label}从 {improved.previous}{improved.unit} "
                    f"提升到 {improved.current}{improved.unit}（+{improved.delta_pct}%）。"
                ),
            )
        )
    if declined and declined.delta_pct < 0:
        concerns.append(
            ReportItem(
                title=f"{declined.label}下降",
                detail=(
                    f"日均{declined.label}从 {declined.previous}{declined.unit} "
                    f"回落到 {declined.current}{declined.unit}（{declined.delta_pct}%）。"
                ),
            )
        )
        suggestions.append(
            ReportItem(
                title=f"改善{declined.label}",
                detail=f"优先恢复{declined.label}，可从每天小幅增加开始，逐步回到上周水平。",
            )
        )
    if not suggestions:
        suggestions.append(
            ReportItem(title="保持节奏", detail="各项指标与上周基本持平，继续保持当前节奏。")
        )

    parts = [f"本周共记录 {stats.days_recorded} 天"]
    if improved and improved.delta_pct > 0:
        parts.append(f"{improved.label}进步最明显")
    if declined and declined.delta_pct < 0:
        parts.append(f"{declined.label}略有回落")
    summary = "，".join(parts) + "。"

    if declined and declined.delta_pct < 0:
        next_goal = f"下周把{declined.label}恢复到上周水平"
    elif improved:
        next_goal = f"下周继续稳定{improved.label}的上升势头"
    else:
        next_goal = "保持当前节奏，坚持每日记录"

    return AiReportContent(
        summary=summary,
        highlights=highlights,
        concerns=concerns,
        suggestions=suggestions,
        next_goal=next_goal,
    )


def generate_report(
    stats: WeeklyStats,
    week_start: date,
    week_end: date,
    settings: Settings | None = None,
) -> tuple[AiReportContent, str]:
    if settings is None:
        settings = get_settings()
    if stats.total_days == 0:
        return _build_fallback(stats), "fallback"
    if not (settings.ai_base_url and settings.ai_model):
        return _build_fallback(stats), "fallback"
    _, user_prompt = build_prompt(stats, week_start, week_end)
    parsed = _call_ai(user_prompt, settings)
    if parsed is None:
        return _build_fallback(stats), "fallback"
    try:
        return AiReportContent.model_validate(parsed), "ai"
    except ValidationError:
        return _build_fallback(stats), "fallback"
