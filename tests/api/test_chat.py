from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from app.api.v1.endpoints.sessions import _chat_events
from app.core.ai import REFUSAL_TEXT
from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.exceptions import AppError
from app.db import get_session_factory
from app.models import Conversation, Message, Paper, PaperChunk
from tests.api.conftest import login
from tests.fakes import FakeLLMClient


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data += line.removeprefix("data: ")
        if event:
            events.append((event, json.loads(data)))
    return events


async def _create_paper(content: str) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(
            title="Chat paper",
            authors=["Author"],
            status="ready",
            is_archived=False,
        )
        db.add(paper)
        await db.flush()
        db.add(
            PaperChunk(
                paper_id=paper.id,
                chunk_index=0,
                page=1,
                content=content,
                char_count=len(content),
            )
        )
        await db.commit()
        await db.refresh(paper)
        return paper


async def _create_session(
    client,
    *,
    query: str = "transformer",
    content: str = "transformer evidence",
):
    await _create_paper(content)
    response = await client.post("/api/v1/search", json={"query": query})
    assert response.status_code == 201, response.text
    return response.json()


async def _message_rows(conversation_id: int) -> list[Message]:
    async with get_session_factory()() as db:
        return list(
            (
                await db.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id.asc())
                )
            ).all()
        )


async def _conversation_for(session_id: int, user_id: int) -> Conversation:
    async with get_session_factory()() as db:
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.scope_type == ChatScope.session.value,
                Conversation.scope_id == session_id,
                Conversation.user_id == user_id,
            )
        )
        assert conversation is not None
        return conversation


def _fake(monkeypatch, **kwargs) -> FakeLLMClient:
    fake = FakeLLMClient(**kwargs)
    monkeypatch.setattr("app.api.v1.endpoints.sessions.get_llm_client", lambda: fake)
    return fake


async def test_session_chat_meta_then_citation_then_token_then_done(
    reader_client,
    reader_user,
    monkeypatch,
) -> None:
    session = await _create_session(reader_client)
    fake = _fake(monkeypatch, stream_parts=["结论 ", "[1]"])

    response = await reader_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "解释一下"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _ in events] == ["meta", "citation", "token", "done"]
    assert events[0][1]["scope"] == "session"
    assert events[0][1]["scope_id"] == session["id"]
    assert events[0][1]["conversation_id"] == session["conversation_id"]
    assert events[0][1]["user_message_id"] > 0
    assert events[1][1]["index"] == 1
    assert events[1][1]["paper_id"] > 0
    assert events[2][1]["text"] == "结论 [1]"
    assert events[3][1]["status"] == "complete"
    assert events[3][1]["assistant_message_id"] > 0
    assert fake.stream_calls

    messages = await _message_rows(session["conversation_id"])
    assert messages[-2].role == MessageRole.user.value
    assert messages[-2].content == "解释一下"
    assert messages[-1].role == MessageRole.assistant.value
    assert messages[-1].content == "结论 [1]"
    assert messages[-1].status == MessageStatus.complete.value
    assert messages[-1].citations[0]["index"] == 1


async def test_session_chat_requires_owner(
    reader_client,
    reader_user,
    make_client,
    user_factory,
    monkeypatch,
) -> None:
    session = await _create_session(reader_client)
    _fake(monkeypatch)

    other = await user_factory(email="chat-other@example.com")
    other_client = await make_client()
    await login(other_client, other.email)
    response = await other_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"

    admin = await user_factory(email="chat-admin@example.com", role="admin")
    admin_client = await make_client()
    await login(admin_client, admin.email)
    response = await admin_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )
    assert response.status_code == 404


async def test_empty_session_chat_409_without_message(reader_client, monkeypatch) -> None:
    response = await reader_client.post("/api/v1/search", json={"query": "no-such-chat"})
    session = response.json()
    fake = _fake(monkeypatch)

    before = await _message_rows(session["conversation_id"])
    response = await reader_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_evidence"
    assert fake.stream_calls == []
    assert len(await _message_rows(session["conversation_id"])) == len(before)


async def test_session_chat_uses_snapshot_not_global_catalog(
    reader_client,
    monkeypatch,
) -> None:
    session = await _create_session(reader_client)
    await _create_paper("new unrelated catalog content")
    fake = _fake(monkeypatch, stream_parts=["结论 [1]"])

    response = await reader_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )

    assert response.status_code == 200
    system = fake.stream_calls[0][0]["content"]
    assert "Chat paper" in system
    assert "new unrelated" not in system


async def test_session_chat_retries_once_then_refuses(reader_client, monkeypatch) -> None:
    session = await _create_session(reader_client)
    fake = _fake(
        monkeypatch,
        stream_sequences=[["没有引用"], ["仍然没有引用"]],
    )

    response = await reader_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )

    events = _parse_sse(response.text)
    assert [event for event, _ in events] == ["meta", "token", "done"]
    token = next(data for event, data in events if event == "token")
    assert token["text"] == REFUSAL_TEXT
    assert len(fake.stream_calls) == 2
    assert "上一次回答没有通过引用校验" in fake.stream_calls[1][0]["content"]
    messages = await _message_rows(session["conversation_id"])
    assert messages[-1].content == REFUSAL_TEXT
    assert messages[-1].citations == []
    assert messages[-1].status == MessageStatus.complete.value


async def test_session_chat_invalid_citation_sanitized(reader_client, monkeypatch) -> None:
    session = await _create_session(reader_client)
    _fake(
        monkeypatch,
        stream_sequences=[["无效 [9]"], ["有效 [9] [1]"]],
    )

    response = await reader_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )

    events = _parse_sse(response.text)
    assert [event for event, _ in events] == ["meta", "citation", "token", "done"]
    assert events[1][1]["index"] == 1
    token = next(data for event, data in events if event == "token")
    assert token["text"] == "有效  [1]"


async def test_session_chat_stream_error_emits_error_and_saves_error(
    reader_client,
    monkeypatch,
) -> None:
    session = await _create_session(reader_client)
    _fake(
        monkeypatch,
        stream_error=AppError(502, "llm_failed", "模型失败"),
    )

    response = await reader_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )

    events = _parse_sse(response.text)
    assert [event for event, _ in events] == ["meta", "error"]
    assert events[1][1]["code"] == "llm_failed"
    messages = await _message_rows(session["conversation_id"])
    assert messages[-1].role == MessageRole.assistant.value
    assert messages[-1].status == MessageStatus.error.value
    assert all(message.role != MessageRole.assistant.value for message in messages[:-1])


async def test_session_chat_missing_key_503_without_message(reader_client, monkeypatch) -> None:
    session = await _create_session(reader_client)
    before = await _message_rows(session["conversation_id"])

    def _missing():
        raise AppError(503, "llm_unavailable", "AI 服务未配置")

    monkeypatch.setattr("app.api.v1.endpoints.sessions.get_llm_client", _missing)
    response = await reader_client.post(
        f"/api/v1/sessions/{session['id']}/chat/stream",
        json={"message": "hi"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"
    assert len(await _message_rows(session["conversation_id"])) == len(before)


async def test_session_chat_validation_extra_field(reader_client, monkeypatch) -> None:
    session = await _create_session(reader_client)
    before = await _message_rows(session["conversation_id"])
    _fake(monkeypatch)

    for payload in ({"message": ""}, {"message": "   "}, {"message": "hi", "extra": 1}):
        response = await reader_client.post(
            f"/api/v1/sessions/{session['id']}/chat/stream",
            json=payload,
        )
        assert response.status_code == 422

    assert len(await _message_rows(session["conversation_id"])) == len(before)


class _BlockingClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream(self, messages):
        yield "部分"
        self.started.set()
        await asyncio.sleep(30)


async def _wait_for_status(
    conversation_id: int,
    status: MessageStatus,
    *,
    timeout: float = 2.0,
) -> Message:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        rows = await _message_rows(conversation_id)
        if (
            rows
            and rows[-1].role == MessageRole.assistant.value
            and rows[-1].status == status.value
        ):
            return rows[-1]
        await asyncio.sleep(0.05)
    raise AssertionError(f"no assistant message with status {status.value}")


async def test_session_chat_stop_saves_stopped(reader_client, reader_user) -> None:
    session = await _create_session(reader_client)
    conversation = await _conversation_for(session["id"], reader_user.id)
    client = _BlockingClient()
    events = _chat_events(
        client=client,
        messages=[],
        retry_messages=[],
        evidence=[],
        conversation_id=conversation.id,
        user_message_id=1,
        scope=ChatScope.session,
        scope_id=session["id"],
    )

    first = await anext(events)
    assert first.startswith("event: meta")
    pending = asyncio.create_task(anext(events))
    await asyncio.wait_for(client.started.wait(), timeout=1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await events.aclose()

    stopped = await _wait_for_status(conversation.id, MessageStatus.stopped)
    assert stopped.content == "部分"
    assert stopped.citations == []
