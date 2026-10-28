from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.chat import QueryType
from app.core.exceptions import AppError
from app.db import get_db
from app.models import User
from app.schemas.chat import SearchRequest, SessionDetail
from app.services.paper_parser import PdfParseError, extract_pdf_pages
from app.services.search import create_search_session, search_catalog
from app.services.session_service import get_session_detail

router = APIRouter(tags=["search"], dependencies=[Depends(get_current_user)])

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_PDF_QUERY_CHARS = 2000
_PDF_CHUNK_SIZE = 1024 * 1024


@router.post("/search", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
async def search(
    payload: SearchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SessionDetail:
    try:
        result = await search_catalog(db, payload.query)
        session = await create_search_session(
            db,
            user_id=user.id,
            query=payload.query,
            query_type=QueryType.text,
            result=result,
        )
    except SQLAlchemyError:
        await db.rollback()
        raise AppError(503, "search_unavailable", "检索服务暂时不可用") from None
    return await get_session_detail(db, session)


def _pdf_display_name(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (name or "PDF 查询")[:MAX_PDF_QUERY_CHARS]


async def _stage_pdf(upload: UploadFile, path: Path) -> int:
    size = 0
    with path.open("wb") as target:
        while chunk := await upload.read(_PDF_CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                raise AppError(413, "file_too_large", "文件大小超过 20MB")
            target.write(chunk)

    if size == 0:
        raise AppError(422, "validation_error", "文件不能为空")

    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if content_type != "application/pdf":
        raise AppError(415, "unsupported_media_type", "仅支持 PDF 文件")
    return size


@router.post(
    "/search/pdf",
    response_model=SessionDetail,
    status_code=status.HTTP_201_CREATED,
)
async def search_pdf(
    file: Annotated[UploadFile, File(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SessionDetail:
    display_name = _pdf_display_name(file.filename)
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "query.pdf"
        await _stage_pdf(file, path)
        try:
            pages = extract_pdf_pages(path, max_pages=MAX_PDF_PAGES)
        except PdfParseError as exc:
            if exc.code == "too_many_pages":
                raise AppError(422, "pdf_too_many_pages", "PDF 页数超过 50 页") from None
            raise AppError(422, "pdf_no_text", "PDF 没有可提取的文本层") from None

        query_text = "\n".join(text for _, text in pages if text)[:MAX_PDF_QUERY_CHARS]
        if not query_text:
            raise AppError(422, "pdf_no_text", "PDF 没有可提取的文本层")

    try:
        result = await search_catalog(db, query_text)
        session = await create_search_session(
            db,
            user_id=user.id,
            query=display_name,
            query_type=QueryType.pdf,
            result=result,
        )
    except SQLAlchemyError:
        await db.rollback()
        raise AppError(503, "search_unavailable", "检索服务暂时不可用") from None
    return await get_session_detail(db, session)
