from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.core.ai import REFUSAL_TEXT, sse
from app.core.chat import ChatScope, MessageStatus
from app.core.exceptions import AppError
from app.services.chat import (
    ChatEvidence,
    collect_stream,
    save_assistant_message,
    validate_answer_citations,
)

logger = logging.getLogger("paperlens.chat_stream")

TOKEN_SIZE = 32


def token_chunks(answer: str, size: int = TOKEN_SIZE) -> list[str]:
    return [answer[index : index + size] for index in range(0, len(answer), size)]


async def persist_quietly(
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


def meta_event(
    *,
    conversation_id: int,
    user_message_id: int,
    scope: ChatScope,
    scope_id: int,
) -> str:
    return sse(
        "meta",
        {
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "scope": scope.value,
            "scope_id": scope_id,
        },
    )


async def answer_events(
    *,
    client: Any,
    messages: list[dict[str, str]],
    retry_messages: list[dict[str, str]],
    evidence: list[ChatEvidence],
    conversation_id: int,
) -> AsyncIterator[str]:
    """从第一次模型调用开始：缓冲 → 校验引用 → citation/token/done；不含 meta 事件。"""
    try:
        raw = await collect_stream(client, messages)
        answer, citations = validate_answer_citations(raw, evidence)
    except asyncio.CancelledError as exc:
        await persist_quietly(
            conversation_id=conversation_id,
            content=getattr(exc, "partial", ""),
            status=MessageStatus.stopped,
            wait=False,
        )
        raise
    except GeneratorExit as exc:
        await persist_quietly(
            conversation_id=conversation_id,
            content=getattr(exc, "partial", ""),
            status=MessageStatus.stopped,
            wait=False,
        )
        raise
    except AppError as exc:
        await persist_quietly(
            conversation_id=conversation_id,
            content=getattr(exc, "partial", ""),
            status=MessageStatus.error,
        )
        yield sse("error", {"code": exc.code, "message": exc.message})
        return
    except Exception:
        logger.exception("unexpected chat failure")
        await persist_quietly(
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
            await persist_quietly(
                conversation_id=conversation_id,
                content=getattr(exc, "partial", ""),
                status=MessageStatus.stopped,
                wait=False,
            )
            raise
        except GeneratorExit as exc:
            await persist_quietly(
                conversation_id=conversation_id,
                content=getattr(exc, "partial", ""),
                status=MessageStatus.stopped,
                wait=False,
            )
            raise
        except AppError as exc:
            await persist_quietly(
                conversation_id=conversation_id,
                content=getattr(exc, "partial", ""),
                status=MessageStatus.error,
            )
            yield sse("error", {"code": exc.code, "message": exc.message})
            return
        except Exception:
            logger.exception("unexpected chat retry failure")
            await persist_quietly(
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
    for chunk in token_chunks(answer):
        yield sse("token", {"text": chunk})
    yield sse("done", {"assistant_message_id": assistant_id, "status": "complete"})


async def chat_events(
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
    """会话/单篇对话：meta → answer_events（D3 行为保持不变）。"""
    yield meta_event(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        scope=scope,
        scope_id=scope_id,
    )
    async for event in answer_events(
        client=client,
        messages=messages,
        retry_messages=retry_messages,
        evidence=evidence,
        conversation_id=conversation_id,
    ):
        yield event
