from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.papers import PaperStatus
from app.core.time import utcnow
from app.db import get_session_factory
from app.models import Paper, PaperChunk, PaperFile
from app.services import paper_storage
from app.services.paper_storage import PaperStorage
from tests.api.conftest import login
from tests.pdf_factory import make_pdf_bytes, upload

ADMIN_PAPERS = "/api/v1/admin/papers"
IMPORT = f"{ADMIN_PAPERS}/import"


async def _counts() -> tuple[int, int]:
    async with get_session_factory()() as db:
        papers = await db.scalar(select(func.count()).select_from(Paper)) or 0
        files = await db.scalar(select(func.count()).select_from(PaperFile)) or 0
    return papers, files


async def wait_for_status(
    client: AsyncClient,
    paper_id: int,
    expected: str,
    timeout: float = 10.0,
) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last: dict | None = None
    while loop.time() < deadline:
        response = await client.get(f"{ADMIN_PAPERS}/{paper_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == expected:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"paper {paper_id} did not reach {expected}: {last}")


def _storage_file_count(root: Path) -> int:
    return len([path for path in root.rglob("*") if path.is_file()])


def _form_files(*uploads: tuple[str, bytes, str]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", item) for item in uploads]


async def _create_paper(
    storage_root: Path,
    *,
    title: str = "Test paper",
    status: str = PaperStatus.ready.value,
    archived: bool = False,
    abstract: str | None = None,
    venue: str | None = "Test venue",
    year: int | None = 2020,
    created_at=None,
    updated_at=None,
    with_file: bool = False,
    with_chunk: bool = False,
) -> Paper:
    storage = PaperStorage(storage_root)
    async with get_session_factory()() as db:
        paper = Paper(
            title=title,
            authors=["Alice", "Bob"],
            status=status,
            is_archived=archived,
            abstract=abstract,
            venue=venue,
            year=year,
        )
        if created_at is not None:
            paper.created_at = created_at
        if updated_at is not None:
            paper.updated_at = updated_at
        db.add(paper)
        await db.flush()

        if with_file:
            content = make_pdf_bytes(title=title, subject="Test venue")
            digest = hashlib.sha256(content).hexdigest()
            storage.tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = storage.tmp_dir / f"{uuid.uuid4().hex}.pdf"
            tmp_path.write_bytes(content)
            relative = storage.relative_path(paper.id, digest)
            storage.promote(tmp_path, relative)
            db.add(
                PaperFile(
                    paper_id=paper.id,
                    original_filename=f"{title}.pdf",
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
                    content="old chunk",
                    char_count=9,
                )
            )
        await db.commit()
        await db.refresh(paper)
        return paper


async def test_import_requires_admin(
    make_client,
    reader_user,
) -> None:
    anon = await make_client()
    response = await anon.post(IMPORT, files=_form_files(upload("a.pdf", make_pdf_bytes())))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"

    reader = await make_client()
    await login(reader, reader_user.email)
    response = await reader.post(IMPORT, files=_form_files(upload("a.pdf", make_pdf_bytes())))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_import_missing_or_zero_files(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    response = await admin_client.post(IMPORT)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

    response = await admin_client.post(IMPORT, files=[])
    assert response.status_code == 422
    assert await _counts() == (0, 0)
    assert _storage_file_count(paper_storage_root) == 0


async def test_import_over_20_files(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    files = [upload(f"{index}.pdf", make_pdf_bytes()) for index in range(21)]

    response = await admin_client.post(IMPORT, files=_form_files(*files))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert await _counts() == (0, 0)
    assert _storage_file_count(paper_storage_root) == 0


async def test_import_valid_pdf_returns_202(admin_client: AsyncClient) -> None:
    files = _form_files(upload("paper.pdf", make_pdf_bytes()))
    response = await admin_client.post(IMPORT, files=files)

    assert response.status_code == 202
    body = response.json()
    assert body["created"] == 1
    assert body["duplicates"] == 0
    assert body["rejected"] == 0
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["filename"] == "paper.pdf"
    assert item["status"] == "queued"
    assert item["paper_id"] is not None

    detail = await wait_for_status(admin_client, item["paper_id"], "ready")
    assert detail["chunk_count"] > 0
    assert detail["file"]["original_filename"] == "paper.pdf"
    assert await _counts() == (1, 1)


async def test_import_duplicate_returns_existing(admin_client: AsyncClient) -> None:
    content = make_pdf_bytes()
    first = await admin_client.post(
        IMPORT,
        files=_form_files(upload("paper.pdf", content)),
    )
    assert first.status_code == 202
    paper_id = first.json()["items"][0]["paper_id"]
    await wait_for_status(admin_client, paper_id, "ready")

    second = await admin_client.post(
        IMPORT,
        files=_form_files(upload("paper.pdf", content)),
    )

    assert second.status_code == 202
    body = second.json()
    assert body["created"] == 0
    assert body["duplicates"] == 1
    assert body["items"][0]["duplicate"] is True
    assert body["items"][0]["paper_id"] == paper_id
    assert await _counts() == (1, 1)


async def test_import_same_request_duplicate(admin_client: AsyncClient) -> None:
    content = make_pdf_bytes()
    response = await admin_client.post(
        IMPORT,
        files=_form_files(
            upload("first.pdf", content),
            upload("second.pdf", content),
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["created"] == 1
    assert body["duplicates"] == 1
    assert body["items"][0]["duplicate"] is False
    assert body["items"][1]["duplicate"] is True
    await wait_for_status(admin_client, body["items"][0]["paper_id"], "ready")
    assert await _counts() == (1, 1)


async def test_import_rejects_non_pdf_without_failing_request(
    admin_client: AsyncClient,
) -> None:
    response = await admin_client.post(
        IMPORT,
        files=_form_files(
            upload("good.pdf", make_pdf_bytes()),
            upload("bad.pdf", b"not a pdf"),
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["created"] == 1
    assert body["rejected"] == 1
    assert len(body["items"]) == 2
    assert body["items"][1]["paper_id"] is None
    assert "不是 PDF" in body["items"][1]["error"]
    await wait_for_status(admin_client, body["items"][0]["paper_id"], "ready")
    assert await _counts() == (1, 1)


async def test_import_sanitizes_filename(admin_client: AsyncClient) -> None:
    response = await admin_client.post(
        IMPORT,
        files=_form_files(upload("../../论文.pdf", make_pdf_bytes())),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["items"][0]["filename"] == "论文.pdf"
    paper_id = body["items"][0]["paper_id"]
    detail = await wait_for_status(admin_client, paper_id, "ready")
    assert detail["file"]["original_filename"] == "论文.pdf"


async def test_import_batch_too_large_returns_413(
    admin_client: AsyncClient,
    paper_storage_root: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(paper_storage, "MAX_BATCH_BYTES", 1024)
    content = b"%PDF-" + b"0" * 600

    response = await admin_client.post(
        IMPORT,
        files=_form_files(upload("one.pdf", content), upload("two.pdf", content)),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert await _counts() == (0, 0)
    assert _storage_file_count(paper_storage_root) == 0


async def test_import_single_oversized_is_rejected_item(
    admin_client: AsyncClient,
    paper_storage_root: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(paper_storage, "MAX_FILE_BYTES", 1024)

    response = await admin_client.post(
        IMPORT,
        files=_form_files(upload("large.pdf", b"%PDF-" + b"0" * 2048)),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["created"] == 0
    assert body["rejected"] == 1
    assert await _counts() == (0, 0)
    assert _storage_file_count(paper_storage_root) == 0


async def test_import_route_not_shadowed_by_id(admin_client: AsyncClient) -> None:
    files = _form_files(upload("paper.pdf", make_pdf_bytes()))
    response = await admin_client.post(IMPORT, files=files)

    assert response.status_code == 202
    body = response.json()
    await wait_for_status(admin_client, body["items"][0]["paper_id"], "ready")


async def test_admin_list_filters_and_pagination(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    now = utcnow()
    older = await _create_paper(
        paper_storage_root,
        title="Alpha Ready",
        created_at=now - timedelta(minutes=2),
    )
    newer = await _create_paper(
        paper_storage_root,
        title="Alpha Archived",
        archived=True,
        created_at=now - timedelta(minutes=1),
    )
    failed = await _create_paper(
        paper_storage_root,
        title="Beta Failed",
        status=PaperStatus.failed.value,
        created_at=now,
    )

    response = await admin_client.get(
        ADMIN_PAPERS,
        params={"q": "Alpha", "status": "ready", "archived": "false"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [older.id]

    response = await admin_client.get(
        ADMIN_PAPERS,
        params={"q": "Alpha", "page": 1, "page_size": 1},
    )
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [newer.id]

    response = await admin_client.get(
        ADMIN_PAPERS,
        params={"q": "Alpha", "page": 2, "page_size": 1},
    )
    assert [item["id"] for item in response.json()["items"]] == [older.id]

    response = await admin_client.get(ADMIN_PAPERS, params={"status": "failed"})
    assert [item["id"] for item in response.json()["items"]] == [failed.id]
    response = await admin_client.get(ADMIN_PAPERS, params={"archived": "true"})
    assert [item["id"] for item in response.json()["items"]] == [newer.id]


async def test_admin_detail_includes_file_and_chunk_count(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="Detail paper",
        with_file=True,
        with_chunk=True,
    )

    response = await admin_client.get(f"{ADMIN_PAPERS}/{paper.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_count"] == 1
    assert body["file"]["original_filename"] == "Detail paper.pdf"
    assert body["file"]["mime"] == "application/pdf"
    assert body["file"]["sha256"]
    assert body["file"]["uploaded_at"].endswith("Z")


async def test_patch_metadata_updates_and_clears(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    old = utcnow() - timedelta(minutes=5)
    paper = await _create_paper(
        paper_storage_root,
        title="Old title",
        abstract="old abstract",
        updated_at=old,
    )

    response = await admin_client.patch(
        f"{ADMIN_PAPERS}/{paper.id}",
        json={
            "title": "  New title  ",
            "authors": [" Alice ", "Bob", "Alice"],
            "year": 2021,
            "venue": "  Journal X  ",
            "abstract": "  new\nabstract  ",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "New title"
    assert body["authors"] == ["Alice", "Bob"]
    assert body["year"] == 2021
    assert body["venue"] == "Journal X"
    assert body["abstract"] == "new\nabstract"
    assert body["updated_at"] != old.strftime("%Y-%m-%dT%H:%M:%SZ")

    response = await admin_client.patch(
        f"{ADMIN_PAPERS}/{paper.id}",
        json={"authors": [], "year": None, "venue": " ", "abstract": " "},
    )
    body = response.json()
    assert body["authors"] == []
    assert body["year"] is None
    assert body["venue"] is None
    assert body["abstract"] is None


async def test_patch_empty_body_400(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(paper_storage_root)
    response = await admin_client.patch(f"{ADMIN_PAPERS}/{paper.id}", json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


@pytest.mark.parametrize(
    "status",
    [PaperStatus.queued.value, PaperStatus.parsing.value, PaperStatus.indexing.value],
)
async def test_patch_processing_paper_409(
    admin_client: AsyncClient,
    paper_storage_root: Path,
    status: str,
) -> None:
    paper = await _create_paper(paper_storage_root, status=status)
    response = await admin_client.patch(
        f"{ADMIN_PAPERS}/{paper.id}",
        json={"title": "Nope"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state"


async def test_patch_missing_404(admin_client: AsyncClient) -> None:
    response = await admin_client.patch(f"{ADMIN_PAPERS}/999999", json={"title": "Nope"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_not_found"


async def test_retry_only_failed_unarchived(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    failed = await _create_paper(
        paper_storage_root,
        title="Failed retry",
        status=PaperStatus.failed.value,
    )
    response = await admin_client.post(f"{ADMIN_PAPERS}/{failed.id}/retry")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    await wait_for_status(admin_client, failed.id, "failed")

    ready = await _create_paper(paper_storage_root, title="Ready no retry")
    response = await admin_client.post(f"{ADMIN_PAPERS}/{ready.id}/retry")
    assert response.status_code == 409

    archived = await _create_paper(
        paper_storage_root,
        title="Archived failed",
        status=PaperStatus.failed.value,
        archived=True,
    )
    response = await admin_client.post(f"{ADMIN_PAPERS}/{archived.id}/retry")
    assert response.status_code == 409


async def test_retry_reprocesses_to_ready(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="Retry paper",
        status=PaperStatus.failed.value,
        with_file=True,
        with_chunk=True,
    )

    response = await admin_client.post(f"{ADMIN_PAPERS}/{paper.id}/retry")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    detail = await wait_for_status(admin_client, paper.id, "ready")
    assert detail["chunk_count"] > 0
    async with get_session_factory()() as db:
        chunks = (await db.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper.id))).all()
    assert all(chunk.content != "old chunk" for chunk in chunks)


async def test_archive_ready_and_idempotent(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(paper_storage_root, title="Archive paper")

    first = await admin_client.post(f"{ADMIN_PAPERS}/{paper.id}/archive")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["is_archived"] is True
    assert first_body["archived_at"].endswith("Z")

    second = await admin_client.post(f"{ADMIN_PAPERS}/{paper.id}/archive")
    assert second.status_code == 200
    assert second.json()["archived_at"] == first_body["archived_at"]


async def test_archive_failed_409(
    admin_client: AsyncClient,
    paper_storage_root: Path,
) -> None:
    paper = await _create_paper(
        paper_storage_root,
        title="Not ready",
        status=PaperStatus.failed.value,
    )
    response = await admin_client.post(f"{ADMIN_PAPERS}/{paper.id}/archive")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state"


async def test_archive_missing_404(admin_client: AsyncClient) -> None:
    response = await admin_client.post(f"{ADMIN_PAPERS}/999999/archive")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_not_found"
