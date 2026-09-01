"""Vercel Blob 头像上传/删除服务。

封装官方 `vercel` Python SDK（`vercel.blob`）。本项目使用静态 `BLOB_READ_WRITE_TOKEN`，
从 `Settings.blob_read_write_token` 显式传入；后续如需切换 OIDC，只需在 Vercel 控制台把
Blob store 关联到项目即可让 `Settings` 通过 OIDC 自动取 token。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from vercel.blob import (
    BlobContentTypeNotAllowedError,
    BlobError,
    BlobFileTooLargeError,
    delete,
    put,
)

ALLOWED_MIME: set[str] = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_BYTES: int = 2 * 1024 * 1024  # 2 MB
MIME_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _strip_token(token: str | None) -> str | None:
    """空白会被 SDK 误判成合法 token；空字符串统一返回 None 让 SDK 自动 fallback。"""
    if token is None:
        return None
    stripped = token.strip()
    return stripped or None


def upload_avatar(file: UploadFile, user_id: int, *, token: str | None = None) -> str:
    """把用户上传的头像写到 Vercel Blob，返回公开 URL。

    - 校验 MIME 与大小；
    - path 为 `avatars/user_{id}_{uuid8}.{ext}`，`add_random_suffix=True` 兜底防重名。
    - `token` 留空时让 SDK 自动从环境变量读取（生产 Vercel 上 OIDC 自动注入）。
    """
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="仅支持 jpg / png / webp / gif 图片",
        )
    payload = file.file.read()
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="图片不能超过 2 MB",
        )
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片内容为空",
        )

    ext = MIME_EXT[content_type]
    pathname = f"avatars/user_{user_id}_{uuid4().hex[:8]}.{ext}"
    resolved_token = _strip_token(token)
    try:
        result = put(
            pathname,
            payload,
            access="public",
            content_type=content_type,
            add_random_suffix=True,
            token=resolved_token,
        )
    except BlobFileTooLargeError:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="图片不能超过 2 MB",
        )
    except BlobContentTypeNotAllowedError:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="仅支持 jpg / png / webp / gif 图片",
        )
    except BlobError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"头像上传失败：{exc}",
        )

    return result.url


def remove_avatar(url: str | None, *, token: str | None = None) -> None:
    """best-effort 删除 Vercel Blob 上的头像。仅当 URL 指向 blob.vercel-storage.com 时调用。

    删除失败不抛错，避免脏数据阻塞后续流程。
    """
    if not url or "blob.vercel-storage.com" not in url:
        return
    try:
        delete(url, token=_strip_token(token))
    except BlobError:
        pass