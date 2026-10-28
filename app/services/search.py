from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import literal, or_, select
from sqlalchemy.dialects.mysql import match
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat import (
    EMPTY_SESSION_SYSTEM_MESSAGE,
    SESSION_SYSTEM_MESSAGE,
    ChatScope,
    MessageRole,
    MessageStatus,
    QueryType,
)
from app.models import (
    Conversation,
    Message,
    Paper,
    PaperChunk,
    SearchSession,
    SessionEvidence,
    SessionPaper,
)
from app.services.paper_queries import visible_paper_conditions

PAPER_LIMIT = 8
EVIDENCE_LIMIT = 20
QUOTE_MAX = 500
_CANDIDATE_LIMIT = EVIDENCE_LIMIT * 3


@dataclass(frozen=True, slots=True)
class EvidenceHit:
    paper_id: int
    chunk_id: int
    page: int | None
    quote: str
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class SearchResult:
    papers: list[Paper]
    evidence: list[EvidenceHit]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _quote(content: str, keyword: str, *, center: bool) -> str:
    if not center:
        return content[:QUOTE_MAX]
    index = content.lower().find(keyword.lower())
    if index < 0:
        return content[:QUOTE_MAX]
    start = max(0, index - 250)
    end = min(len(content), index + len(keyword) + 250)
    return content[start:end][:QUOTE_MAX]


def _hit_from_chunk(
    chunk: PaperChunk,
    paper: Paper,
    keyword: str,
    *,
    score: float,
    center: bool,
) -> EvidenceHit:
    return EvidenceHit(
        paper_id=paper.id,
        chunk_id=chunk.id,
        page=chunk.page,
        quote=_quote(chunk.content, keyword, center=center),
        content=chunk.content,
        score=score,
    )


async def _search_chunks(db: AsyncSession, keyword: str) -> list[EvidenceHit]:
    if len(keyword) >= 2:
        relevance = match(PaperChunk.content, against=keyword)
        stmt = (
            select(PaperChunk, Paper, relevance.label("score"))
            .join(Paper, Paper.id == PaperChunk.paper_id)
            .where(*visible_paper_conditions(), relevance)
            .order_by(relevance.desc(), Paper.id.desc(), PaperChunk.chunk_index.asc())
        )
    else:
        pattern = f"%{_escape_like(keyword)}%"
        stmt = (
            select(PaperChunk, Paper, literal(0.0).label("score"))
            .join(Paper, Paper.id == PaperChunk.paper_id)
            .where(
                *visible_paper_conditions(),
                PaperChunk.content.like(pattern, escape="\\"),
            )
            .order_by(Paper.id.desc(), PaperChunk.chunk_index.asc())
        )

    rows = (await db.execute(stmt.limit(_CANDIDATE_LIMIT))).all()
    hits: list[EvidenceHit] = []
    seen_chunks: set[int] = set()
    for chunk, paper, score in rows:
        if chunk.id in seen_chunks:
            continue
        seen_chunks.add(chunk.id)
        hits.append(
            _hit_from_chunk(
                chunk,
                paper,
                keyword,
                score=float(score or 0.0),
                center=True,
            )
        )
    return hits


async def _search_metadata(db: AsyncSession, keyword: str) -> list[Paper]:
    if len(keyword) >= 2:
        relevance = match(Paper.title, Paper.abstract, against=keyword)
        stmt = (
            select(Paper)
            .where(*visible_paper_conditions(), relevance)
            .order_by(relevance.desc(), Paper.id.desc())
        )
    else:
        pattern = f"%{_escape_like(keyword)}%"
        stmt = (
            select(Paper)
            .where(
                *visible_paper_conditions(),
                or_(
                    Paper.title.like(pattern, escape="\\"),
                    Paper.abstract.like(pattern, escape="\\"),
                ),
            )
            .order_by(Paper.id.desc())
        )
    return list((await db.scalars(stmt.limit(PAPER_LIMIT))).all())


async def search_catalog(db: AsyncSession, query: str) -> SearchResult:
    keyword = query.strip()
    if not keyword:
        return SearchResult(papers=[], evidence=[])

    hits = await _search_chunks(db, keyword)
    if hits:
        papers: list[Paper] = []
        paper_ids: set[int] = set()
        for hit in hits:
            if hit.paper_id in paper_ids:
                continue
            paper = await db.get(Paper, hit.paper_id)
            if paper is None:
                continue
            paper_ids.add(paper.id)
            papers.append(paper)
            if len(papers) >= PAPER_LIMIT:
                break
        evidence = [hit for hit in hits if hit.paper_id in paper_ids][:EVIDENCE_LIMIT]
        return SearchResult(papers=papers, evidence=evidence)

    papers = await _search_metadata(db, keyword)
    if not papers:
        return SearchResult(papers=[], evidence=[])

    chunks = (
        await db.scalars(
            select(PaperChunk)
            .where(PaperChunk.paper_id.in_([paper.id for paper in papers]))
            .order_by(PaperChunk.paper_id.asc(), PaperChunk.chunk_index.asc())
        )
    ).all()
    by_paper: dict[int, list[PaperChunk]] = {}
    for chunk in chunks:
        by_paper.setdefault(chunk.paper_id, []).append(chunk)

    selected_papers: list[Paper] = []
    evidence: list[EvidenceHit] = []
    for paper in papers:
        paper_chunks = by_paper.get(paper.id, [])[:2]
        if not paper_chunks:
            continue
        selected_papers.append(paper)
        for chunk in paper_chunks:
            if len(evidence) >= EVIDENCE_LIMIT:
                break
            evidence.append(
                _hit_from_chunk(
                    chunk,
                    paper,
                    keyword,
                    score=0.0,
                    center=False,
                )
            )
    return SearchResult(papers=selected_papers, evidence=evidence)


async def create_search_session(
    db: AsyncSession,
    *,
    user_id: int,
    query: str,
    query_type: QueryType,
    result: SearchResult,
) -> SearchSession:
    paper_count = len(result.papers)
    evidence_count = len(result.evidence)
    system_message = SESSION_SYSTEM_MESSAGE if evidence_count else EMPTY_SESSION_SYSTEM_MESSAGE
    session = SearchSession(
        user_id=user_id,
        query=query.strip(),
        query_type=query_type.value,
        paper_count=paper_count,
        evidence_count=evidence_count,
        system_message=system_message,
    )
    try:
        db.add(session)
        await db.flush()

        for rank, paper in enumerate(result.papers, start=1):
            db.add(SessionPaper(session_id=session.id, paper_id=paper.id, rank=rank))
        seen_chunks: set[int] = set()
        for rank, hit in enumerate(result.evidence, start=1):
            if hit.chunk_id in seen_chunks:
                continue
            seen_chunks.add(hit.chunk_id)
            db.add(
                SessionEvidence(
                    session_id=session.id,
                    paper_id=hit.paper_id,
                    chunk_id=hit.chunk_id,
                    rank=rank,
                    page=hit.page,
                    quote=hit.quote,
                )
            )

        conversation = Conversation(
            user_id=user_id,
            scope_type=ChatScope.session.value,
            scope_id=session.id,
        )
        db.add(conversation)
        await db.flush()
        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.system.value,
                content=system_message,
                status=MessageStatus.complete.value,
                citations=[],
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await db.refresh(session)
    return session
