from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_session, get_current_user
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import (
    generate_session_token,
    hash_session_token,
    verify_password,
)
from app.core.time import utcnow
from app.db import get_db
from app.models import User, UserSession
from app.schemas import AuthResponse, LoginRequest, UserRead

router = APIRouter()


def _issue_session(db: AsyncSession, user: User) -> str:
    settings = get_settings()
    token = generate_session_token()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=utcnow() + timedelta(days=settings.session_ttl_days),
        )
    )
    return token


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        path="/",
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    email = str(payload.email).lower()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "邮箱或密码错误")
    if not user.is_active:
        raise AppError(401, "invalid_credentials", "邮箱或密码错误")

    token = _issue_session(db, user)
    await db.commit()
    _set_session_cookie(response, token)
    return AuthResponse(user=UserRead.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    current: Annotated[UserSession, Depends(get_current_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await db.delete(current)
    await db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.get("/me", response_model=UserRead)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserRead:
    return UserRead.model_validate(user)
