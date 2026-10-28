from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import AppError
from app.db import get_db
from app.models import User
from app.schemas.chat import MessagePage, SessionDetail, SessionPage
from app.services.session_service import (
    get_owned_session,
    get_session_conversation,
    get_session_detail,
    list_messages,
    list_sessions,
)

router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=SessionPage)
async def list_sessions_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SessionPage:
    return await list_sessions(
        db,
        user_id=user.id,
        page=page,
        page_size=page_size,
    )


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SessionDetail:
    session = await get_owned_session(db, session_id=session_id, user_id=user.id)
    return await get_session_detail(db, session)


@router.get("/{session_id}/messages", response_model=MessagePage)
async def get_session_messages(
    session_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> MessagePage:
    await get_owned_session(db, session_id=session_id, user_id=user.id)
    conversation = await get_session_conversation(
        db,
        session_id=session_id,
        user_id=user.id,
    )
    if conversation is None:
        raise AppError(500, "internal_error", "会话数据不完整")
    return await list_messages(
        db,
        conversation_id=conversation.id,
        limit=limit,
        before_id=before_id,
    )
