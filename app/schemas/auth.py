from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.roles import UserRole
from app.schemas.common import UTCDatetime


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str | None
    role: UserRole
    is_active: bool
    created_at: UTCDatetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=1, max_length=128)


class AuthResponse(BaseModel):
    user: UserRead
