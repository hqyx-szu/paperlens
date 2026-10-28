from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from app.core.chat import ChatScope, MessageRole, QueryType
from app.core.papers import PaperStatus
from app.core.roles import UserRole
from app.db import get_session_factory
from app.models import (
    Conversation,
    Message,
    Paper,
    PaperChunk,
    PaperFile,
    SearchSession,
    SessionEvidence,
    SessionPaper,
)
from app.services.search import SearchResult, create_search_session, search_catalog
from tests.api.conftest import login
from tests.pdf_factory import make_pdf_bytes, upload


async def _create_paper(
    title: str,
    *,
    status: PaperStatus = PaperStatus.ready,
    archived: bool = False,
    chunks: Sequence[tuple[int | None, str]] = (),
    abstract: str | None = None,
) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(
            title=title,
            authors=[],
            abstract=abstract,
            status=status.value,
            is_archived=archived,
        )
        db.add(paper)
        await db.flush()
        for index, (page, content) in enumerate(chunks):
            db.add(
                PaperChunk(
                    paper_id=paper.id,
                    chunk_index=index,
                    page=page,
                    content=content,
                    char_count=len(content),
                )
            )
        await db.commit()
        await db.refresh(paper)
        return paper


async def _count_sessions() -> int:
    async with get_session_factory()() as db:
        return await db.scalar(select(func.count()).select_from(SearchSession)) or 0


async def _get_session(session_id: int) -> SearchSession | None:
    async with get_session_factory()() as db:
        return await db.get(SearchSession, session_id)


async def test_search_catalog_chunk_hits_prefer_content() -> None:
    content_paper = await _create_paper(
        "Attention paper",
        chunks=[(1, "The transformer architecture uses self attention.")],
    )
    title_only_paper = await _create_paper(
        "Transformer survey",
        chunks=[(1, "zzz qqq mmm")],
    )

    async with get_session_factory()() as db:
        result = await search_catalog(db, "transformer")

    assert [paper.id for paper in result.papers] == [content_paper.id]
    assert title_only_paper.id not in {paper.id for paper in result.papers}
    assert result.evidence
    assert all(hit.paper_id == content_paper.id for hit in result.evidence)
    assert "transformer" in result.evidence[0].content.lower()


async def test_search_catalog_excludes_processing_and_archived() -> None:
    ready = await _create_paper("ready", chunks=[(1, "unique ready content")])
    await _create_paper(
        "indexing",
        status=PaperStatus.indexing,
        chunks=[(1, "unique indexing content")],
    )
    await _create_paper(
        "failed",
        status=PaperStatus.failed,
        chunks=[(1, "unique failed content")],
    )
    await _create_paper(
        "archived",
        archived=True,
        chunks=[(1, "unique archived content")],
    )

    async with get_session_factory()() as db:
        result = await search_catalog(db, "unique")

    assert [paper.id for paper in result.papers] == [ready.id]


async def test_search_catalog_single_char_uses_like() -> None:
    paper = await _create_paper("single char", chunks=[(1, "alpha beta gamma")])

    async with get_session_factory()() as db:
        result = await search_catalog(db, "β")

    assert result.papers == []
    assert result.evidence == []

    async with get_session_factory()() as db:
        result = await search_catalog(db, "a")

    assert [item.id for item in result.papers] == [paper.id]
    assert result.evidence


async def test_search_catalog_metadata_fallback() -> None:
    paper = await _create_paper(
        "UniqueFallbackTitle",
        abstract="A metadata abstract",
        chunks=[
            (1, "zzz qqq vvv"),
            (2, "yyy ppp nnn"),
            (3, "xxx ooo rrr"),
        ],
    )

    async with get_session_factory()() as db:
        result = await search_catalog(db, "UniqueFallbackTitle")

    assert [item.id for item in result.papers] == [paper.id]
    assert [hit.content for hit in result.evidence] == [
        "zzz qqq vvv",
        "yyy ppp nnn",
    ]
    assert all(hit.quote == hit.content for hit in result.evidence)


async def test_search_catalog_empty_returns_empty() -> None:
    async with get_session_factory()() as db:
        result = await search_catalog(db, "no-such-term-anywhere")

    assert result.papers == []
    assert result.evidence == []


async def test_search_catalog_limits() -> None:
    for number in range(10):
        await _create_paper(
            f"paper-{number}",
            chunks=[(1, f"shared budget term {number}") for _ in range(3)],
        )

    async with get_session_factory()() as db:
        result = await search_catalog(db, "shared budget")

    assert len(result.papers) <= 8
    assert len(result.evidence) <= 20
    chunk_ids = [hit.chunk_id for hit in result.evidence]
    assert len(chunk_ids) == len(set(chunk_ids))
    paper_ids = {paper.id for paper in result.papers}
    assert {hit.paper_id for hit in result.evidence} <= paper_ids


async def test_create_search_session_snapshot(user_factory) -> None:
    user = await user_factory()
    paper = await _create_paper("snapshot", chunks=[(1, "snapshot alpha evidence")])

    async with get_session_factory()() as db:
        result = await search_catalog(db, "alpha")

    async with get_session_factory()() as db:
        session = await create_search_session(
            db,
            user_id=user.id,
            query=" alpha ",
            query_type=QueryType.text,
            result=result,
        )
        session_id = session.id

    async with get_session_factory()() as db:
        stored_session = await db.get(SearchSession, session_id)
        assert stored_session is not None
        assert stored_session.query == "alpha"
        assert stored_session.paper_count == 1
        assert stored_session.evidence_count == 1
        assert stored_session.system_message

        stored_papers = (
            await db.scalars(select(SessionPaper).where(SessionPaper.session_id == session_id))
        ).all()
        assert [(item.paper_id, item.rank) for item in stored_papers] == [(paper.id, 1)]

        stored_evidence = (
            await db.scalars(
                select(SessionEvidence).where(SessionEvidence.session_id == session_id)
            )
        ).all()
        assert len(stored_evidence) == 1
        assert stored_evidence[0].rank == 1
        assert stored_evidence[0].paper_id == paper.id
        assert stored_evidence[0].quote in result.evidence[0].content

        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.user_id == user.id,
                Conversation.scope_type == ChatScope.session.value,
                Conversation.scope_id == session_id,
            )
        )
        assert conversation is not None
        system_message = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.role == MessageRole.system.value,
            )
        )
        assert system_message is not None
        assert system_message.content == stored_session.system_message
        assert system_message.citations == []
    assert await _count_sessions() == 1


async def test_create_search_session_quote_is_substring(user_factory) -> None:
    user = await user_factory()
    await _create_paper("quote", chunks=[(1, "prefix " + "x" * 700 + " target suffix")])

    async with get_session_factory()() as db:
        result = await search_catalog(db, "target")

    assert result.evidence
    for hit in result.evidence:
        assert hit.quote in hit.content
        assert len(hit.quote) <= 500

    async with get_session_factory()() as db:
        await create_search_session(
            db,
            user_id=user.id,
            query="target",
            query_type=QueryType.text,
            result=result,
        )


async def test_create_empty_session_has_conversation_and_system_message(user_factory) -> None:
    user = await user_factory()

    async with get_session_factory()() as db:
        session = await create_search_session(
            db,
            user_id=user.id,
            query="nothing",
            query_type=QueryType.text,
            result=SearchResult(papers=[], evidence=[]),
        )
        session_id = session.id
        system_message = session.system_message

    assert system_message
    assert await _count_sessions() == 1

    async with get_session_factory()() as db:
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.user_id == user.id,
                Conversation.scope_type == ChatScope.session.value,
                Conversation.scope_id == session_id,
            )
        )
        assert conversation is not None
        message = await db.scalar(select(Message).where(Message.conversation_id == conversation.id))
        assert message is not None
        assert message.content == system_message


async def test_search_requires_login(client) -> None:
    response = await client.post("/api/v1/search", json={"query": "x"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_search_returns_201_session_detail(reader_client) -> None:
    paper = await _create_paper("Attention", chunks=[(1, "transformer evidence")])

    response = await reader_client.post("/api/v1/search", json={"query": "transformer"})

    assert response.status_code == 201
    body = response.json()
    assert body["query"] == "transformer"
    assert body["query_type"] == "text"
    assert body["paper_count"] == 1
    assert body["evidence_count"] >= 1
    assert body["has_evidence"] is True
    assert body["related_repo_count"] == 0
    assert body["conversation_id"] > 0
    assert body["system_message"]
    assert [item["id"] for item in body["papers"]] == [paper.id]
    assert body["created_at"].endswith("Z")


async def test_search_empty_creates_empty_session(reader_client) -> None:
    response = await reader_client.post(
        "/api/v1/search",
        json={"query": "no-such-query-term"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["papers"] == []
    assert body["paper_count"] == 0
    assert body["evidence_count"] == 0
    assert body["has_evidence"] is False
    assert body["system_message"] == (
        "没有找到足够证据回答这次查询。你可以修改查询，或去公开仓库看看。"
    )


async def test_search_validation(reader_client) -> None:
    for payload in (
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 2001},
        {"query": "x", "extra": 1},
    ):
        response = await reader_client.post("/api/v1/search", json=payload)
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "validation_error"


async def test_search_is_owner_only(
    reader_client,
    make_client,
    user_factory,
) -> None:
    await _create_paper("Owner paper", chunks=[(1, "owner evidence")])
    response = await reader_client.post("/api/v1/search", json={"query": "owner"})
    assert response.status_code == 201
    session_id = response.json()["id"]

    other_user = await user_factory(email="other@example.com")
    other_client = await make_client()
    await login(other_client, other_user.email)
    response = await other_client.get(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"

    admin = await user_factory(email="other-admin@example.com", role=UserRole.admin)
    admin_client = await make_client()
    await login(admin_client, admin.email)
    response = await admin_client.get(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_search_snapshot_ignores_later_catalog_changes(reader_client) -> None:
    first = await _create_paper("First", chunks=[(1, "snapshot evidence")])
    response = await reader_client.post("/api/v1/search", json={"query": "snapshot"})
    assert response.status_code == 201
    session_id = response.json()["id"]

    await _create_paper("Second", chunks=[(1, "snapshot evidence")])
    async with get_session_factory()() as db:
        stored = await db.get(Paper, first.id)
        assert stored is not None
        stored.is_archived = True
        await db.commit()

    response = await reader_client.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["papers"]] == [first.id]
    assert body["papers"][0]["is_archived"] is True


async def test_search_sql_error_returns_503_without_session(reader_client, monkeypatch) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    async def _broken(*args, **kwargs):
        raise SQLAlchemyError("broken")

    monkeypatch.setattr("app.api.v1.endpoints.search.search_catalog", _broken)

    response = await reader_client.post("/api/v1/search", json={"query": "anything"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "search_unavailable"
    assert await _count_sessions() == 0


async def test_search_route_is_registered(app) -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/v1/search" in paths
    assert "/api/v1/sessions/{session_id}" in paths


async def test_search_pdf_success(reader_client) -> None:
    catalog = await _create_paper("Catalog PDF", chunks=[(1, "unique pdf evidence")])
    content = make_pdf_bytes(title="PDF Paper", body="unique pdf evidence")
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("paper.pdf", content)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["query_type"] == "pdf"
    assert body["query"] == "paper.pdf"
    assert [item["id"] for item in body["papers"]] == [catalog.id]
    assert body["paper_count"] == 1
    assert body["evidence_count"] >= 1
    assert body["has_evidence"] is True

    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(Paper)) == 1
        assert await db.scalar(select(func.count()).select_from(PaperFile)) == 0


async def test_search_pdf_empty_filename(reader_client) -> None:
    content = make_pdf_bytes(title="PDF Paper", body="unique pdf evidence")
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("   ", content)},
    )

    assert response.status_code == 201, response.text
    assert response.json()["query"] == "PDF 查询"


async def test_search_pdf_no_text(reader_client) -> None:
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("blank.pdf", make_pdf_bytes(blank=True))},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "pdf_no_text"
    assert await _count_sessions() == 0


async def test_search_pdf_encrypted(reader_client) -> None:
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("secret.pdf", make_pdf_bytes(encrypted=True))},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "pdf_no_text"
    assert await _count_sessions() == 0


async def test_search_pdf_too_many_pages(reader_client) -> None:
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("long.pdf", make_pdf_bytes(pages=51))},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "pdf_too_many_pages"
    assert await _count_sessions() == 0


async def test_search_pdf_type_and_size(reader_client, monkeypatch) -> None:
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("paper.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"

    monkeypatch.setattr("app.api.v1.endpoints.search.MAX_PDF_BYTES", 1024)
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("large.pdf", b"%PDF-" + b"x" * 1024)},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert await _count_sessions() == 0


async def test_search_pdf_missing_file(reader_client) -> None:
    response = await reader_client.post("/api/v1/search/pdf")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert await _count_sessions() == 0


async def test_search_pdf_no_result_still_creates_empty_session(reader_client) -> None:
    content = make_pdf_bytes(title="No match", body="zzz qqq vvv")
    response = await reader_client.post(
        "/api/v1/search/pdf",
        files={"file": upload("no-match.pdf", content)},
    )

    assert response.status_code == 201
    assert response.json()["papers"] == []
    assert response.json()["system_message"] == (
        "没有找到足够证据回答这次查询。你可以修改查询，或去公开仓库看看。"
    )
