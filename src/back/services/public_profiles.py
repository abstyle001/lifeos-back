from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import Achievement, DailyRecord, ProfileSettings, SocialInteraction, User
from ..schemas import Attributes, ProfileSettingsOut, PublicAchievementOut, PublicProfileOut
from ..services.attributes import compute_attributes


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_profile_settings(db: Session, user_id: int) -> ProfileSettingsOut:
    settings = db.scalar(select(ProfileSettings).where(ProfileSettings.user_id == user_id))
    return ProfileSettingsOut(is_public=bool(settings and settings.is_public))


def set_profile_visibility(db: Session, user_id: int, is_public: bool) -> ProfileSettingsOut:
    settings = db.scalar(select(ProfileSettings).where(ProfileSettings.user_id == user_id))
    if settings is None:
        settings = ProfileSettings(user_id=user_id, is_public=is_public)
        db.add(settings)
    else:
        settings.is_public = is_public
    db.commit()
    db.refresh(settings)
    return ProfileSettingsOut(is_public=bool(settings.is_public))


def search_public_profiles(db: Session, query: str) -> list[User]:
    term = query.strip()
    if len(term) < 2:
        raise ValueError("请输入至少 2 个字符的用户名")
    if len(term) > 50:
        raise ValueError("用户名搜索不能超过 50 个字符")

    normalized = term.lower()
    escaped = _escape_like(normalized)
    username_lower = func.lower(User.username)
    priority = case(
        (username_lower == normalized, 0),
        (username_lower.like(f"{escaped}%", escape="\\"), 1),
        else_=2,
    )
    statement = (
        select(User)
        .join(ProfileSettings, ProfileSettings.user_id == User.id)
        .where(
            ProfileSettings.is_public.is_(True),
            username_lower.like(f"%{escaped}%", escape="\\"),
        )
        .order_by(priority, username_lower)
        .limit(20)
    )
    return list(db.scalars(statement))


def build_public_profile(
    db: Session, target: User, viewer: User
) -> PublicProfileOut | None:
    # Check visibility before loading any target-owned data. The response is
    # built explicitly below to keep the public allowlist separate from the
    # private dashboard and record schemas.
    is_public = bool(
        db.scalar(
            select(ProfileSettings.is_public).where(ProfileSettings.user_id == target.id)
        )
    )
    if target.id != viewer.id and not is_public:
        return None

    records = list(
        db.scalars(
            select(DailyRecord)
            .where(DailyRecord.user_id == target.id)
            .order_by(DailyRecord.date)
        )
    )
    social_records = list(
        db.scalars(
            select(SocialInteraction)
            .where(SocialInteraction.user_id == target.id)
            .order_by(SocialInteraction.date)
        )
    )
    achievements = list(
        db.scalars(
            select(Achievement)
            .where(Achievement.user_id == target.id)
            .order_by(Achievement.unlocked_at, Achievement.id)
        )
    )

    return PublicProfileOut(
        username=target.username,
        avatar=target.avatar,
        level=target.level,
        experience=target.experience,
        attributes=Attributes(**compute_attributes(records, social_records)),
        achievements=[
            PublicAchievementOut(
                code=achievement.code,
                title=achievement.title,
                description=achievement.description,
                unlocked_at=achievement.unlocked_at,
            )
            for achievement in achievements
        ],
    )
