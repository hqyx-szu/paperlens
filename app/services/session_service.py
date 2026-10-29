from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.prompts import Citation
from app.core.chat import ChatScope, QueryType
from app.core.exceptions import AppError
from app.models import Conversation, Message, Paper, SearchSession, SessionPaper
from app.schemas.chat import MessagePage, MessageRead, SessionDetail, SessionPage, SessionSummary
from app.schemas.paper import PaperBrief
from app.services.repo_related import count_related_repos


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
    related_repo_count = await count_related_repos(
        db,
        session=session,
        user_id=session.user_id,
    )
    return SessionDetail(
        id=session.id,
        query=session.query,
        query_type=QueryType(session.query_type),
        paper_count=session.paper_count,
        evidence_count=session.evidence_count,
        related_repo_count=related_repo_count,
        has_evidence=session.evidence_count > 0,
        system_message=session.system_message,
        conversation_id=conversation_id,
        papers=papers,
        created_at=session.created_at,
    )


async def list_sessions(
    db: AsyncSession,
    *,
    user_id: int,
    page: int,
    page_size: int,
) -> SessionPage:
    total = (
        await db.scalar(
            select(func.count()).select_from(SearchSession).where(SearchSession.user_id == user_id)
        )
        or 0
    )
    sessions = (
        await db.scalars(
            select(SearchSession)
            .where(SearchSession.user_id == user_id)
            .order_by(SearchSession.created_at.desc(), SearchSession.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return SessionPage(
        items=[
            SessionSummary(
                id=session.id,
                query=session.query,
                query_type=QueryType(session.query_type),
                paper_count=session.paper_count,
                evidence_count=session.evidence_count,
                created_at=session.created_at,
            )
            for session in sessions
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_session_conversation(
    db: AsyncSession,
    *,
    session_id: int,
    user_id: int,
) -> Conversation | None:
    return await db.scalar(
        select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.scope_type == ChatScope.session.value,
            Conversation.scope_id == session_id,
        )
    )


async def list_messages(
    db: AsyncSession,
    *,
    conversation_id: int,
    limit: int,
    before_id: int | None,
) -> MessagePage:
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    rows = list((await db.scalars(stmt.order_by(Message.id.desc()).limit(limit + 1))).all())
    has_more = len(rows) > limit
    messages = rows[:limit]
    items = [
        MessageRead(
            id=message.id,
            role=message.role,
            content=message.content,
            citations=[Citation.model_validate(citation) for citation in message.citations or []],
            status=message.status,
            created_at=message.created_at,
        )
        for message in messages
    ]
    return MessagePage(
        items=items,
        has_more=has_more,
        next_before_id=messages[-1].id if has_more and messages else None,
    )
