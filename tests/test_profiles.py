from datetime import date


def _auth_headers(client, username: str) -> dict[str, str]:
    registered = client.post(
        "/api/auth/register", json={"username": username, "password": "secret123"}
    )
    assert registered.status_code == 201
    token = registered.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _set_public(client, headers: dict[str, str], value: bool = True):
    response = client.patch(
        "/api/profiles/me/settings", headers=headers, json={"is_public": value}
    )
    assert response.status_code == 200
    return response


def _record_payload(day: date) -> dict[str, object]:
    return {
        "date": day.isoformat(),
        "sleep": 7.5,
        "study_time": 3,
        "exercise": 1,
        "mood": 8,
        "focus": 8,
        "reading_time": 1,
        "skill_time": 1.5,
        "diet": 7,
        "stress": 3,
        "energy": 8,
        "tasks_completed": 2,
        "tasks_total": 2,
        "note": "private note",
    }


def test_profile_search_requires_auth_and_valid_query(client):
    assert client.get("/api/profiles/search?q=ab").status_code == 401

    headers = _auth_headers(client, "searchuser")
    assert client.get("/api/profiles/search?q=a", headers=headers).status_code == 422
    assert client.get("/api/profiles/search?q=%20%20", headers=headers).status_code == 422
    assert client.get("/api/profiles/search?q=" + "a" * 51, headers=headers).status_code == 422
    assert client.get("/api/profiles/search?q=%20" + "a" * 50 + "%20", headers=headers).status_code == 200


def test_profile_search_is_case_insensitive_public_only_and_ranked(client):
    viewer = _auth_headers(client, "viewer")
    exact = _auth_headers(client, "Alice")
    _set_public(client, exact)
    prefix = _auth_headers(client, "AliceDev")
    _set_public(client, prefix)
    contains = _auth_headers(client, "MyAlice")
    _set_public(client, contains)
    private = _auth_headers(client, "PrivateAlice")

    response = client.get("/api/profiles/search?q= alice ", headers=viewer)
    assert response.status_code == 200
    names = [item["username"] for item in response.json()]
    assert names[:3] == ["Alice", "AliceDev", "MyAlice"]
    assert "PrivateAlice" not in names
    assert all(set(item) == {"username", "avatar", "level", "experience"} for item in response.json())
    assert client.get("/api/profiles/search?q=AL", headers=viewer).status_code == 200
    assert private["Authorization"]


def test_profile_search_is_capped_at_twenty_results(client):
    viewer = _auth_headers(client, "capviewer")
    for index in range(21):
        headers = _auth_headers(client, f"capuser{index:02d}")
        _set_public(client, headers)

    response = client.get("/api/profiles/search?q=cap", headers=viewer)
    assert response.status_code == 200
    assert len(response.json()) == 20


def test_public_profile_is_allowlisted_and_uses_backend_attributes(client):
    owner = _auth_headers(client, "publicowner")
    _set_public(client, owner)
    record = client.post(
        "/api/records", headers=owner, json=_record_payload(date.today())
    )
    assert record.status_code == 200
    social = client.post(
        "/api/social",
        headers=owner,
        json={"date": date.today().isoformat(), "interactions": 3, "social_time": 0, "quality": 7},
    )
    assert social.status_code == 200

    viewer = _auth_headers(client, "profileviewer")
    response = client.get("/api/profiles/publicowner", headers=viewer)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"username", "avatar", "level", "experience", "attributes", "achievements"}
    assert set(body["attributes"]) == {"INT", "VIT", "FOCUS", "CHA"}
    assert any(achievement["code"] == "first_record" for achievement in body["achievements"])
    assert all(set(achievement) == {"code", "title", "description", "unlocked_at"} for achievement in body["achievements"])
    forbidden = " ".join(str(body).split()).lower()
    for key in ("note", "sleep", "mood", "stress", "tasks", "goals", "social_interactions", "progress", "password_hash"):
        assert key not in forbidden


def test_private_profile_is_hidden_from_others_but_visible_to_owner(client):
    owner = _auth_headers(client, "privateowner")
    viewer = _auth_headers(client, "privateviewer")

    settings = client.get("/api/profiles/me/settings", headers=owner)
    assert settings.status_code == 200
    assert settings.json() == {"is_public": False}

    assert client.get("/api/profiles/privateowner", headers=viewer).status_code == 404
    search = client.get("/api/profiles/search?q=private", headers=viewer)
    assert search.status_code == 200
    assert search.json() == []

    own = client.get("/api/profiles/privateowner", headers=owner)
    assert own.status_code == 200
    assert own.json()["username"] == "privateowner"

    _set_public(client, owner, False)
    assert client.get("/api/profiles/privateowner", headers=viewer).status_code == 404


def test_visibility_changes_are_immediately_enforced(client):
    owner = _auth_headers(client, "toggleowner")
    viewer = _auth_headers(client, "toggleviewer")

    assert client.patch(
        "/api/profiles/me/settings", headers=owner, json={"is_public": True, "extra": True}
    ).status_code == 422
    assert client.patch(
        "/api/profiles/me/settings", headers=owner, json={"is_public": "yes"}
    ).status_code == 422

    _set_public(client, owner, True)
    assert client.get("/api/profiles/search?q=toggle", headers=viewer).json()[0]["username"] == "toggleowner"
    _set_public(client, owner, False)
    assert client.get("/api/profiles/search?q=toggle", headers=viewer).json() == []
    _set_public(client, owner, True)
    assert client.get("/api/profiles/toggleowner", headers=viewer).status_code == 200


def test_unknown_profile_and_unauthenticated_settings_are_protected(client):
    headers = _auth_headers(client, "errorviewer")
    missing = client.get("/api/profiles/does-not-exist", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "用户不存在或不可见"
    assert client.get("/api/profiles/me/settings").status_code == 401
    assert client.patch("/api/profiles/me/settings", json={"is_public": True}).status_code == 401
