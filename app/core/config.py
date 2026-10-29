from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
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

    paper_storage_dir: Path = Path("var/papers")
    paper_processing_timeout_minutes: int = Field(default=30, ge=1)

    session_cookie_name: str = "paperlens_session"
    session_ttl_days: int = Field(default=7, ge=1)
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_tool_steps: int = Field(default=4, ge=1, le=10)
    llm_max_output_tokens: int = Field(default=2048, ge=1)

    embedding_provider: Literal["none", "local"] = "none"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_model_path: Path | None = None
    embedding_query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    vector_top_k: int = Field(default=40, ge=1, le=500)

    @model_validator(mode="after")
    def _require_secure_cookie_in_production(self) -> Settings:
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("生产环境必须设置 COOKIE_SECURE=true")
        return self

    @field_validator("embedding_model_path", mode="before")
    @classmethod
    def _blank_path_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def ai_configured(self) -> bool:
        return bool(self.deepseek_api_key and self.deepseek_api_key.strip())

    @property
    def embeddings_enabled(self) -> bool:
        return self.embedding_provider != "none"

    def resolved_database_url(self) -> str:
        url = self.test_database_url if self.app_env == "test" else self.database_url
        if not url:
            env_key = "TEST_DATABASE_URL" if self.app_env == "test" else "DATABASE_URL"
            raise RuntimeError(f"{env_key} is not configured")
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
