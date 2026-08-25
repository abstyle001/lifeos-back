import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# 必须在导入 back 包之前设置，让 get_settings() 读到临时测试库（而不是默认的 lifeos.db）
_TMP = tempfile.mkdtemp(prefix="lifeos-test-")
_DB_PATH = os.path.join(_TMP, "test.db").replace("\\", "/")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["SEED_DEMO"] = "false"

from back.database import Base, engine  # noqa: E402
from back.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
