from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from urllib.parse import quote

from httpx import AsyncClient

from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Paper, PaperChunk, PaperFile
from app.services.paper_storage import PaperStorage
from tests.pdf_factory import make_pdf_bytes

PAPERS = "/api/v1/papers"


async def _create_paper(
    storage_root: Path,
    *,
    title: str,
    abstract: str | None = None,
    status: str = PaperStatus.ready.value,
    archived: bool = False,
    with_file: bool = False,
    with_chunk: bool = False,
    filename: str = "paper.pdf",
    storage_path: str | None = None,
) -> Paper:
    storage = PaperStorage(storage_root)
    async with get_session_factory()() as db:
        paper = Paper(
            title=title,
            authors=["Alice"],
            status=status,
            is_archived=archived,
            abstract=abstract,
            venue="Test venue",
            year=2020,
        )
        db.add(paper)
        await db.flush()

        if with_file:
            content = make_pdf_bytes(title=title, subject="Test venue")
            digest = hashlib.sha256(content).hexdigest()
            relative = storage_path or storage.relative_path(paper.id, digest)
            if storage_path is None:
                storage.tmp_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = storage.tmp_dir / f"{uuid.uuid4().hex}.pdf"
                tmp_path.write_bytes(content)
                storage.promote(tmp_path, relative)
            db.add(
                PaperFile(
                    paper_id=paper.id,
                    original_filename=filename,
                    storage_path=relative,
                    size=len(content),
                    mime="application/pdf",
                    sha256=digest,
                )
            )
        if with_chunk:
            db.add(
                PaperChunk(
                    paper_id=paper.id,
                    chunk_index=0,
                    page=1,
                    content="chunk",
                    char_count=5,
                )
            )
        await db.commit()
        await db.refresh(paper)
        return paper


async def test_user_papers_require_login(client: AsyncClient) -> None:
    response = await client.get(PAPERS)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_catalog_only_ready_unarchived(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    ready = await _create_paper(paper_storage_root, title="Ready")
    await _create_paper(paper_storage_root, title="Archived", archived=True)
    await _create_paper(
        paper_storage_root,
        title="Parsing",
        status=PaperStatus.parsing.value,
    )
    await _create_paper(
        paper_storage_root,
        title="Failed",
        status=PaperStatus.failed.value,
    )

    response = await reader_client.get(PAPERS)

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [ready.id]
    assert body["total"] == 1


async def test_catalog_search_chinese(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="量子计算综述",
        abstract="本文讨论量子算法与量子纠错。",
    )

    response = await reader_client.get(PAPERS, params={"q": "量子"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [paper.id]


async def test_catalog_search_single_char_falls_back_like(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(paper_storage_root, title="量子计算")

    response = await reader_client.get(PAPERS, params={"q": "量"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [paper.id]


async def test_catalog_pagination_and_order(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    papers = [await _create_paper(paper_storage_root, title=f"Paper {index}") for index in range(3)]

    response = await reader_client.get(PAPERS, params={"page": 1, "page_size": 2})

    body = response.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [papers[2].id, papers[1].id]
    response = await reader_client.get(PAPERS, params={"page": 2, "page_size": 2})
    assert [item["id"] for item in response.json()["items"]] == [papers[0].id]


async def test_detail_hides_processing_and_missing(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    processing = await _create_paper(
        paper_storage_root,
        title="Processing",
        status=PaperStatus.indexing.value,
    )

    response = await reader_client.get(f"{PAPERS}/{processing.id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_not_found"

    response = await reader_client.get(f"{PAPERS}/999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_not_found"


async def test_detail_allows_archived(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="Archived detail",
        archived=True,
    )

    response = await reader_client.get(f"{PAPERS}/{paper.id}")

    assert response.status_code == 200
    assert response.json()["is_archived"] is True


async def test_detail_flags(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="Flags paper",
        with_file=True,
        with_chunk=True,
    )
    no_file = await _create_paper(paper_storage_root, title="No file")

    response = await reader_client.get(f"{PAPERS}/{paper.id}")
    body = response.json()
    assert body["has_file"] is True
    assert body["can_download"] is True
    assert body["can_chat"] is True
    assert body["available_summary_levels"] == []

    response = await reader_client.get(f"{PAPERS}/{no_file.id}")
    body = response.json()
    assert body["has_file"] is False
    assert body["can_download"] is False
    assert body["can_chat"] is False


async def test_download_returns_pdf_and_rfc5987_name(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="Download paper",
        with_file=True,
        filename="中文论文.pdf",
    )

    response = await reader_client.get(f"{PAPERS}/{paper.id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    disposition = response.headers["content-disposition"]
    assert f'filename="paper-{paper.id}.pdf"' in disposition
    assert f"filename*=UTF-8''{quote('中文论文.pdf', safe='')}" in disposition
    assert response.content.startswith(b"%PDF-")


async def test_download_missing_or_outside_path_404(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    no_file = await _create_paper(paper_storage_root, title="No file download")
    response = await reader_client.get(f"{PAPERS}/{no_file.id}/download")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"

    outside = await _create_paper(
        paper_storage_root,
        title="Outside path",
        with_file=True,
        storage_path="../../etc/passwd",
    )
    response = await reader_client.get(f"{PAPERS}/{outside.id}/download")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


async def test_download_archived_allowed(
    reader_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="Archived download",
        archived=True,
        with_file=True,
    )

    response = await reader_client.get(f"{PAPERS}/{paper.id}/download")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
