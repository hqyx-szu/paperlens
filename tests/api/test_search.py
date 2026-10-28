from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from app.core.chat import ChatScope, MessageRole, QueryType
from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import (
    Conversation,
    Message,
    Paper,
    PaperChunk,
    SearchSession,
    SessionEvidence,
    SessionPaper,
)
from app.services.search import SearchResult, create_search_session, search_catalog


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
