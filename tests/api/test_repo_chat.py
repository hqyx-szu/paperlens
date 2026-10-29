from __future__ import annotations

import asyncio
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.ai import REFUSAL_TEXT
from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.exceptions import AppError
from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Conversation, Message, Paper, PaperChunk
from app.services.repo_chat import repo_chat_events
from tests.api.conftest import login
from tests.fakes import FakeLLMClient, final_response, response_with_tool_call

REPOS = "/api/v1/repos"


async def _client_for(make_client, user) -> AsyncClient:
    client = await make_client()
    await login(client, user.email)
    return client


async def _create_repo(client: AsyncClient, name: str = "Repo", **extra: object) -> dict:
    payload: dict[str, object] = {"name": name, **extra}
    response = await client.post(REPOS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _paper_with_chunk(
    title: str = "Repo paper",
    content: str = "transformer attention evidence",
    *,
    with_chunk: bool = True,
) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(title=title, authors=["A"], status=PaperStatus.ready.value)
        db.add(paper)
        await db.flush()
        if with_chunk:
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


async def _add_paper(client: AsyncClient, repo_id: int, paper_id: int) -> None:
    response = await client.post(f"{REPOS}/{repo_id}/papers", json={"paper_ids": [paper_id]})
    assert response.status_code == 200, response.text


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


def _patch_llm(monkeypatch, client: FakeLLMClient) -> FakeLLMClient:
    monkeypatch.setattr("app.api.v1.endpoints.repos.get_llm_client", lambda: client)
    return client


def _tool_call(query: str = "transformer"):
    return response_with_tool_call("search_repo", json.dumps({"query": query}))


async def _rows(conversation_id: int) -> list[Message]:
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


async def _repo_setup(make_client, user, **repo_extra: object) -> tuple[AsyncClient, dict, Paper]:
    client = await _client_for(make_client, user)
    repo = await _create_repo(client, **repo_extra)
    paper = await _paper_with_chunk()
    await _add_paper(client, repo["id"], paper.id)
    return client, repo, paper


async def test_repo_chat_requires_login(client: AsyncClient) -> None:
    response = await client.post(f"{REPOS}/1/chat/stream", json={"message": "hi"})

    assert response.status_code == 401


async def test_repo_chat_empty_repo_returns_409(make_client, reader_user, monkeypatch) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    _patch_llm(monkeypatch, FakeLLMClient())

    response = await client.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "hi"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "repo_empty"


async def test_repo_chat_without_chunks_returns_409(make_client, reader_user, monkeypatch) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    paper = await _paper_with_chunk(with_chunk=False)
    await _add_paper(client, repo["id"], paper.id)
    _patch_llm(monkeypatch, FakeLLMClient())

    response = await client.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "hi"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "repo_empty"


async def test_repo_chat_llm_unavailable_before_stream(
    make_client,
    reader_user,
    monkeypatch,
) -> None:
    client, repo, _ = await _repo_setup(make_client, reader_user, visibility="public")

    def _raise() -> None:
        raise AppError(503, "llm_unavailable", "AI 服务未配置或暂不可用")

    monkeypatch.setattr("app.api.v1.endpoints.repos.get_llm_client", _raise)

    response = await client.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "hi"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"


async def test_repo_chat_sse_order_and_citation(make_client, reader_user, monkeypatch) -> None:
    client, repo, paper = await _repo_setup(make_client, reader_user, visibility="public")
    fake = _patch_llm(
        monkeypatch,
        FakeLLMClient(
            responses=[_tool_call("transformer"), final_response("已找到证据")],
            stream_parts=["方法见 [1]。"],
        ),
    )

    response = await client.post(
        f"{REPOS}/{repo['id']}/chat/stream", json={"message": "方法是什么"}
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0][0] == "meta"
    assert events[0][1]["scope"] == "repo"
    assert events[0][1]["scope_id"] == repo["id"]
    names = [name for name, _ in events]
    assert names.index("meta") < names.index("citation") < names.index("token")
    assert names[-1] == "done"
    assert "error" not in names

    citation = next(data for name, data in events if name == "citation")
    assert citation["index"] == 1
    assert citation["paper_id"] == paper.id
    assert citation["page"] == 1

    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert tokens == "方法见 [1]。"
    assert events[-1][1]["status"] == "complete"

    # 工具阶段只暴露 search_repo，且参数里没有 repo_id。
    tools = fake.calls[0][1]
    assert tools is not None
    spec = tools[0]["function"]
    assert spec["name"] == "search_repo"
    assert "repo_id" not in json.dumps(spec["parameters"])

    rows = await _rows(events[0][1]["conversation_id"])
    assert [row.role for row in rows] == [MessageRole.user.value, MessageRole.assistant.value]
    assert rows[-1].status == MessageStatus.complete.value
    assert rows[-1].citations[0]["paper_id"] == paper.id


async def test_repo_chat_does_not_use_papers_outside_repo(
    make_client,
    reader_user,
    monkeypatch,
) -> None:
    client, repo, inside = await _repo_setup(make_client, reader_user, visibility="public")
    outside = await _paper_with_chunk("Outside paper", "transformer outside content")
    _patch_llm(
        monkeypatch,
        FakeLLMClient(
            responses=[_tool_call("transformer"), final_response("ok")],
            stream_parts=["答案 [1]"],
        ),
    )

    response = await client.post(
        f"{REPOS}/{repo['id']}/chat/stream", json={"message": "transformer"}
    )

    events = _parse_sse(response.text)
    citations = [data for name, data in events if name == "citation"]
    assert citations
    assert {citation["paper_id"] for citation in citations} == {inside.id}
    assert outside.id != inside.id


async def test_repo_chat_can_use_archived_paper(make_client, reader_user, monkeypatch) -> None:
    client, repo, paper = await _repo_setup(make_client, reader_user, visibility="public")
    async with get_session_factory()() as db:
        await db.execute(update(Paper).where(Paper.id == paper.id).values(is_archived=True))
        await db.commit()
    _patch_llm(
        monkeypatch,
        FakeLLMClient(
            responses=[_tool_call("transformer"), final_response("ok")],
            stream_parts=["答案 [1]"],
        ),
    )

    response = await client.post(
        f"{REPOS}/{repo['id']}/chat/stream", json={"message": "transformer"}
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [data for name, data in events if name == "citation"][0]["paper_id"] == paper.id


async def test_repo_chat_private_repo_hidden(
    make_client, reader_user, user_factory, monkeypatch
) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    _patch_llm(monkeypatch, FakeLLMClient())

    response = await reader.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "hi"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repo_not_found"


async def test_repo_chat_refusal_when_tool_finds_nothing(
    make_client,
    reader_user,
    monkeypatch,
) -> None:
    client, repo, _ = await _repo_setup(make_client, reader_user, visibility="public")
    fake = _patch_llm(
        monkeypatch,
        FakeLLMClient(
            responses=[_tool_call("zzz-not-found"), final_response("ok")],
            stream_parts=["没有找到依据"],
        ),
    )

    response = await client.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "问题"})

    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert "citation" not in names
    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert REFUSAL_TEXT in tokens
    assert len(fake.stream_calls) == 2
    assert events[-1][0] == "done"


async def test_repo_chat_tool_loop_exceeded(make_client, reader_user, monkeypatch) -> None:
    client, repo, _ = await _repo_setup(make_client, reader_user, visibility="public")
    _patch_llm(
        monkeypatch,
        FakeLLMClient(responses=[_tool_call() for _ in range(10)]),
    )

    response = await client.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "hi"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "error"
    assert events[-1][1]["code"] == "tool_loop_exceeded"

    rows = await _rows(events[0][1]["conversation_id"])
    assert [row.role for row in rows] == [MessageRole.user.value, MessageRole.assistant.value]
    assert rows[-1].status == MessageStatus.error.value


async def test_repo_messages_only_returns_own_conversation(
    make_client,
    reader_user,
    user_factory,
    monkeypatch,
) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner, visibility="public")
    paper = await _paper_with_chunk()
    await _add_paper(owner, repo["id"], paper.id)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    _patch_llm(
        monkeypatch,
        FakeLLMClient(
            responses=[_tool_call("transformer"), final_response("ok")],
            stream_parts=["答案 [1]"],
        ),
    )
    assert (
        await reader.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "问题"})
    ).status_code == 200

    reader_messages = (await reader.get(f"{REPOS}/{repo['id']}/messages")).json()
    owner_messages = (await owner.get(f"{REPOS}/{repo['id']}/messages")).json()

    # 消息游标分页 items 按 id DESC（最新在前）。
    assert [item["role"] for item in reader_messages["items"]] == ["assistant", "user"]
    assert owner_messages == {"items": [], "has_more": False, "next_before_id": None}


async def test_repo_messages_pagination(make_client, reader_user, monkeypatch) -> None:
    client, repo, _ = await _repo_setup(make_client, reader_user, visibility="public")
    _patch_llm(
        monkeypatch,
        FakeLLMClient(
            responses=[
                _tool_call("transformer"),
                final_response("ok"),
                _tool_call("transformer"),
                final_response("ok"),
            ],
            stream_parts=["答案 [1]"],
        ),
    )
    for question in ("第一个问题", "第二个问题"):
        assert (
            await client.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": question})
        ).status_code == 200

    page = (await client.get(f"{REPOS}/{repo['id']}/messages", params={"limit": 2})).json()
    older = (
        await client.get(
            f"{REPOS}/{repo['id']}/messages",
            params={"limit": 2, "before_id": page["next_before_id"]},
        )
    ).json()

    assert len(page["items"]) == 2
    assert page["has_more"] is True
    assert [item["id"] for item in page["items"]] == sorted(
        (item["id"] for item in page["items"]),
        reverse=True,
    )
    assert len(older["items"]) == 2
    assert older["has_more"] is False
    assert older["next_before_id"] is None


class _BlockingClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def complete(self, messages, tools=None):
        return final_response("ok")

    async def stream(self, messages):
        yield "部分"
        self.started.set()
        await asyncio.sleep(30)


async def _conversation_for(repo_id: int, user_id: int) -> Conversation:
    async with get_session_factory()() as db:
        conversation = Conversation(
            user_id=user_id,
            scope_type=ChatScope.repo.value,
            scope_id=repo_id,
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        return conversation


async def _wait_for_status(
    conversation_id: int,
    status: MessageStatus,
    *,
    timeout: float = 2.0,
) -> Message:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        rows = await _rows(conversation_id)
        if (
            rows
            and rows[-1].role == MessageRole.assistant.value
            and rows[-1].status == status.value
        ):
            return rows[-1]
        await asyncio.sleep(0.05)
    raise AssertionError(f"no assistant message with status {status.value}")


async def test_repo_chat_stop_saves_stopped(make_client, reader_user) -> None:
    client, repo, _ = await _repo_setup(make_client, reader_user, visibility="public")
    conversation = await _conversation_for(repo["id"], reader_user.id)
    blocking = _BlockingClient()
    events = repo_chat_events(
        client=blocking,
        repo_id=repo["id"],
        conversation_id=conversation.id,
        user_message_id=1,
        history=[],
        question="问题",
    )

    first = await anext(events)
    assert first.startswith("event: meta")
    pending = asyncio.create_task(anext(events))
    await asyncio.wait_for(blocking.started.wait(), timeout=2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await events.aclose()

    stopped = await _wait_for_status(conversation.id, MessageStatus.stopped)
    assert stopped.content == "部分"
    assert stopped.citations == []
