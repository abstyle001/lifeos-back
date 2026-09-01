"""头像上传/删除测试。mock 掉 vercel.blob，避免访问真实网络。"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest

from back.config import get_settings
from back.services import blob as blob_service
from back.services.blob import (
    MAX_BYTES,
    remove_avatar,
    upload_avatar,
)


class _FakeUploadFile:
    def __init__(self, content: bytes, content_type: str) -> None:
        self.file = BytesIO(content)
        self.content_type = content_type


@pytest.fixture()
def fake_put(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    def _put(
        pathname: str,
        body: bytes,
        *,
        access: str,
        content_type: str | None,
        add_random_suffix: bool,
        token: str | None = None,
    ) -> SimpleNamespace:
        calls.append(
            {
                "pathname": pathname,
                "size": len(body),
                "access": access,
                "content_type": content_type,
                "add_random_suffix": add_random_suffix,
                "token": token,
            }
        )
        # 真实行为：随机后缀插在 base 与扩展名之间，如 `user_42_ecde53b7-rand.png`
        if add_random_suffix and "." in pathname:
            base, ext = pathname.rsplit(".", 1)
            final_pathname = f"{base}-rand.{ext}"
        else:
            final_pathname = pathname
        return SimpleNamespace(
            url=f"https://store.public.blob.vercel-storage.com/{final_pathname}",
            pathname=final_pathname,
        )

    monkeypatch.setattr(blob_service, "put", _put)
    return calls


@pytest.fixture()
def fake_delete(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    def _delete(url: str, *, token: str | None = None) -> None:
        calls.append({"url": url, "token": token})

    monkeypatch.setattr(blob_service, "delete", _delete)
    return calls


def _register_and_login(client) -> str:
    r = client.post(
        "/api/auth/register", json={"username": "alice", "password": "secret123"}
    )
    return r.json()["access_token"]


@pytest.fixture(autouse=True)
def fake_token(monkeypatch: pytest.MonkeyPatch):
    """让 router 通过 get_settings() 拿到测试用的 token。"""
    get_settings.cache_clear()
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_test")
    yield
    get_settings.cache_clear()


def test_upload_avatar_happy_path(fake_put, fake_delete):
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # 108 bytes
    file = _FakeUploadFile(payload, "image/png")
    url = upload_avatar(file, user_id=42, token="vercel_blob_rw_test")

    assert url.startswith(
        "https://store.public.blob.vercel-storage.com/avatars/user_42_"
    )
    assert url.endswith(".png")
    assert len(fake_put) == 1
    call = fake_put[0]
    assert call["pathname"].startswith("avatars/user_42_")
    assert call["pathname"].endswith(".png")
    assert call["access"] == "public"
    assert call["content_type"] == "image/png"
    assert call["add_random_suffix"] is True
    assert call["size"] == len(payload)
    assert call["token"] == "vercel_blob_rw_test"
    assert fake_delete == []


def test_upload_avatar_rejects_bad_mime(fake_put, fake_delete):
    file = _FakeUploadFile(b"not an image", "text/plain")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        upload_avatar(file, user_id=1)
    assert exc.value.status_code == 415
    assert fake_put == []
    assert fake_delete == []


def test_upload_avatar_rejects_oversize(fake_put, fake_delete):
    payload = b"\xff" * (MAX_BYTES + 1)
    file = _FakeUploadFile(payload, "image/jpeg")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        upload_avatar(file, user_id=1)
    assert exc.value.status_code == 413
    assert fake_put == []


def test_upload_avatar_rejects_empty(fake_put):
    file = _FakeUploadFile(b"", "image/png")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        upload_avatar(file, user_id=1)
    assert exc.value.status_code == 400


def test_remove_avatar_only_cleans_blob_urls(fake_delete):
    remove_avatar("https://store.public.blob.vercel-storage.com/avatars/user_1_abc.png", token="t")
    remove_avatar(None, token="t")
    remove_avatar("", token="t")
    remove_avatar("https://example.com/external.png", token="t")
    assert fake_delete == [
        {
            "url": "https://store.public.blob.vercel-storage.com/avatars/user_1_abc.png",
            "token": "t",
        }
    ]


def test_remove_avatar_swallows_sdk_errors(fake_delete, monkeypatch):
    from vercel.blob import BlobError

    def boom(url: str, *, token: str | None = None) -> None:
        raise BlobError("network down")

    monkeypatch.setattr(blob_service, "delete", boom)
    # 不应抛错
    remove_avatar("https://store.public.blob.vercel-storage.com/avatars/user_1_abc.png")


def test_strip_token_handles_whitespace():
    from back.services.blob import _strip_token

    assert _strip_token("") is None
    assert _strip_token("   ") is None
    assert _strip_token(None) is None
    assert _strip_token("  abc  ") == "abc"


# ---------- HTTP 端到端 ----------


def test_post_me_avatar_saves_url_and_cleans_old(client, fake_put, fake_delete):
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 先做一次上传
    files = {"file": ("avatar.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "image/png")}
    r1 = client.post("/api/auth/me/avatar", headers=headers, files=files)
    assert r1.status_code == 200, r1.text
    first_url = r1.json()["avatar"]
    assert first_url.endswith(".png")

    # 再上传一张，旧的应被 remove
    files2 = {"file": ("avatar.jpg", b"\xff\xd8\xff" + b"\x00" * 60, "image/jpeg")}
    r2 = client.post("/api/auth/me/avatar", headers=headers, files=files2)
    assert r2.status_code == 200, r2.text
    second_url = r2.json()["avatar"]
    assert second_url != first_url

    me = client.get("/api/auth/me", headers=headers).json()
    assert me["avatar"] == second_url
    assert len(fake_delete) == 1
    assert fake_delete[0]["url"] == first_url
    assert fake_delete[0]["token"] == "vercel_blob_rw_test"


def test_delete_me_avatar_clears_and_removes(client, fake_put, fake_delete):
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    files = {"file": ("a.gif", b"GIF89a" + b"\x00" * 30, "image/gif")}
    up = client.post("/api/auth/me/avatar", headers=headers, files=files)
    assert up.status_code == 200
    url = up.json()["avatar"]

    fake_delete.clear()
    rm = client.delete("/api/auth/me/avatar", headers=headers)
    assert rm.status_code == 200
    assert rm.json()["avatar"] is None
    assert len(fake_delete) == 1
    assert fake_delete[0]["url"] == url
    assert fake_delete[0]["token"] == "vercel_blob_rw_test"

    me = client.get("/api/auth/me", headers=headers).json()
    assert me["avatar"] is None


def test_post_me_avatar_rejects_bad_mime(client):
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    files = {"file": ("a.txt", b"hello", "text/plain")}
    r = client.post("/api/auth/me/avatar", headers=headers, files=files)
    assert r.status_code == 415


def test_post_me_avatar_requires_auth(client):
    files = {"file": ("a.png", b"\x89PNG", "image/png")}
    r = client.post("/api/auth/me/avatar", files=files)
    assert r.status_code == 401


def test_patch_me_no_longer_accepts_avatar(client):
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # avatar 字段已从 PATCH /me 移除；旧调用应被 422 拒绝
    r = client.patch(
        "/api/auth/me",
        headers=headers,
        json={"avatar": "https://example.com/x.png"},
    )
    assert r.status_code == 422