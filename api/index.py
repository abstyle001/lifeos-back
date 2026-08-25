import sys
from pathlib import Path

# 把 src 目录加进导入路径，以便导入 src/back 包（项目使用 src 布局）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from back.main import app  # noqa: E402, F401
