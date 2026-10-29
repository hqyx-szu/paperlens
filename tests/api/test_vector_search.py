from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import ChunkEmbedding, Paper, PaperChunk
from app.services import embeddings as embeddings_service
from app.services.embeddings import embed_missing_chunks, embed_paper_chunks
from tests.api.conftest import login
from tests.fakes import (
    FakeEmbeddingProvider,
    FakeLLMClient,
    final_response,
    response_with_tool_call,
)

REPOS = "/api/v1/repos"


def _use_fake_embeddings(monkeypatch) -> FakeEmbeddingProvider:
    provider = FakeEmbeddingProvider()
    monkeypatch.setattr(embeddings_service, "get_embedding_provider", lambda: provider)
    return provider


async def _paper_with_chunk(title: str, content: str) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(title=title, authors=["A"], status=PaperStatus.ready.value)
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


async def _embedding_count() -> int:
    async with get_session_factory()() as db:
        return await db.scalar(select(func.count()).select_from(ChunkEmbedding)) or 0


async def test_embed_missing_chunks_backfill(monkeypatch) -> None:
    _use_fake_embeddings(monkeypatch)
    paper = await _paper_with_chunk("Cat paper", "cat database notes")

    written = await embed_missing_chunks()
    again = await embed_missing_chunks()

    assert written == 1
    assert again == 0
    assert await _embedding_count() == 1

    async with get_session_factory()() as db:
        chunk_id = await db.scalar(select(PaperChunk.id).where(PaperChunk.paper_id == paper.id))
        assert chunk_id is not None
        row = await db.get(ChunkEmbedding, chunk_id)
        assert row is not None
        assert row.model == "fake-embedding"
        assert row.dim == 8
        assert len(row.vector) == 8 * 4


async def test_vector_search_recalls_cross_language_chunk(
    reader_client: AsyncClient,
    monkeypatch,
) -> None:
    _use_fake_embeddings(monkeypatch)
    cat_paper = await _paper_with_chunk("数据库论文", "cat database design")
    other_paper = await _paper_with_chunk("模型论文", "transformer attention")
    assert await embed_paper_chunks(cat_paper.id) == 1
    assert await embed_paper_chunks(other_paper.id) == 1

    response = await reader_client.post("/api/v1/search", json={"query": "猫"})

    assert response.status_code == 201
    body = response.json()
    assert body["has_evidence"] is True
    assert [paper["id"] for paper in body["papers"]] == [cat_paper.id]
    assert body["evidence_count"] >= 1


async def test_vector_disabled_keeps_keyword_behavior(reader_client: AsyncClient) -> None:
    cat_paper = await _paper_with_chunk("数据库论文", "cat database design")
    assert await embed_paper_chunks(cat_paper.id) == 0
    assert await _embedding_count() == 0

    response = await reader_client.post("/api/v1/search", json={"query": "猫"})

    assert response.status_code == 201
    assert response.json()["has_evidence"] is False


async def test_vector_search_respects_visibility(
    reader_client: AsyncClient,
    monkeypatch,
) -> None:
    _use_fake_embeddings(monkeypatch)
    archived = await _paper_with_chunk("归档猫论文", "cat archived notes")
    pending = await _paper_with_chunk("处理中猫论文", "cat pending notes")
    assert await embed_paper_chunks(archived.id) == 1
    assert await embed_paper_chunks(pending.id) == 1
    async with get_session_factory()() as db:
        await db.execute(update(Paper).where(Paper.id == archived.id).values(is_archived=True))
        await db.execute(
            update(Paper).where(Paper.id == pending.id).values(status=PaperStatus.parsing.value)
        )
        await db.commit()

    response = await reader_client.post("/api/v1/search", json={"query": "猫"})

    body = response.json()
    assert body["has_evidence"] is False
    assert body["papers"] == []


async def test_repo_agent_uses_vector_recall(
    make_client,
    reader_user,
    monkeypatch,
) -> None:
    _use_fake_embeddings(monkeypatch)
    paper = await _paper_with_chunk("仓库猫论文", "cat behaviour survey")
    assert await embed_paper_chunks(paper.id) == 1

    client = await make_client()
    await login(client, reader_user.email)
    repo = (await client.post(REPOS, json={"name": "Cat repo", "visibility": "public"})).json()
    assert (
        await client.post(f"{REPOS}/{repo['id']}/papers", json={"paper_ids": [paper.id]})
    ).status_code == 200

    fake = FakeLLMClient(
        responses=[
            response_with_tool_call("search_repo", json.dumps({"query": "猫"})),
            final_response("ok"),
        ],
        stream_parts=["结论 [1]"],
    )
    monkeypatch.setattr("app.api.v1.endpoints.repos.get_llm_client", lambda: fake)

    response = await client.post(f"{REPOS}/{repo['id']}/chat/stream", json={"message": "猫 相关"})

    assert response.status_code == 200
    tool_messages = [message for message in fake.calls[1][0] if message.get("role") == "tool"]
    assert tool_messages
    tool_content = tool_messages[-1]["content"]
    assert "cat behaviour survey" in tool_content
    assert "paper_id" in tool_content
    assert "repo_id" not in tool_content


async def test_vector_search_ignores_documents_without_embeddings(
    reader_client: AsyncClient,
    monkeypatch,
) -> None:
    _use_fake_embeddings(monkeypatch)
    embedded = await _paper_with_chunk("已索引", "cat indexed")
    assert await embed_paper_chunks(embedded.id) == 1
    await _paper_with_chunk("未索引", "cat not indexed")

    response = await reader_client.post("/api/v1/search", json={"query": "猫"})

    body = response.json()
    assert [paper["id"] for paper in body["papers"]] == [embedded.id]
