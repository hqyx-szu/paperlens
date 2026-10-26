from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "PaperLens"
    app_version: str = "0.1.0"
    app_env: Literal["local", "test", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    database_url: str | None = None
    test_database_url: str | None = None

    session_cookie_name: str = "paperlens_session"
    session_ttl_days: int = Field(default=7, ge=1)
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def resolved_database_url(self) -> str:
        url = self.test_database_url if self.app_env == "test" else self.database_url
        if not url:
            env_key = "TEST_DATABASE_URL" if self.app_env == "test" else "DATABASE_URL"
            raise RuntimeError(f"{env_key} is not configured")
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
