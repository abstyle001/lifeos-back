from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Follow, ProfileSettings, User
from ..schemas import FollowActionOut, FollowRelationOut, FollowUserOut

FOLLOW_LIST_LIMIT = 100


class TargetNotFound(LookupError):
    """目标用户不存在，或对当前查看者不可见。"""


class SelfFollowError(ValueError):
    """不能关注自己。"""


def is_public_profile(db: Session, user_id: int) -> bool:
    """无 ProfileSettings 行按私密处理（与 public_profiles 口径一致）。"""
    return bool(
        db.scalar(
            select(ProfileSettings.is_public).where(ProfileSettings.user_id == user_id)
        )
    )


def resolve_visible_target(db: Session, username: str, viewer: User) -> User:
    """按用户名定位目标；路径保留字 "me" 指向查看者本人。

    他人视角下，不存在或私密的用户统一抛 TargetNotFound（不暴露存在性）。
    """
    if username == "me":
        return viewer
    target = db.scalar(select(User).where(User.username == username))
    if target is None:
        raise TargetNotFound
    if target.id != viewer.id and not is_public_profile(db, target.id):
        raise TargetNotFound
    return target


def follow_counts(db: Session, user_id: int) -> tuple[int, int]:
    """返回 (关注数, 粉丝数)。"""
    following_count = db.scalar(
        select(func.count()).select_from(Follow).where(Follow.follower_id == user_id)
    )
    followers_count = db.scalar(
        select(func.count()).select_from(Follow).where(Follow.followee_id == user_id)
    )
    return int(following_count or 0), int(followers_count or 0)


def is_following(db: Session, follower_id: int, followee_id: int) -> bool:
    return (
        db.scalar(
            select(Follow.id).where(
                Follow.follower_id == follower_id,
                Follow.followee_id == followee_id,
            )
        )
        is not None
    )


def _to_card(user: User) -> FollowUserOut:
    return FollowUserOut(
        username=user.username,
        avatar=user.avatar,
        level=user.level,
        experience=user.experience,
    )


def follow_user(db: Session, follower: User, username: str) -> FollowActionOut:
    """关注目标用户（幂等：重复关注不报错）。仅公开用户可被关注。"""
    target = resolve_visible_target(db, username, follower)
    if target.id == follower.id:
        raise SelfFollowError
    if not is_following(db, follower.id, target.id):
        db.add(Follow(follower_id=follower.id, followee_id=target.id))
        db.commit()
    _, followers_count = follow_counts(db, target.id)
    return FollowActionOut(
        username=target.username, is_following=True, followers_count=followers_count
    )


def unfollow_user(db: Session, follower: User, username: str) -> FollowActionOut:
    """取消关注（幂等）。不要求目标仍公开——对方转为私密后也要能取关。"""
    if username == "me":
        raise TargetNotFound
    target = db.scalar(select(User).where(User.username == username))
    if target is None:
        raise TargetNotFound
    existing = db.scalar(
        select(Follow).where(
            Follow.follower_id == follower.id,
            Follow.followee_id == target.id,
        )
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
    _, followers_count = follow_counts(db, target.id)
    return FollowActionOut(
        username=target.username, is_following=False, followers_count=followers_count
    )


def list_related(
    db: Session, viewer: User, username: str, kind: str
) -> list[FollowUserOut]:
    """关注/粉丝列表。查看私密目标（非本人）时抛 TargetNotFound。"""
    target = resolve_visible_target(db, username, viewer)
    if kind == "following":
        join_condition = Follow.followee_id == User.id
        where_condition = Follow.follower_id == target.id
    else:
        join_condition = Follow.follower_id == User.id
        where_condition = Follow.followee_id == target.id
    statement = (
        select(User)
        .join(Follow, join_condition)
        .where(where_condition)
        .order_by(Follow.created_at.desc(), func.lower(User.username))
        .limit(FOLLOW_LIST_LIMIT)
    )
    return [_to_card(user) for user in db.scalars(statement)]


def get_relation(db: Session, viewer: User, username: str) -> FollowRelationOut:
    """查看者与目标用户之间的双向关注状态及计数。"""
    target = resolve_visible_target(db, username, viewer)
    following_count, followers_count = follow_counts(db, target.id)
    is_self = target.id == viewer.id
    return FollowRelationOut(
        username=target.username,
        is_self=is_self,
        is_following=not is_self and is_following(db, viewer.id, target.id),
        is_followed_by=not is_self and is_following(db, target.id, viewer.id),
        following_count=following_count,
        followers_count=followers_count,
    )
