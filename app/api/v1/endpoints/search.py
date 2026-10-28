from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.chat import QueryType
from app.core.exceptions import AppError
from app.db import get_db
from app.models import User
from app.schemas.chat import SearchRequest, SessionDetail
from app.services.search import create_search_session, search_catalog
from app.services.session_service import get_session_detail

router = APIRouter(tags=["search"], dependencies=[Depends(get_current_user)])


@router.post("/search", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
async def search(
    payload: SearchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SessionDetail:
    try:
        result = await search_catalog(db, payload.query)
        session = await create_search_session(
            db,
            user_id=user.id,
            query=payload.query,
            query_type=QueryType.text,
            result=result,
        )
    except SQLAlchemyError:
        await db.rollback()
        raise AppError(503, "search_unavailable", "检索服务暂时不可用") from None
    return await get_session_detail(db, session)
