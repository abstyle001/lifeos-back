def _auth_headers(client, username: str) -> dict[str, str]:
    registered = client.post(
        "/api/auth/register", json={"username": username, "password": "secret123"}
    )
    assert registered.status_code == 201
    token = registered.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _set_public(client, headers: dict[str, str]):
    response = client.patch(
        "/api/profiles/me/settings", headers=headers, json={"is_public": True}
    )
    assert response.status_code == 200
    return response


def test_follow_endpoints_require_auth(client):
    assert client.post("/api/follows/someone").status_code == 401
    assert client.delete("/api/follows/someone").status_code == 401
    assert client.get("/api/follows/someone/relation").status_code == 401
    assert client.get("/api/follows/someone/following").status_code == 401


def test_cannot_follow_self(client):
    alice = _auth_headers(client, "falice")
    _set_public(client, alice)
    response = client.post("/api/follows/falice", headers=alice)
    assert response.status_code == 400


def test_cannot_follow_private_or_missing_user(client):
    viewer = _auth_headers(client, "fviewer")
    private_user = _auth_headers(client, "fhiddenguy")  # 未公开

    assert client.post("/api/follows/fhiddenguy", headers=viewer).status_code == 404
    assert client.post("/api/follows/ghost-account", headers=viewer).status_code == 404
    # 私密用户的关系/列表同样不可见
    assert client.get("/api/follows/fhiddenguy/relation", headers=viewer).status_code == 404
    assert client.get("/api/follows/fhiddenguy/followers", headers=viewer).status_code == 404


def test_follow_and_unfollow_are_idempotent(client):
    alice = _auth_headers(client, "idpalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "idpbob")

    first = client.post("/api/follows/idpalice", headers=bob)
    assert first.status_code == 200
    assert first.json() == {
        "username": "idpalice",
        "is_following": True,
        "followers_count": 1,
    }
    # 重复关注幂等，计数不增加
    second = client.post("/api/follows/idpalice", headers=bob)
    assert second.status_code == 200
    assert second.json()["followers_count"] == 1

    removed = client.delete("/api/follows/idpalice", headers=bob)
    assert removed.status_code == 200
    assert removed.json() == {
        "username": "idpalice",
        "is_following": False,
        "followers_count": 0,
    }
    # 重复取关也幂等
    again = client.delete("/api/follows/idpalice", headers=bob)
    assert again.status_code == 200
    assert again.json()["followers_count"] == 0


def test_relation_and_follow_lists_reflect_both_directions(client):
    alice = _auth_headers(client, "rlalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "rlbob")
    _set_public(client, bob)
    carol = _auth_headers(client, "rlcarol")  # carol 保持私密，也可以关注别人

    # bob、carol 关注 alice；alice 回关 bob（互关）
    assert client.post("/api/follows/rlalice", headers=bob).status_code == 200
    assert client.post("/api/follows/rlalice", headers=carol).status_code == 200
    assert client.post("/api/follows/rlbob", headers=alice).status_code == 200

    # alice 的粉丝：bob、carol
    followers = client.get("/api/follows/rlalice/followers", headers=bob)
    assert followers.status_code == 200
    follower_names = sorted(item["username"] for item in followers.json())
    assert follower_names == ["rlbob", "rlcarol"]
    assert all(set(item) == {"username", "avatar", "level", "experience"} for item in followers.json())

    # bob 的关注：alice
    following = client.get("/api/follows/rlbob/following", headers=alice)
    assert [item["username"] for item in following.json()] == ["rlalice"]

    # bob 视角看 alice：互关
    relation = client.get("/api/follows/rlalice/relation", headers=bob)
    assert relation.status_code == 200
    assert relation.json() == {
        "username": "rlalice",
        "is_self": False,
        "is_following": True,
        "is_followed_by": True,
        "following_count": 1,
        "followers_count": 2,
    }

    # me 保留字指向自己
    mine = client.get("/api/follows/me/following", headers=bob)
    assert mine.status_code == 200
    assert [item["username"] for item in mine.json()] == ["rlalice"]
    own_relation = client.get("/api/follows/me/relation", headers=bob).json()
    assert own_relation["is_self"] is True
    assert own_relation["is_following"] is False


def test_invalid_follow_list_kind_is_rejected(client):
    viewer = _auth_headers(client, "kindviewer")
    response = client.get("/api/follows/me/strangers", headers=viewer)
    assert response.status_code == 422


def test_private_user_can_view_own_lists_but_others_cannot(client):
    dave = _auth_headers(client, "pvdave")  # 保持私密
    viewer = _auth_headers(client, "pvviewer")

    assert client.get("/api/follows/pvdave/following", headers=viewer).status_code == 404
    own = client.get("/api/follows/me/following", headers=dave)
    assert own.status_code == 200
    assert own.json() == []


def test_unfollow_works_after_target_becomes_private(client):
    alice = _auth_headers(client, "tpalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "tpbob")
    assert client.post("/api/follows/tpalice", headers=bob).status_code == 200

    # alice 转为私密后，bob 仍应能取关
    client.patch("/api/profiles/me/settings", headers=alice, json={"is_public": False})
    assert client.post("/api/follows/tpalice", headers=bob).status_code == 404
    removed = client.delete("/api/follows/tpalice", headers=bob)
    assert removed.status_code == 200
    assert removed.json()["followers_count"] == 0


def test_public_profile_carries_follow_state(client):
    alice = _auth_headers(client, "psalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "psbob")
    _set_public(client, bob)

    before = client.get("/api/profiles/psalice", headers=bob).json()
    assert before["is_following"] is False
    assert before["is_followed_by"] is False
    assert before["followers_count"] == 0
    assert before["is_self"] is False

    # bob 关注 alice，alice 回关 bob → 互关
    client.post("/api/follows/psalice", headers=bob)
    client.post("/api/follows/psbob", headers=alice)
    after = client.get("/api/profiles/psalice", headers=bob).json()
    assert after["is_following"] is True
    assert after["is_followed_by"] is True
    assert after["followers_count"] == 1

    own = client.get("/api/profiles/psalice", headers=alice).json()
    assert own["is_self"] is True
    assert own["is_following"] is False
    assert own["is_followed_by"] is False
