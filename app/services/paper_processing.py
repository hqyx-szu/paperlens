from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.papers import INTERRUPTED_MESSAGE, PaperStatus
from app.core.time import utcnow
from app.db import get_session_factory
from app.models import Paper, PaperChunk
from app.services.embeddings import embed_paper_chunks
from app.services.paper_parser import PdfParseError, parse_pdf
from app.services.paper_storage import PaperStorage

logger = logging.getLogger("paperlens.paper_processing")

_INTERRUPTED_STATUSES = (PaperStatus.parsing.value, PaperStatus.indexing.value)
_FAILED_MESSAGE = "解析失败，请重试"
_MISSING_FILE_MESSAGE = "文件不存在，请重试"
_ERROR_MESSAGE_MAX = 500


async def _mark_failed(paper_id: int, message: str) -> None:
    try:
        async with get_session_factory()() as db:
            await db.execute(
                update(Paper)
                .where(
                    Paper.id == paper_id,
                    Paper.status.in_(_INTERRUPTED_STATUSES),
                )
                .values(
                    status=PaperStatus.failed.value,
                    error_message=message,
                    updated_at=utcnow(),
                )
            )
            await db.commit()
    except Exception:
        logger.exception("failed to mark paper as failed", extra={"paper_id": paper_id})


def _duplicate_message(duplicate: Paper) -> str:
    return f"与已有论文重复：{duplicate.title}（ID {duplicate.id}）"[:_ERROR_MESSAGE_MAX]


async def _find_duplicate(
    db: AsyncSession,
    paper_id: int,
    content_hash: str,
) -> Paper | None:
    """正文哈希命中其他 ready 论文时，视为同一论文的另一份 PDF 副本。"""
    return await db.scalar(
        select(Paper)
        .where(
            Paper.id != paper_id,
            Paper.content_hash == content_hash,
            Paper.status == PaperStatus.ready.value,
            Paper.is_archived.is_(False),
        )
        .order_by(Paper.id)
        .limit(1)
    )


async def process_paper(paper_id: int) -> None:
    """Claim and parse one queued paper; duplicate workers exit immediately."""
    async with get_session_factory()() as db:
        claimed = await db.execute(
            update(Paper)
            .where(
                Paper.id == paper_id,
                Paper.status == PaperStatus.queued.value,
            )
            .values(status=PaperStatus.parsing.value, updated_at=utcnow())
        )
        await db.commit()
        if claimed.rowcount == 0:
            return

        try:
            paper = await db.scalar(
                select(Paper).options(selectinload(Paper.file)).where(Paper.id == paper_id)
            )
            if paper is None or paper.file is None:
                await db.rollback()
                await _mark_failed(paper_id, _MISSING_FILE_MESSAGE)
                return

            storage = PaperStorage(get_settings().paper_storage_dir)
            try:
                stored_path = storage.absolute_path(paper.file.storage_path)
            except (OSError, ValueError):
                await db.rollback()
                await _mark_failed(paper_id, _MISSING_FILE_MESSAGE)
                return
            if not stored_path.is_file():
                await db.rollback()
                await _mark_failed(paper_id, _MISSING_FILE_MESSAGE)
                return

            parsed = await asyncio.to_thread(
                parse_pdf,
                stored_path,
                fallback_title=paper.title,
            )

            duplicate = await _find_duplicate(db, paper_id, parsed.content_hash)
            if duplicate is not None:
                paper.status = PaperStatus.failed.value
                paper.error_message = _duplicate_message(duplicate)
                paper.updated_at = utcnow()
                await db.commit()
                logger.info(
                    "paper duplicate detected",
                    extra={"paper_id": paper_id, "duplicate_of": duplicate.id},
                )
                return

            paper.status = PaperStatus.indexing.value
            paper.updated_at = utcnow()
            await db.commit()

            await db.execute(delete(PaperChunk).where(PaperChunk.paper_id == paper_id))
            now = utcnow()
            rows = [
                {
                    "paper_id": paper_id,
                    "chunk_index": index,
                    "page": chunk.page,
                    "content": chunk.content,
                    "char_count": chunk.char_count,
                    "created_at": now,
                }
                for index, chunk in enumerate(parsed.chunks)
            ]
            for start in range(0, len(rows), 500):
                await db.execute(insert(PaperChunk), rows[start : start + 500])

            paper.title = parsed.title
            paper.authors = parsed.authors
            paper.year = parsed.year
            paper.venue = parsed.venue
            paper.abstract = parsed.abstract
            paper.doi = parsed.doi
            paper.content_hash = parsed.content_hash
            paper.status = PaperStatus.ready.value
            paper.error_message = None
            paper.updated_at = now
            await db.commit()
        except PdfParseError as exc:
            await db.rollback()
            await _mark_failed(paper_id, exc.user_message)
        except Exception:
            await db.rollback()
            logger.exception("paper processing failed", extra={"paper_id": paper_id})
            await _mark_failed(paper_id, _FAILED_MESSAGE)
            return

    # 向量索引在片段落库后进行；失败不阻塞论文就绪（可用回填脚本补齐）。
    if get_settings().embeddings_enabled and not await embed_paper_chunks(paper_id):
        logger.warning("paper chunks left without embeddings", extra={"paper_id": paper_id})


async def recover_stale_papers() -> int:
    timeout = get_settings().paper_processing_timeout_minutes
    cutoff = utcnow() - timedelta(minutes=timeout)
    async with get_session_factory()() as db:
        result = await db.execute(
            update(Paper)
            .where(
                Paper.status.in_(_INTERRUPTED_STATUSES),
                Paper.updated_at < cutoff,
            )
            .values(
                status=PaperStatus.failed.value,
                error_message=INTERRUPTED_MESSAGE,
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return result.rowcount or 0


__all__ = ["process_paper", "recover_stale_papers"]
