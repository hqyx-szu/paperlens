from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import hash_session_token
from app.core.time import utcnow
from app.db import get_db
from app.models import User, UserSession


async def get_current_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserSession:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise AppError(401, "unauthorized", "请先登录")

    stmt = (
        select(UserSession)
        .options(selectinload(UserSession.user))
        .where(UserSession.token_hash == hash_session_token(token))
    )
    session = await db.scalar(stmt)
    if session is None or session.expires_at <= utcnow() or not session.user.is_active:
        raise AppError(401, "unauthorized", "登录已失效，请重新登录")
    return session


async def get_current_user(
    session: Annotated[UserSession, Depends(get_current_session)],
) -> User:
    return session.user
