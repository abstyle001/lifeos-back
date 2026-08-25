from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "LifeOS"
    database_url: str = "postgresql+psycopg://lifeos:lifeos@localhost:5432/lifeos"
    secret_key: str = "dev-secret-key-change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 天
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    seed_demo: bool = True
    auto_create_tables: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
