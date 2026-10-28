from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.ai import REFUSAL_TEXT, get_llm_client, sse, sse_response, with_heartbeat
from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.exceptions import AppError
from app.db import get_db
from app.models import Message, User
from app.schemas.chat import ChatRequest, MessagePage, SessionDetail, SessionPage
from app.services.chat import (
    ChatEvidence,
    build_chat_messages,
    collect_stream,
    get_or_create_conversation,
    load_session_evidence,
    save_assistant_message,
    validate_answer_citations,
)
from app.services.session_service import (
    get_owned_session,
    get_session_conversation,
    get_session_detail,
    list_messages,
    list_sessions,
)

logger = logging.getLogger("paperlens.session_chat")

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


def _token_chunks(answer: str, size: int = 32) -> list[str]:
    return [answer[index : index + size] for index in range(0, len(answer), size)]


async def _persist_quietly(
    *,
    conversation_id: int,
    content: str,
    status: MessageStatus,
    wait: bool = True,
) -> None:
    task = asyncio.create_task(
        save_assistant_message(
            conversation_id=conversation_id,
            content=content,
            status=status,
        )
    )
    if not wait:
        return
    try:
        await asyncio.shield(task)
    except BaseException:
        logger.exception("failed to persist assistant message")


async def _chat_events(
    *,
    client: Any,
    messages: list[dict[str, str]],
    retry_messages: list[dict[str, str]],
    evidence: list[ChatEvidence],
    conversation_id: int,
    user_message_id: int,
    scope: ChatScope,
    scope_id: int,
) -> AsyncIterator[str]:
    yield sse(
        "meta",
        {
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "scope": scope.value,
            "scope_id": scope_id,
        },
    )

    try:
        raw = await collect_stream(client, messages)
        answer, citations = validate_answer_citations(raw, evidence)
    except asyncio.CancelledError as exc:
        await _persist_quietly(
            conversation_id=conversation_id,
            content=getattr(exc, "partial", ""),
            status=MessageStatus.stopped,
            wait=False,
        )
        raise
    except GeneratorExit as exc:
        await _persist_quietly(
            conversation_id=conversation_id,
            content=getattr(exc, "partial", ""),
            status=MessageStatus.stopped,
            wait=False,
        )
        raise
    except AppError as exc:
        await _persist_quietly(
            conversation_id=conversation_id,
            content=getattr(exc, "partial", ""),
            status=MessageStatus.error,
        )
        yield sse("error", {"code": exc.code, "message": exc.message})
        return
    except Exception:
        logger.exception("unexpected chat failure")
        await _persist_quietly(
            conversation_id=conversation_id,
            content="",
            status=MessageStatus.error,
        )
        yield sse("error", {"code": "llm_failed", "message": "AI 生成失败，请稍后重试"})
        return

    if REFUSAL_TEXT not in raw.strip() and not citations:
        try:
            raw = await collect_stream(client, retry_messages)
            answer, citations = validate_answer_citations(raw, evidence)
        except asyncio.CancelledError as exc:
            await _persist_quietly(
                conversation_id=conversation_id,
                content=getattr(exc, "partial", ""),
                status=MessageStatus.stopped,
                wait=False,
            )
            raise
        except GeneratorExit as exc:
            await _persist_quietly(
                conversation_id=conversation_id,
                content=getattr(exc, "partial", ""),
                status=MessageStatus.stopped,
                wait=False,
            )
            raise
        except AppError as exc:
            await _persist_quietly(
                conversation_id=conversation_id,
                content=getattr(exc, "partial", ""),
                status=MessageStatus.error,
            )
            yield sse("error", {"code": exc.code, "message": exc.message})
            return
        except Exception:
            logger.exception("unexpected chat retry failure")
            await _persist_quietly(
                conversation_id=conversation_id,
                content="",
                status=MessageStatus.error,
            )
            yield sse("error", {"code": "llm_failed", "message": "AI 生成失败，请稍后重试"})
            return
        if not citations:
            answer = REFUSAL_TEXT

    try:
        assistant_id = await save_assistant_message(
            conversation_id=conversation_id,
            content=answer,
            status=MessageStatus.complete,
            citations=citations,
        )
    except Exception:
        logger.exception("failed to persist assistant reply")
        yield sse("error", {"code": "llm_failed", "message": "AI 生成失败，请稍后重试"})
        return

    for citation in citations:
        yield sse("citation", citation.model_dump())
    for chunk in _token_chunks(answer):
        yield sse("token", {"text": chunk})
    yield sse("done", {"assistant_message_id": assistant_id, "status": "complete"})


@router.post("/{session_id}/chat/stream")
async def session_chat(
    session_id: Annotated[int, Path(ge=1)],
    payload: ChatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    session = await get_owned_session(db, session_id=session_id, user_id=user.id)
    if session.evidence_count == 0:
        raise AppError(409, "no_evidence", "当前会话没有可用证据")

    evidence = await load_session_evidence(db, session_id=session.id)
    if not evidence:
        raise AppError(409, "no_evidence", "当前会话没有可用证据")

    client = get_llm_client()
    conversation = await get_or_create_conversation(
        db,
        user_id=user.id,
        scope=ChatScope.session,
        scope_id=session.id,
    )
    history = list(
        (
            await db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.role != MessageRole.system.value,
                )
                .order_by(Message.id.desc())
                .limit(12)
            )
        ).all()
    )[::-1]
    messages = build_chat_messages(
        scope=ChatScope.session,
        evidence=evidence,
        history=history,
        question=payload.message,
    )
    retry_messages = build_chat_messages(
        scope=ChatScope.session,
        evidence=evidence,
        history=history,
        question=payload.message,
        retry=True,
    )

    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.user.value,
        content=payload.message,
        status=MessageStatus.complete.value,
        citations=[],
    )
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)

    events = _chat_events(
        client=client,
        messages=messages,
        retry_messages=retry_messages,
        evidence=evidence,
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        scope=ChatScope.session,
        scope_id=session.id,
    )
    return sse_response(with_heartbeat(events))
