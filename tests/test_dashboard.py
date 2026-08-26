from datetime import date, timedelta


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


def test_dashboard_flow(client):
    h = _auth_headers(client)
    today = date.today()

    r = client.post("/api/records", headers=h, json=_record_payload(today))
    assert r.status_code == 200

    d = client.get("/api/dashboard", headers=h).json()
    assert d["streak"] >= 1
    assert d["total_days"] == 1
    assert set(d["attributes"].keys()) == {"INT", "VIT", "FOCUS", "CHA"}
    assert all(0 <= v <= 100 for v in d["attributes"].values())
    assert d["today"]["score"] > 0

    ach = client.get("/api/achievements", headers=h).json()
    assert any(a["code"] == "first_record" for a in ach["unlocked"])


def test_streak_counts_consecutive_days(client):
    h = _auth_headers(client)
    today = date.today()
    for i in range(3):
        client.post(
            "/api/records", headers=h, json=_record_payload(today - timedelta(days=i))
        )
    d = client.get("/api/dashboard", headers=h).json()
    assert d["streak"] == 3


def test_record_upsert_does_not_duplicate(client):
    h = _auth_headers(client)
    today = date.today()
    client.post("/api/records", headers=h, json=_record_payload(today))
    client.post(
        "/api/records", headers=h, json=_record_payload(today, study_time=5)
    )
    records = client.get("/api/records", headers=h).json()
    assert len(records) == 1
    assert records[0]["study_time"] == 5

def test_delete_record_recalculates_experience(client):
    h = _auth_headers(client)
    today = date.today()
    created = client.post("/api/records", headers=h, json=_record_payload(today))
    record_id = created.json()["id"]

    before = client.get("/api/dashboard", headers=h).json()
    assert before["user"]["experience"] > 0

    deleted = client.delete(f"/api/records/{record_id}", headers=h)
    assert deleted.status_code == 204

    after = client.get("/api/dashboard", headers=h).json()
    assert after["total_days"] == 0
    assert after["user"]["experience"] == 0
    assert after["user"]["level"] == 1

    missing = client.delete(f"/api/records/{record_id}", headers=h)
    assert missing.status_code == 404


def test_cannot_delete_another_users_record(client):
    owner = _auth_headers(client, username="owner")
    created = client.post(
        "/api/records", headers=owner, json=_record_payload(date.today())
    )
    other = _auth_headers(client, username="other")

    deleted = client.delete(f"/api/records/{created.json()['id']}", headers=other)
    assert deleted.status_code == 404


def test_record_validates_task_progress_and_note_length(client):
    h = _auth_headers(client)
    today = date.today()

    invalid_tasks = client.post(
        "/api/records",
        headers=h,
        json=_record_payload(today, tasks_completed=6, tasks_total=5),
    )
    assert invalid_tasks.status_code == 422

    invalid_note = client.post(
        "/api/records",
        headers=h,
        json=_record_payload(today, note="x" * 501),
    )
    assert invalid_note.status_code == 422
