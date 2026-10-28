from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat import ChatScope, QueryType
from app.core.exceptions import AppError
from app.models import Conversation, Paper, SearchSession, SessionPaper
from app.schemas.chat import SessionDetail
from app.schemas.paper import PaperBrief


async def get_owned_session(
    db: AsyncSession,
    *,
    session_id: int,
    user_id: int,
) -> SearchSession:
    session = await db.scalar(
        select(SearchSession).where(
            SearchSession.id == session_id,
            SearchSession.user_id == user_id,
        )
    )
    if session is None:
        raise AppError(404, "session_not_found", "会话不存在")
    return session


async def get_session_detail(
    db: AsyncSession,
    session: SearchSession,
) -> SessionDetail:
    conversation_id = await db.scalar(
        select(Conversation.id).where(
            Conversation.user_id == session.user_id,
            Conversation.scope_type == ChatScope.session.value,
            Conversation.scope_id == session.id,
        )
    )
    if conversation_id is None:
        raise AppError(500, "internal_error", "会话数据不完整")

    rows = (
        await db.execute(
            select(Paper, SessionPaper.rank)
            .join(SessionPaper, SessionPaper.paper_id == Paper.id)
            .where(SessionPaper.session_id == session.id)
            .order_by(SessionPaper.rank.asc())
        )
    ).all()
    papers = [PaperBrief.model_validate(paper) for paper, _ in rows]
    return SessionDetail(
        id=session.id,
        query=session.query,
        query_type=QueryType(session.query_type),
        paper_count=session.paper_count,
        evidence_count=session.evidence_count,
        related_repo_count=0,
        has_evidence=session.evidence_count > 0,
        system_message=session.system_message,
        conversation_id=conversation_id,
        papers=papers,
        created_at=session.created_at,
    )
