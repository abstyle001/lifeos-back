from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import DailyRecord, Goal, SocialInteraction, Task, User
from .security import hash_password
from .services.achievements import check_and_unlock
from .services.experience import level_for_xp, record_xp

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo1234"


def seed(db: Session) -> None:
    """幂等：若无 demo 用户，则创建 demo 用户 + 近 30 天示例数据。"""
    existing = db.scalar(select(User).where(User.username == DEMO_USERNAME))
    if existing is not None:
        return

    user = User(username=DEMO_USERNAME, password_hash=hash_password(DEMO_PASSWORD))
    db.add(user)
    db.flush()

    rng = random.Random(42)
    today = date.today()
    total_xp = 0
    for i in range(30, 0, -1):
        day = today - timedelta(days=i)
        progress = (30 - i) / 30  # 0 -> 1，模拟成长上升趋势
        record = DailyRecord(
            user_id=user.id,
            date=day,
            sleep=round(rng.uniform(6.0, 8.5), 1),
            study_time=round(rng.uniform(1.0, 3.5) + progress * 2, 1),
            exercise=round(rng.uniform(0.0, 1.2) + progress * 0.6, 1),
            mood=rng.randint(4, 9),
            focus=rng.randint(4, 9),
            reading_count=rng.randint(0, 1),
            skill_time=round(rng.uniform(0.0, 1.5) + progress * 1.5, 1),
            diet=rng.randint(4, 9),
            stress=rng.randint(2, 7),
            energy=rng.randint(4, 9),
            tasks_completed=rng.randint(1, 5),
            tasks_total=5,
        )
        db.add(record)
        total_xp += record_xp(record)

        social = SocialInteraction(
            user_id=user.id,
            date=day,
            interactions=round(rng.uniform(0, 2) + progress * 3),
            social_time=round(rng.uniform(0.0, 1.0) + progress * 1.5, 1),
            quality=rng.randint(4, 9),
        )
        db.add(social)

    # 演示任务（最近 5 天）
    for i in range(5, 0, -1):
        day = today - timedelta(days=i)
        db.add(Task(user_id=user.id, date=day, title="阅读 30 分钟", done=True))
        db.add(Task(user_id=user.id, date=day, title="锻炼 20 分钟", done=i <= 3))

    # 演示目标
    db.add(Goal(user_id=user.id, title="连续打卡 30 天", done=False))
    db.add(Goal(user_id=user.id, title="读完 5 本书", done=False))

    user.experience = total_xp
    user.level = level_for_xp(total_xp)
    db.commit()

    check_and_unlock(db, user)
    db.commit()


def run_seed() -> None:
    with SessionLocal() as db:
        seed(db)
