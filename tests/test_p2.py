from datetime import date


def _auth_headers(client, username="p2user"):
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    token = (
        client.post("/api/auth/login", json={"username": username, "password": "secret123"})
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


def test_achievements_include_progress(client):
    h = _auth_headers(client)
    client.post("/api/records", headers=h, json=_record_payload(date.today()))
    r = client.get("/api/achievements", headers=h).json()

    unlocked = {a["code"]: a for a in r["unlocked"]}
    assert unlocked["first_record"]["progress"] == 1.0
    assert unlocked["first_record"]["requirement"]
    assert all(0 <= a["progress"] <= 1 for a in r["locked"])
    assert all(a["requirement"] for a in r["locked"])


def test_record_save_returns_new_achievements(client):
    h = _auth_headers(client)
    r = client.post("/api/records", headers=h, json=_record_payload(date.today()))
    assert r.status_code == 200
    body = r.json()
    assert any(a["code"] == "first_record" for a in body["new_achievements"])


def test_tasks_crud(client):
    h = _auth_headers(client)
    today = date.today()
    created = client.post(
        "/api/tasks", headers=h, json={"date": today.isoformat(), "title": "阅读", "done": False}
    )
    assert created.status_code == 201
    tid = created.json()["id"]

    assert len(client.get("/api/tasks", headers=h).json()) == 1
    assert client.patch(f"/api/tasks/{tid}", headers=h, json={"done": True}).json()["done"] is True
    assert client.delete(f"/api/tasks/{tid}", headers=h).status_code == 204
    assert client.get("/api/tasks", headers=h).json() == []


def test_goals_crud(client):
    h = _auth_headers(client)
    created = client.post("/api/goals", headers=h, json={"title": "早起", "done": False})
    assert created.status_code == 201
    gid = created.json()["id"]
    assert client.get("/api/goals", headers=h).json()[0]["title"] == "早起"
    assert client.patch(f"/api/goals/{gid}", headers=h, json={"done": True}).json()["done"] is True
    assert client.delete(f"/api/goals/{gid}", headers=h).status_code == 204


def test_export_roundtrip(client):
    h = _auth_headers(client)
    today = date.today()
    client.post("/api/records", headers=h, json=_record_payload(today))
    client.post(
        "/api/social",
        headers=h,
        json={"date": today.isoformat(), "interactions": 3, "social_time": 2.0, "quality": 7},
    )
    client.post("/api/goals", headers=h, json={"title": "早起", "done": False})

    exported = client.get("/api/export", headers=h).json()
    assert exported["user"]["username"] == "p2user"
    assert len(exported["records"]) == 1
    assert len(exported["social"]) == 1
    assert len(exported["goals"]) == 1

    h2 = _auth_headers(client, username="p2other")
    imported = client.post(
        "/api/import",
        headers=h2,
        json={
            "records": exported["records"],
            "social": exported["social"],
            "goals": exported["goals"],
            "tasks": [],
        },
    )
    assert imported.status_code == 200
    assert imported.json()["records"] == 1
    assert imported.json()["social"] == 1
    assert imported.json()["goals"] == 1


def test_username_update(client):
    h = _auth_headers(client, username="renameme")
    r = client.patch("/api/auth/me", headers=h, json={"username": "newname"})
    assert r.status_code == 200
    assert r.json()["username"] == "newname"
    # 改名后旧 token 仍有效（JWT 按 user id 签发）
    assert client.get("/api/auth/me", headers=h).json()["username"] == "newname"

    # 用户名已被占用 → 409
    _auth_headers(client, username="occupied")
    assert client.patch("/api/auth/me", headers=h, json={"username": "occupied"}).status_code == 409


def test_password_change(client):
    h = _auth_headers(client, username="pwduser")

    # 缺当前密码 → 422
    assert (
        client.patch("/api/auth/me", headers=h, json={"new_password": "newsecret1"}).status_code
        == 422
    )
    # 当前密码错误 → 400
    assert (
        client.patch(
            "/api/auth/me",
            headers=h,
            json={"old_password": "wrong", "new_password": "newsecret1"},
        ).status_code
        == 400
    )
    # 改密成功，新密码可登录、旧密码失效
    r = client.patch(
        "/api/auth/me",
        headers=h,
        json={"old_password": "secret123", "new_password": "newsecret1"},
    )
    assert r.status_code == 200
    assert (
        client.post(
            "/api/auth/login", json={"username": "pwduser", "password": "newsecret1"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": "pwduser", "password": "secret123"}
        ).status_code
        == 401
    )


def test_report_includes_prediction(client):
    h = _auth_headers(client)
    client.post("/api/records", headers=h, json=_record_payload(date.today()))
    weekly = client.get("/api/ai/weekly-report", headers=h).json()
    monthly = client.get("/api/ai/monthly-report", headers=h).json()
    assert "prediction" in weekly
    assert "prediction" in monthly
