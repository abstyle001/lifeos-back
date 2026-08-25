def test_register_login_and_me(client):
    r = client.post(
        "/api/auth/register", json={"username": "alice", "password": "secret123"}
    )
    assert r.status_code == 201
    token = r.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    login = client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret123"}
    )
    assert login.status_code == 200
    assert login.json()["user"]["username"] == "alice"


def test_register_duplicate_and_bad_login(client):
    client.post("/api/auth/register", json={"username": "bob", "password": "secret123"})

    dup = client.post(
        "/api/auth/register", json={"username": "bob", "password": "secret123"}
    )
    assert dup.status_code == 409

    bad = client.post(
        "/api/auth/login", json={"username": "bob", "password": "wrongpass"}
    )
    assert bad.status_code == 401


def test_me_requires_auth(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
