from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import User
from ..schemas import Token, UserCreate, UserOut, UserUpdate
from ..security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from ..services.blob import remove_avatar, upload_avatar

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Annotated[Session, Depends(get_db)]) -> Token:
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    user = User(username=payload.username, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return Token(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.post("/login", response_model=Token)
def login(payload: UserCreate, db: Annotated[Session, Depends(get_db)]) -> Token:
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    return Token(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current: Annotated[User, Depends(get_current_user)]) -> User:
    return current


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> User:
    if payload.username is not None:
        username = payload.username.strip()
        if len(username) < 3:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="用户名长度至少 3 个字符",
            )
        if username != current.username:
            existing = (
                db.query(User).filter(User.username == username, User.id != current.id).first()
            )
            if existing:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
            current.username = username

    if payload.new_password is not None:
        if not verify_password(payload.old_password or "", current.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码错误"
            )
        current.password_hash = hash_password(payload.new_password)

    db.commit()
    db.refresh(current)
    return current


@router.post("/me/avatar", response_model=UserOut)
def upload_my_avatar(
    file: Annotated[UploadFile, File(description="头像图片（jpg/png/webp/gif，≤2MB）")],
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> User:
    token = get_settings().blob_read_write_token
    new_url = upload_avatar(file, current.id, token=token)
    remove_avatar(current.avatar, token=token)
    current.avatar = new_url
    db.commit()
    db.refresh(current)
    return current


@router.delete("/me/avatar", response_model=UserOut)
def remove_my_avatar(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> User:
    token = get_settings().blob_read_write_token
    remove_avatar(current.avatar, token=token)
    current.avatar = None
    db.commit()
    db.refresh(current)
    return current
