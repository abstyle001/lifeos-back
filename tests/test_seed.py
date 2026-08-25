from datetime import date

from back.database import Base, SessionLocal, engine
from back.models import DailyRecord, User
from back.security import hash_password
from back.seed import seed
from back.services.achievements import check_and_unlock


def test_seed_creates_demo_user_with_achievements():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        seed(db)

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "demo").first()
        assert user is not None
        assert user.level >= 1
        assert user.experience > 0

        codes = {a.code for a in user.achievements}
        assert "first_record" in codes
        assert "streak_7" in codes


def test_seed_is_idempotent():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        seed(db)
    with SessionLocal() as db:
        seed(db)

    with SessionLocal() as db:
        users = db.query(User).filter(User.username == "demo").all()
        assert len(users) == 1


def test_check_and_unlock_is_idempotent_within_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        user = User(username="alice", password_hash=hash_password("secret123"))
        db.add(user)
        db.flush()
        db.add(DailyRecord(user_id=user.id, date=date(2026, 8, 1)))
        db.commit()

        check_and_unlock(db, user)
        check_and_unlock(db, user)
        db.commit()

        codes = [a.code for a in user.achievements]
        assert codes.count("first_record") == 1
