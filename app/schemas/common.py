from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, PlainSerializer


def format_utc(value: datetime) -> str:
    """Render a datetime as RFC 3339 UTC with second precision and a trailing Z."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


UTCDatetime = Annotated[datetime, PlainSerializer(format_utc, return_type=str)]


class UserBrief(BaseModel):
    """公开/半公开场景的用户信息，不含邮箱（契约 §2.2）。"""

    id: int
    display_name: str | None
