from datetime import date


def _auth_headers(client, username="social_user"):
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


def _social_payload(day, **kw):
    payload = dict(date=day.isoformat(), interactions=3, social_time=2.0, quality=7)
    payload.update(kw)
    return payload


def test_social_upsert_and_list(client):
    h = _auth_headers(client)
    today = date.today()

    r = client.post("/api/social", headers=h, json=_social_payload(today))
    assert r.status_code == 200
    assert r.json()["interactions"] == 3

    # 同一天 upsert 覆盖
    client.post("/api/social", headers=h, json=_social_payload(today, interactions=5))
    items = client.get("/api/social", headers=h).json()
    assert len(items) == 1
    assert items[0]["interactions"] == 5


def test_social_requires_auth(client):
    assert client.post("/api/social", json=_social_payload(date.today())).status_code == 401
    assert client.get("/api/social").status_code == 401


def test_social_delete(client):
    h = _auth_headers(client)
    created = client.post(
        "/api/social", headers=h, json=_social_payload(date.today())
    ).json()

    deleted = client.delete(f"/api/social/{created['id']}", headers=h)
    assert deleted.status_code == 204
    assert client.get("/api/social", headers=h).json() == []


def test_social_out_of_range_rejected(client):
    h = _auth_headers(client)
    r = client.post(
        "/api/social", headers=h, json=_social_payload(date.today(), quality=11)
    )
    assert r.status_code == 422
