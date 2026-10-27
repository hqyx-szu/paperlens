from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.roles import UserRole
from app.schemas.auth import UserRead
from app.schemas.common import UTCDatetime


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)
    role: UserRole = UserRole.reader

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=64)
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _reject_null_switches(self) -> AdminUserUpdate:
        if "role" in self.model_fields_set and self.role is None:
            raise ValueError("role 不能为 null")
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("is_active 不能为 null")
        return self


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=8, max_length=128)


class AdminUserPage(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int


class AdminStats(BaseModel):
    users_total: int
    users_active: int
    papers_total: int
    papers_ready: int
    papers_processing: int
    papers_failed: int
    papers_archived: int
    repos_total: int
    repos_private: int
    generated_at: UTCDatetime
