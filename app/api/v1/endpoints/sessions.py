from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas.chat import SessionDetail
from app.services.session_service import get_owned_session, get_session_detail

router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SessionDetail:
    session = await get_owned_session(db, session_id=session_id, user_id=user.id)
    return await get_session_detail(db, session)
