from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from app.core.papers import INTERRUPTED_MESSAGE, PaperStatus
from app.core.time import utcnow
from app.db import get_session_factory
from app.models import Paper, PaperChunk, PaperFile
from app.services.paper_parser import PdfParseError
from app.services.paper_processing import process_paper, recover_stale_papers
from app.services.paper_storage import PaperStorage
from tests.pdf_factory import make_pdf_bytes


async def _create_paper_with_file(
    storage_root: Path,
    *,
    status: str = PaperStatus.queued.value,
    content: bytes | None = None,
    title: str = "uploaded-paper",
) -> Paper:
    content = content if content is not None else make_pdf_bytes()
    storage = PaperStorage(storage_root)
    digest = hashlib.sha256(content).hexdigest()
    storage.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = storage.tmp_dir / f"{uuid.uuid4().hex}.pdf"
    tmp_path.write_bytes(content)

    async with get_session_factory()() as db:
        paper = Paper(title=title, status=status, authors=[])
        db.add(paper)
        await db.flush()
        relative = storage.relative_path(paper.id, digest)
        storage.promote(tmp_path, relative)
        db.add(
            PaperFile(
                paper_id=paper.id,
                original_filename="paper.pdf",
                storage_path=relative,
                size=len(content),
                mime="application/pdf",
                sha256=digest,
            )
        )
        await db.commit()
        await db.refresh(paper)
        return paper


async def _chunk_count(paper_id: int) -> int:
    async with get_session_factory()() as db:
        return (
            await db.scalar(
                select(func.count()).select_from(PaperChunk).where(PaperChunk.paper_id == paper_id)
            )
        ) or 0


async def _get_paper(paper_id: int) -> Paper:
    async with get_session_factory()() as db:
        paper = await db.get(Paper, paper_id)
        assert paper is not None
        return paper


async def test_process_paper_reaches_ready(paper_storage_root: Path) -> None:
    paper = await _create_paper_with_file(paper_storage_root)

    await process_paper(paper.id)

    ready = await _get_paper(paper.id)
    assert ready.status == PaperStatus.ready.value
    assert ready.error_message is None
    assert ready.title == "Attention Is All You Need"
    assert ready.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert ready.content_hash
    assert await _chunk_count(paper.id) > 0


async def test_process_paper_no_text_layer_sets_failed(paper_storage_root: Path) -> None:
    paper = await _create_paper_with_file(
        paper_storage_root,
        content=make_pdf_bytes(blank=True),
    )

    await process_paper(paper.id)

    failed = await _get_paper(paper.id)
    assert failed.status == PaperStatus.failed.value
    expected = PdfParseError(
        "no_text_layer",
        "PDF 没有可提取的文本层（扫描件/图片型 PDF 暂不支持）",
    ).user_message
    assert failed.error_message == expected
    assert str(paper_storage_root) not in (failed.error_message or "")
    assert "Traceback" not in (failed.error_message or "")


async def test_process_paper_corrupt_sets_failed(paper_storage_root: Path) -> None:
    paper = await _create_paper_with_file(
        paper_storage_root,
        content=b"%PDF-1.4\nbroken",
    )

    await process_paper(paper.id)

    failed = await _get_paper(paper.id)
    assert failed.status == PaperStatus.failed.value
    assert failed.error_message == "文件已损坏，无法解析 PDF"


async def test_process_paper_is_idempotent(paper_storage_root: Path) -> None:
    paper = await _create_paper_with_file(paper_storage_root)
    await process_paper(paper.id)
    first_count = await _chunk_count(paper.id)

    await process_paper(paper.id)

    assert await _chunk_count(paper.id) == first_count


async def test_process_paper_missing_file_sets_failed(paper_storage_root: Path) -> None:
    paper = await _create_paper_with_file(paper_storage_root)
    storage = PaperStorage(paper_storage_root)
    async with get_session_factory()() as db:
        file = await db.scalar(select(PaperFile).where(PaperFile.paper_id == paper.id))
        assert file is not None
        storage.absolute_path(file.storage_path).unlink()

    await process_paper(paper.id)

    failed = await _get_paper(paper.id)
    assert failed.status == PaperStatus.failed.value
    assert failed.error_message == "文件不存在，请重试"


async def test_recover_stale_marks_old_parsing_and_indexing(
    paper_storage_root: Path,
) -> None:
    parsing = await _create_paper_with_file(
        paper_storage_root,
        status=PaperStatus.parsing.value,
        content=make_pdf_bytes(title="Parsing paper", subject="Test"),
    )
    indexing = await _create_paper_with_file(
        paper_storage_root,
        status=PaperStatus.indexing.value,
        content=make_pdf_bytes(title="Indexing paper", subject="Test"),
    )
    old = utcnow() - timedelta(minutes=31)
    async with get_session_factory()() as db:
        await db.execute(
            update(Paper).where(Paper.id.in_([parsing.id, indexing.id])).values(updated_at=old)
        )
        await db.commit()

    count = await recover_stale_papers()

    assert count == 2
    for paper_id in (parsing.id, indexing.id):
        paper = await _get_paper(paper_id)
        assert paper.status == PaperStatus.failed.value
        assert paper.error_message == INTERRUPTED_MESSAGE


async def test_recover_stale_ignores_recent_and_queued(paper_storage_root: Path) -> None:
    parsing = await _create_paper_with_file(
        paper_storage_root,
        status=PaperStatus.parsing.value,
        content=make_pdf_bytes(title="Recent parsing", subject="Test"),
    )
    queued = await _create_paper_with_file(
        paper_storage_root,
        status=PaperStatus.queued.value,
        content=make_pdf_bytes(title="Old queued", subject="Test"),
    )
    old = utcnow() - timedelta(minutes=31)
    async with get_session_factory()() as db:
        await db.execute(update(Paper).where(Paper.id == queued.id).values(updated_at=old))
        await db.commit()

    count = await recover_stale_papers()

    assert count == 0
    assert (await _get_paper(parsing.id)).status == PaperStatus.parsing.value
    assert (await _get_paper(queued.id)).status == PaperStatus.queued.value


async def test_process_paper_marks_text_duplicate_as_failed(paper_storage_root: Path) -> None:
    original = await _create_paper_with_file(
        paper_storage_root,
        content=make_pdf_bytes(subject="NeurIPS 2017"),
    )
    await process_paper(original.id)

    # 只改元数据 → 文件字节不同（SHA-256 不同），但抽取出的正文一致。
    copy = await _create_paper_with_file(
        paper_storage_root,
        content=make_pdf_bytes(subject="Reprint 2024"),
        title="reprint.pdf",
    )
    await process_paper(copy.id)

    failed = await _get_paper(copy.id)
    assert failed.status == PaperStatus.failed.value
    assert failed.error_message == f"与已有论文重复：Attention Is All You Need（ID {original.id}）"
    assert str(paper_storage_root) not in (failed.error_message or "")
    assert await _chunk_count(copy.id) == 0
    assert (await _get_paper(original.id)).status == PaperStatus.ready.value


async def test_process_paper_keeps_distinct_content_ready(paper_storage_root: Path) -> None:
    first = await _create_paper_with_file(paper_storage_root)
    second = await _create_paper_with_file(
        paper_storage_root,
        content=make_pdf_bytes(
            body=(
                "Graph Neural Networks\n"
                "Abstract\n"
                "This paper studies message passing over graphs.\n"
                "1 Introduction\n"
                "Graph neural networks generalize convolutions to irregular structures."
            )
        ),
    )

    await process_paper(first.id)
    await process_paper(second.id)

    assert (await _get_paper(first.id)).status == PaperStatus.ready.value
    assert (await _get_paper(second.id)).status == PaperStatus.ready.value


async def test_archived_duplicate_does_not_block_new_import(paper_storage_root: Path) -> None:
    archived = await _create_paper_with_file(paper_storage_root)
    await process_paper(archived.id)
    async with get_session_factory()() as db:
        await db.execute(update(Paper).where(Paper.id == archived.id).values(is_archived=True))
        await db.commit()

    again = await _create_paper_with_file(
        paper_storage_root,
        content=make_pdf_bytes(subject="Reprint 2025"),
    )
    await process_paper(again.id)

    assert (await _get_paper(again.id)).status == PaperStatus.ready.value
