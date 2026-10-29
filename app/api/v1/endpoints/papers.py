from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import FileResponse
from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.mysql import match
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.ai import get_llm_client, sse_response, with_heartbeat
from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.papers import PaperStatus
from app.db import get_db
from app.models import Conversation, Message, Paper, PaperChunk, User
from app.schemas import MessagePage, PaperBrief, PaperDetail, PaperPage
from app.schemas.chat import ChatRequest
from app.schemas.paper import SummaryGenerateRequest, SummaryLevel, SummaryRead
from app.services.chat import (
    build_chat_messages,
    get_or_create_conversation,
    load_paper_evidence,
)
from app.services.chat_stream import chat_events
from app.services.paper_queries import visible_paper_conditions
from app.services.paper_storage import PaperStorage
from app.services.session_service import list_messages
from app.services.summaries import (
    available_levels,
    generate_summary,
    get_cached_summary,
)

router = APIRouter(
    prefix="/papers",
    tags=["papers"],
    dependencies=[Depends(get_current_user)],
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=PaperPage)
async def list_papers(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(max_length=200)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaperPage:
    stmt = select(Paper).where(*visible_paper_conditions())
    keyword = q.strip()
    ordering = (Paper.id.desc(),)
    if keyword:
        if len(keyword) >= 2:
            match_expr = match(Paper.title, Paper.abstract, against=keyword)
            stmt = stmt.where(match_expr)
            ordering = (match_expr.desc(), Paper.id.desc())
        else:
            pattern = f"%{_escape_like(keyword)}%"
            stmt = stmt.where(
                or_(
                    Paper.title.like(pattern, escape="\\"),
                    Paper.abstract.like(pattern, escape="\\"),
                )
            )

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    papers = (
        await db.scalars(stmt.order_by(*ordering).offset((page - 1) * page_size).limit(page_size))
    ).all()
    return PaperPage(
        items=[PaperBrief.model_validate(paper) for paper in papers],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{paper_id}", response_model=PaperDetail)
async def get_paper(
    db: Annotated[AsyncSession, Depends(get_db)],
    paper_id: Annotated[int, Path(ge=1)],
) -> PaperDetail:
    paper = await db.scalar(
        select(Paper)
        .options(selectinload(Paper.file))
        .where(Paper.id == paper_id, Paper.status == PaperStatus.ready.value)
    )
    if paper is None:
        raise AppError(404, "paper_not_found", "论文不存在")

    storage = PaperStorage(get_settings().paper_storage_dir)
    has_file = paper.file is not None
    can_download = has_file and storage.exists(paper.file.storage_path)
    can_chat = bool(await db.scalar(select(exists().where(PaperChunk.paper_id == paper.id))))
    return PaperDetail(
        id=paper.id,
        title=paper.title,
        authors=paper.authors or [],
        year=paper.year,
        venue=paper.venue,
        abstract=paper.abstract,
        doi=paper.doi,
        is_archived=paper.is_archived,
        has_file=has_file,
        can_download=can_download,
        can_chat=can_chat,
        available_summary_levels=await available_levels(db, paper),
        created_at=paper.created_at,
    )


@router.get("/{paper_id}/download")
async def download_paper(
    db: Annotated[AsyncSession, Depends(get_db)],
    paper_id: Annotated[int, Path(ge=1)],
) -> FileResponse:
    paper = await db.scalar(
        select(Paper)
        .options(selectinload(Paper.file))
        .where(Paper.id == paper_id, Paper.status == PaperStatus.ready.value)
    )
    if paper is None:
        raise AppError(404, "paper_not_found", "论文不存在")
    if paper.file is None:
        raise AppError(404, "file_not_found", "文件不存在")

    storage = PaperStorage(get_settings().paper_storage_dir)
    try:
        path = storage.absolute_path(paper.file.storage_path)
    except (OSError, ValueError):
        raise AppError(404, "file_not_found", "文件不存在") from None
    if not path.is_file():
        raise AppError(404, "file_not_found", "文件不存在")

    original = paper.file.original_filename
    ascii_fallback = f"paper-{paper.id}.pdf"
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(original, safe='')}"
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )


async def _ready_paper(db: AsyncSession, paper_id: int) -> Paper:
    paper = await db.scalar(
        select(Paper).where(
            Paper.id == paper_id,
            Paper.status == PaperStatus.ready.value,
        )
    )
    if paper is None:
        raise AppError(404, "paper_not_found", "论文不存在")
    return paper


@router.get("/{paper_id}/summaries", response_model=SummaryRead)
async def get_paper_summary(
    paper_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    level: Annotated[SummaryLevel, Query()] = SummaryLevel.standard,
) -> SummaryRead:
    paper = await _ready_paper(db, paper_id)
    cached = await get_cached_summary(db, paper, level)
    if cached is None:
        raise AppError(404, "summary_not_generated", "该论文还没有生成此详略的四维总结")
    return cached


@router.post("/{paper_id}/summaries/generate", response_model=SummaryRead)
async def generate_paper_summary(
    paper_id: Annotated[int, Path(ge=1)],
    payload: SummaryGenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SummaryRead:
    paper = await _ready_paper(db, paper_id)
    client = get_llm_client()
    return await generate_summary(
        db, paper=paper, level=payload.level, client=client, force=payload.force
    )


@router.post("/{paper_id}/chat/stream")
async def paper_chat(
    paper_id: Annotated[int, Path(ge=1)],
    payload: ChatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    await _ready_paper(db, paper_id)
    if not await db.scalar(select(exists().where(PaperChunk.paper_id == paper_id))):
        raise AppError(409, "paper_not_ready", "论文还没有可对话的片段")

    evidence = await load_paper_evidence(db, paper_id=paper_id, question=payload.message)
    if not evidence:
        raise AppError(409, "paper_not_ready", "论文还没有可对话的片段")

    client = get_llm_client()
    conversation = await get_or_create_conversation(
        db,
        user_id=user.id,
        scope=ChatScope.paper,
        scope_id=paper_id,
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
        scope=ChatScope.paper,
        evidence=evidence,
        history=history,
        question=payload.message,
    )
    retry_messages = build_chat_messages(
        scope=ChatScope.paper,
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

    events = chat_events(
        client=client,
        messages=messages,
        retry_messages=retry_messages,
        evidence=evidence,
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        scope=ChatScope.paper,
        scope_id=paper_id,
    )
    return sse_response(with_heartbeat(events))


@router.get("/{paper_id}/messages", response_model=MessagePage)
async def get_paper_messages(
    paper_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> MessagePage:
    await _ready_paper(db, paper_id)
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.user_id == user.id,
            Conversation.scope_type == ChatScope.paper.value,
            Conversation.scope_id == paper_id,
        )
    )
    if conversation is None:
        return MessagePage(items=[], has_more=False, next_before_id=None)
    return await list_messages(
        db,
        conversation_id=conversation.id,
        limit=limit,
        before_id=before_id,
    )
