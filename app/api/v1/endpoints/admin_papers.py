from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Path, Query, UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_admin
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.papers import PROCESSING_STATUSES, PaperStatus
from app.core.time import utcnow
from app.db import get_db
from app.models import Paper, PaperChunk, PaperFile
from app.schemas import (
    AdminPaperDetail,
    AdminPaperListItem,
    AdminPaperPage,
    AdminPaperUpdate,
    ImportItem,
    ImportResponse,
    PaperFileRead,
)
from app.services.paper_processing import process_paper
from app.services.paper_storage import (
    MAX_BATCH_FILES,
    BatchTooLarge,
    PaperStorage,
    StagedUpload,
)

router = APIRouter(
    prefix="/admin/papers",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _initial_title(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].strip()
    return stem[:500] or "未命名论文"


def _cleanup_staged(staged: StagedUpload) -> None:
    if staged.tmp_path is not None:
        staged.tmp_path.unlink(missing_ok=True)
        staged.tmp_path = None


async def _find_file_by_sha(db: AsyncSession, sha256: str) -> PaperFile | None:
    return await db.scalar(
        select(PaperFile).options(selectinload(PaperFile.paper)).where(PaperFile.sha256 == sha256)
    )


def _paper_stmt(paper_id: int):
    return (
        select(Paper)
        .options(selectinload(Paper.file))
        .where(Paper.id == paper_id)
        .execution_options(populate_existing=True)
    )


async def _get_paper_or_404(db: AsyncSession, paper_id: int) -> Paper:
    paper = await db.scalar(_paper_stmt(paper_id))
    if paper is None:
        raise AppError(404, "paper_not_found", "论文不存在")
    return paper


def _to_admin_list_item(paper: Paper) -> AdminPaperListItem:
    return AdminPaperListItem(
        id=paper.id,
        title=paper.title,
        authors=paper.authors or [],
        year=paper.year,
        venue=paper.venue,
        status=PaperStatus(paper.status),
        is_archived=paper.is_archived,
        file_name=paper.file.original_filename if paper.file is not None else None,
        error_message=paper.error_message,
        created_at=paper.created_at,
        updated_at=paper.updated_at,
    )


async def _to_admin_detail(db: AsyncSession, paper: Paper) -> AdminPaperDetail:
    chunk_count = (
        await db.scalar(
            select(func.count()).select_from(PaperChunk).where(PaperChunk.paper_id == paper.id)
        )
    ) or 0
    file = None
    if paper.file is not None:
        file = PaperFileRead(
            original_filename=paper.file.original_filename,
            size=paper.file.size,
            mime=paper.file.mime,
            sha256=paper.file.sha256,
            uploaded_at=paper.file.uploaded_at,
        )
    base = _to_admin_list_item(paper)
    return AdminPaperDetail(
        **base.model_dump(),
        abstract=paper.abstract,
        doi=paper.doi,
        chunk_count=chunk_count,
        archived_at=paper.archived_at,
        file=file,
    )


@router.post("/import", response_model=ImportResponse, status_code=202)
async def import_papers(
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[list[UploadFile], File()],
) -> ImportResponse:
    if not files or not 1 <= len(files) <= MAX_BATCH_FILES:
        raise AppError(422, "validation_error", "请上传 1–20 个 PDF 文件")

    storage = PaperStorage(get_settings().paper_storage_dir)
    staged_uploads: list[StagedUpload] = []
    created_ids: list[int] = []
    items: list[ImportItem] = []
    created = duplicates = rejected = 0
    total_bytes = 0

    try:
        for upload in files:
            try:
                staged = await storage.stage(upload, already_bytes=total_bytes)
            except BatchTooLarge:
                raise AppError(413, "file_too_large", "上传文件总大小超过 100MB") from None
            staged_uploads.append(staged)
            total_bytes += staged.size

        for staged in staged_uploads:
            if staged.rejection is not None or staged.sha256 is None:
                rejected += 1
                _cleanup_staged(staged)
                items.append(
                    ImportItem(
                        filename=staged.display_name,
                        paper_id=None,
                        status=None,
                        duplicate=False,
                        is_archived=False,
                        error=staged.rejection or "文件处理失败",
                    )
                )
                continue

            existing = await _find_file_by_sha(db, staged.sha256)
            if existing is not None:
                duplicates += 1
                _cleanup_staged(staged)
                items.append(
                    ImportItem(
                        filename=staged.display_name,
                        paper_id=existing.paper_id,
                        status=PaperStatus(existing.paper.status),
                        duplicate=True,
                        is_archived=existing.paper.is_archived,
                        error=None,
                    )
                )
                continue

            paper = Paper(
                title=_initial_title(staged.display_name),
                authors=[],
                status=PaperStatus.queued.value,
            )
            db.add(paper)
            await db.flush()
            relative = storage.relative_path(paper.id, staged.sha256)
            assert staged.tmp_path is not None
            storage.promote(staged.tmp_path, relative)
            staged.tmp_path = None
            db.add(
                PaperFile(
                    paper_id=paper.id,
                    original_filename=staged.display_name,
                    storage_path=relative,
                    size=staged.size,
                    mime="application/pdf",
                    sha256=staged.sha256,
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                storage.remove(relative)
                existing = await _find_file_by_sha(db, staged.sha256)
                if existing is None:
                    raise
                duplicates += 1
                items.append(
                    ImportItem(
                        filename=staged.display_name,
                        paper_id=existing.paper_id,
                        status=PaperStatus(existing.paper.status),
                        duplicate=True,
                        is_archived=existing.paper.is_archived,
                        error=None,
                    )
                )
                continue
            except Exception:
                await db.rollback()
                storage.remove(relative)
                raise

            created += 1
            created_ids.append(paper.id)
            items.append(
                ImportItem(
                    filename=staged.display_name,
                    paper_id=paper.id,
                    status=PaperStatus.queued,
                    duplicate=False,
                    is_archived=False,
                    error=None,
                )
            )
    finally:
        for staged in staged_uploads:
            _cleanup_staged(staged)

    for paper_id in created_ids:
        background_tasks.add_task(process_paper, paper_id)
    return ImportResponse(
        items=items,
        created=created,
        duplicates=duplicates,
        rejected=rejected,
    )


@router.get("", response_model=AdminPaperPage)
async def list_papers(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(max_length=200)] = "",
    status: Annotated[PaperStatus | None, Query()] = None,
    archived: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminPaperPage:
    stmt = select(Paper).options(selectinload(Paper.file))
    keyword = q.strip()
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        stmt = stmt.where(
            or_(
                Paper.title.like(pattern, escape="\\"),
                Paper.abstract.like(pattern, escape="\\"),
            )
        )
    if status is not None:
        stmt = stmt.where(Paper.status == status.value)
    if archived is not None:
        stmt = stmt.where(Paper.is_archived.is_(archived))

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    papers = (
        await db.scalars(
            stmt.order_by(Paper.created_at.desc(), Paper.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return AdminPaperPage(
        items=[_to_admin_list_item(paper) for paper in papers],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{paper_id}", response_model=AdminPaperDetail)
async def get_paper(
    db: Annotated[AsyncSession, Depends(get_db)],
    paper_id: Annotated[int, Path(ge=1)],
) -> AdminPaperDetail:
    paper = await _get_paper_or_404(db, paper_id)
    return await _to_admin_detail(db, paper)


@router.patch("/{paper_id}", response_model=AdminPaperDetail)
async def update_paper(
    payload: AdminPaperUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    paper_id: Annotated[int, Path(ge=1)],
) -> AdminPaperDetail:
    if not payload.model_fields_set:
        raise AppError(400, "bad_request", "请求不合法")

    paper = await _get_paper_or_404(db, paper_id)
    if paper.status in PROCESSING_STATUSES:
        raise AppError(409, "invalid_state", "论文正在处理，暂不能修改")

    changes = payload.model_fields_set
    if "title" in changes:
        paper.title = payload.title
    if "authors" in changes:
        paper.authors = payload.authors
    if "year" in changes:
        paper.year = payload.year
    if "venue" in changes:
        paper.venue = payload.venue
    if "abstract" in changes:
        paper.abstract = payload.abstract
    paper.updated_at = utcnow()
    await db.commit()
    return await _to_admin_detail(db, paper)


@router.post("/{paper_id}/retry", response_model=AdminPaperDetail, status_code=202)
async def retry_paper(
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    paper_id: Annotated[int, Path(ge=1)],
) -> AdminPaperDetail:
    await _get_paper_or_404(db, paper_id)
    result = await db.execute(
        update(Paper)
        .where(
            Paper.id == paper_id,
            Paper.status == PaperStatus.failed.value,
            Paper.is_archived.is_(False),
        )
        .values(
            status=PaperStatus.queued.value,
            error_message=None,
            updated_at=utcnow(),
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise AppError(409, "invalid_state", "论文当前状态不能重试")

    paper = await _get_paper_or_404(db, paper_id)
    background_tasks.add_task(process_paper, paper_id)
    return await _to_admin_detail(db, paper)


@router.post("/{paper_id}/archive", response_model=AdminPaperDetail)
async def archive_paper(
    db: Annotated[AsyncSession, Depends(get_db)],
    paper_id: Annotated[int, Path(ge=1)],
) -> AdminPaperDetail:
    paper = await _get_paper_or_404(db, paper_id)
    if paper.status != PaperStatus.ready.value:
        raise AppError(409, "invalid_state", "只有就绪论文可以归档")
    if not paper.is_archived:
        now = utcnow()
        result = await db.execute(
            update(Paper)
            .where(
                Paper.id == paper_id,
                Paper.status == PaperStatus.ready.value,
                Paper.is_archived.is_(False),
            )
            .values(is_archived=True, archived_at=now, updated_at=now)
        )
        await db.commit()
        if result.rowcount == 0:
            paper = await _get_paper_or_404(db, paper_id)
            if paper.status != PaperStatus.ready.value:
                raise AppError(409, "invalid_state", "只有就绪论文可以归档")
            return await _to_admin_detail(db, paper)
        paper = await _get_paper_or_404(db, paper_id)
    return await _to_admin_detail(db, paper)
