# LifeOS Backend

LifeOS 后端服务：个人数据可视化人生仪表盘的 API，基于 FastAPI + SQLAlchemy + PostgreSQL。

完整的开发指南、目录结构、API 表、数据模型与部署说明见仓库根目录 [AGENTS.md](../AGENTS.md)。

## 快速开始

```bash
docker compose up -d                    # 在仓库根目录启动本地 PostgreSQL
cd back
uv sync                                 # 安装依赖
uv run uvicorn back.main:app --reload   # 或 uv run back
```

首次启动自动建表，并按 `SEED_DEMO`（默认 true）写入 `demo/demo1234` 演示数据。

## 配置

复制 `.env.example` 为 `.env` 后修改。关键键：`DATABASE_URL`、`SECRET_KEY`、`CORS_ORIGINS`、`SEED_DEMO`、`AUTO_CREATE_TABLES`、`AI_BASE_URL`/`AI_API_KEY`/`AI_MODEL`/`AI_TIMEOUT_SECONDS`、`BLOB_READ_WRITE_TOKEN`。

## 测试

```bash
uv run pytest
```

测试跑在临时 SQLite 上，无需 Postgres。

## 部署

Vercel Serverless 入口在 `api/index.py`，路由见 `vercel.json`；步骤见 [AGENTS.md](../AGENTS.md) 的「部署（Vercel）」。
