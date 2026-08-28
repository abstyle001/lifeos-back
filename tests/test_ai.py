from datetime import date, timedelta

import httpx

from back.config import Settings
from back.models import DailyRecord
from back.schemas import AiReportContent, Attributes, ChatMessageIn, MetricStat, WeeklyStats
from back.services.ai import (
    _call_ai,
    _parse_json_object,
    build_chat_context,
    build_weekly_stats,
    chat,
    generate_report,
)


def _auth_headers(client, username="bob"):
    client.post(
        "/api/auth/register", json={"username": username, "password": "secret123"}
    )
    token = (
        client.post(
            "/api/auth/login", json={"username": username, "password": "secret123"}
        )
        .json()["access_token"]
    )
    return {"Authorization": f"Bearer {token}"}


def _record_payload(day, **kw):
    payload = dict(
        date=day.isoformat(),
        sleep=7.5,
        study_time=3,
        exercise=1,
        mood=8,
        focus=8,
        reading_count=1,
        skill_time=1.5,
        diet=7,
        stress=3,
        energy=8,
        tasks_completed=4,
        tasks_total=5,
    )
    payload.update(kw)
    return payload


def _rec(day, **kw):
    defaults = dict(
        user_id=1,
        sleep=7.5,
        study_time=3,
        exercise=1,
        mood=8,
        focus=8,
        reading_count=1,
        skill_time=1.5,
        diet=7,
        stress=3,
        energy=8,
        tasks_completed=4,
        tasks_total=5,
    )
    defaults.update(kw)
    return DailyRecord(date=day, **defaults)


def _stats():
    return WeeklyStats(
        days_recorded=1,
        previous_days_recorded=0,
        total_days=1,
        streak=1,
        level=1,
        experience=10,
        attributes=Attributes(INT=30, VIT=30, FOCUS=30, CHA=30),
        metrics=[
            MetricStat(
                key="study_time",
                label="学习时长",
                unit="小时",
                current=3.0,
                previous=0.0,
                delta=3.0,
                delta_pct=0.0,
            )
        ],
    )


def test_weekly_report_requires_auth(client):
    r = client.get("/api/ai/weekly-report")
    assert r.status_code == 401


def test_weekly_report_fallback_when_unconfigured(client):
    h = _auth_headers(client)
    today = date.today()
    client.post("/api/records", headers=h, json=_record_payload(today))

    r = client.get("/api/ai/weekly-report", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback"
    assert body["summary"]
    assert body["week_start"] == (today - timedelta(days=6)).isoformat()
    assert body["week_end"] == today.isoformat()
    assert body["stats"]["days_recorded"] == 1
    assert set(body["stats"]["attributes"].keys()) == {"INT", "VIT", "FOCUS", "CHA"}


def test_build_weekly_stats_deltas():
    today = date.today()
    records = [
        _rec(today - timedelta(days=i), study_time=3) for i in range(7)
    ] + [
        _rec(today - timedelta(days=7 + i), study_time=2) for i in range(7)
    ]
    stats = build_weekly_stats(records, today, level=1, experience=100)

    study = next(m for m in stats.metrics if m.key == "study_time")
    assert study.current == 3.0
    assert study.previous == 2.0
    assert study.delta == 1.0
    assert study.delta_pct == 50.0
    assert stats.days_recorded == 7
    assert stats.previous_days_recorded == 7


def test_build_weekly_stats_sparse_data():
    today = date.today()
    records = [_rec(today - timedelta(days=i), study_time=3) for i in range(3)]
    stats = build_weekly_stats(records, today, level=1, experience=10)

    assert stats.days_recorded == 3
    assert stats.previous_days_recorded == 0
    study = next(m for m in stats.metrics if m.key == "study_time")
    assert study.current == 3.0
    assert study.previous == 0.0
    assert study.delta_pct == 0.0


def test_parse_json_object():
    assert _parse_json_object('{"a": 1}') == {"a": 1}
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert _parse_json_object("not json at all") is None
    assert _parse_json_object("[1, 2, 3]") is None
    assert _parse_json_object("") is None


def test_generate_report_ai_success(monkeypatch):
    monkeypatch.setattr(
        "back.services.ai._call_ai",
        lambda prompt, settings: {
            "summary": "本周表现不错",
            "highlights": [{"title": "学习提升", "detail": "学习时长增加"}],
            "concerns": [],
            "suggestions": [{"title": "保持", "detail": "继续"}],
            "next_goal": "每天学习 3 小时",
        },
    )
    settings = Settings(ai_base_url="https://example.com/v1", ai_model="m")
    content, source = generate_report(_stats(), date.today(), date.today(), settings)
    assert source == "ai"
    assert content.summary == "本周表现不错"


def test_generate_report_fallback_on_http_error(monkeypatch):
    monkeypatch.setattr("back.services.ai._call_ai", lambda prompt, settings: None)
    settings = Settings(ai_base_url="https://example.com/v1", ai_model="m")
    content, source = generate_report(_stats(), date.today(), date.today(), settings)
    assert source == "fallback"
    assert content.summary


def test_generate_report_fallback_on_bad_shape(monkeypatch):
    monkeypatch.setattr(
        "back.services.ai._call_ai",
        lambda prompt, settings: {"summary": 123, "highlights": "not-a-list"},
    )
    settings = Settings(ai_base_url="https://example.com/v1", ai_model="m")
    content, source = generate_report(_stats(), date.today(), date.today(), settings)
    assert source == "fallback"


def test_call_ai_request_shape(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"summary": "ok"}'}}]}

    class FakeClient:
        def __init__(self, **kw):
            captured["timeout"] = kw.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("back.services.ai.httpx.Client", FakeClient)
    settings = Settings(
        ai_base_url="https://example.com/v1",
        ai_model="m",
        ai_api_key="k",
        ai_timeout_seconds=20.0,
    )
    result = _call_ai("prompt", settings)

    assert result == {"summary": "ok"}
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["json"]["model"] == "m"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer k"
    assert captured["timeout"] == 20.0


def test_call_ai_timeout_returns_none(monkeypatch):
    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kw):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("back.services.ai.httpx.Client", FakeClient)
    settings = Settings(ai_base_url="https://example.com/v1", ai_model="m")
    assert _call_ai("prompt", settings) is None


def test_chat_requires_auth(client):
    r = client.post("/api/ai/chat", json={"message": "你好"})
    assert r.status_code == 401


def test_chat_unconfigured_returns_503(client):
    h = _auth_headers(client)
    r = client.post("/api/ai/chat", headers=h, json={"message": "你好"})
    assert r.status_code == 503


def test_chat_endpoint_success(monkeypatch, client):
    h = _auth_headers(client)
    client.post("/api/records", headers=h, json=_record_payload(date.today()))

    monkeypatch.setattr(
        "back.routers.ai.get_settings",
        lambda: Settings(ai_base_url="https://example.com/v1", ai_model="m"),
    )
    monkeypatch.setattr(
        "back.routers.ai.ai_chat",
        lambda messages, context, settings: "试试睡前 1 小时放下手机。",
    )

    r = client.post("/api/ai/chat", headers=h, json={"message": "睡眠不好怎么办"})
    assert r.status_code == 200
    assert r.json()["reply"] == "试试睡前 1 小时放下手机。"


def test_build_chat_context_contains_data():
    ctx = build_chat_context(_stats())
    assert "INT" in ctx
    assert "学习时长" in ctx
    assert "LV.1" in ctx


def test_chat_returns_text(monkeypatch):
    monkeypatch.setattr(
        "back.services.ai._post",
        lambda url, headers, body, timeout: "你好！",
    )
    settings = Settings(ai_base_url="https://example.com/v1", ai_model="m")
    messages = [ChatMessageIn(role="user", content="我最近睡眠不好怎么办？")]
    assert chat(messages, "数据上下文", settings) == "你好！"


def test_weekly_stats_requires_auth(client):
    r = client.get("/api/ai/weekly-stats")
    assert r.status_code == 401


def test_weekly_stats_endpoint(client):
    h = _auth_headers(client)
    today = date.today()
    client.post("/api/records", headers=h, json=_record_payload(today))

    r = client.get("/api/ai/weekly-stats", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["week_start"] == (today - timedelta(days=6)).isoformat()
    assert body["week_end"] == today.isoformat()
    assert body["stats"]["days_recorded"] == 1
    assert set(body["stats"]["attributes"].keys()) == {"INT", "VIT", "FOCUS", "CHA"}


def test_weekly_report_cached(monkeypatch, client):
    h = _auth_headers(client)
    client.post("/api/records", headers=h, json=_record_payload(date.today()))

    calls = {"n": 0}

    def fake_generate(stats, week_start, week_end, settings=None):
        calls["n"] += 1
        return (
            AiReportContent(
                summary="缓存测试",
                suggestions=[{"title": "建议", "detail": "详情"}],
                next_goal="目标",
            ),
            "ai",
        )

    monkeypatch.setattr("back.routers.ai.generate_report", fake_generate)

    r1 = client.get("/api/ai/weekly-report", headers=h)
    assert r1.status_code == 200
    assert r1.json()["source"] == "ai"
    assert r1.json()["summary"] == "缓存测试"
    assert calls["n"] == 1

    r2 = client.get("/api/ai/weekly-report", headers=h)
    assert r2.json()["summary"] == "缓存测试"
    assert calls["n"] == 1  # 命中缓存，未重新生成

    r3 = client.get("/api/ai/weekly-report?refresh=true", headers=h)
    assert r3.status_code == 200
    assert calls["n"] == 2  # 强制重新生成


def test_chat_persists_history(monkeypatch, client):
    h = _auth_headers(client)
    client.post("/api/records", headers=h, json=_record_payload(date.today()))

    monkeypatch.setattr(
        "back.routers.ai.get_settings",
        lambda: Settings(ai_base_url="https://example.com/v1", ai_model="m"),
    )
    monkeypatch.setattr(
        "back.routers.ai.ai_chat",
        lambda messages, context, settings: "试试睡前 1 小时放下手机。",
    )

    client.post("/api/ai/chat", headers=h, json={"message": "睡眠不好怎么办"})

    history = client.get("/api/ai/chat/messages", headers=h).json()
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "睡眠不好怎么办"}
    assert history[1] == {"role": "assistant", "content": "试试睡前 1 小时放下手机。"}


def test_monthly_stats_endpoint(client):
    h = _auth_headers(client)
    today = date.today()
    client.post("/api/records", headers=h, json=_record_payload(today))

    r = client.get("/api/ai/monthly-stats", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["month_start"] == today.replace(day=1).isoformat()
    assert body["month_end"] == today.isoformat()
    assert body["stats"]["days_recorded"] >= 1
    assert set(body["stats"]["attributes"].keys()) == {"INT", "VIT", "FOCUS", "CHA"}


def test_monthly_report_fallback(client):
    h = _auth_headers(client)
    today = date.today()
    client.post("/api/records", headers=h, json=_record_payload(today))

    r = client.get("/api/ai/monthly-report", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback"
    assert body["summary"]
    assert body["month_start"] == today.replace(day=1).isoformat()
