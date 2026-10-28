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
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.papers import PaperStatus
from app.db import get_db
from app.models import Paper, PaperChunk
from app.schemas import PaperBrief, PaperDetail, PaperPage
from app.services.paper_queries import visible_paper_conditions
from app.services.paper_storage import PaperStorage

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
        available_summary_levels=[],
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
