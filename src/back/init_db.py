from __future__ import annotations

from .config import get_settings
from .database import Base, SessionLocal, engine
from .seed import seed


def main() -> None:
    """一次性初始化数据库：建表 + （可选）写入演示数据。

    用于生产环境：先把表建好，再让服务以 AUTO_CREATE_TABLES=false 运行，
    避免每次 serverless 冷启动都跑 create_all。
    """
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    if settings.seed_demo:
        with SessionLocal() as db:
            seed(db)
    print("数据库初始化完成（建表 + 按需 seed）。")


if __name__ == "__main__":
    main()
