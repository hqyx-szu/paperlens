from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.services import paper_storage
from app.services.paper_storage import BatchTooLarge, PaperStorage, safe_basename


def _upload(
    content: bytes,
    *,
    filename: str = "paper.pdf",
    content_type: str = "application/pdf",
) -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def test_safe_basename_strips_posix_and_windows_paths() -> None:
    assert safe_basename("../../a.pdf") == "a.pdf"
    assert safe_basename(r"C:\x\a.pdf") == "a.pdf"


def test_safe_basename_rejects_empty_dot_and_null() -> None:
    assert safe_basename(None) is None
    assert safe_basename("") is None
    assert safe_basename(".") is None
    assert safe_basename("..") is None
    assert safe_basename("a\x00.pdf") is None


async def test_stage_valid_pdf(tmp_path: Path) -> None:
    storage = PaperStorage(tmp_path / "papers")
    content = b"%PDF-1.4\n" + b"x" * 100

    staged = await storage.stage(_upload(content), already_bytes=0)

    assert staged.display_name == "paper.pdf"
    assert staged.rejection is None
    assert staged.size == len(content)
    assert staged.sha256 == hashlib.sha256(content).hexdigest()
    assert staged.tmp_path is not None
    assert staged.tmp_path.is_file()
    assert staged.tmp_path.is_relative_to(storage.root)


async def test_stage_rejects_non_pdf_header(tmp_path: Path) -> None:
    storage = PaperStorage(tmp_path / "papers")
    staged = await storage.stage(_upload(b"not a pdf"), already_bytes=0)

    assert staged.rejection is not None
    assert "不是 PDF" in staged.rejection
    assert staged.tmp_path is None
    assert not list(storage.tmp_dir.glob("*.upload"))


async def test_stage_rejects_bad_content_type(tmp_path: Path) -> None:
    storage = PaperStorage(tmp_path / "papers")
    staged = await storage.stage(
        _upload(b"%PDF-1.4\n", content_type="application/octet-stream"),
        already_bytes=0,
    )

    assert staged.rejection is not None
    assert staged.tmp_path is None


async def test_stage_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paper_storage, "MAX_FILE_BYTES", 1024)
    storage = PaperStorage(tmp_path / "papers")

    staged = await storage.stage(_upload(b"%PDF-" + b"0" * 2048), already_bytes=0)

    assert staged.rejection == "文件大小超过 20MB"
    assert staged.tmp_path is None
    assert not list(storage.tmp_dir.glob("*.upload"))


async def test_stage_raises_batch_too_large(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paper_storage, "MAX_BATCH_BYTES", 1024)
    storage = PaperStorage(tmp_path / "papers")
    first = await storage.stage(_upload(b"%PDF-" + b"0" * 600), already_bytes=0)
    assert first.tmp_path is not None
    first.tmp_path.unlink()

    with pytest.raises(BatchTooLarge):
        await storage.stage(_upload(b"%PDF-" + b"0" * 600), already_bytes=600)

    assert not list(storage.tmp_dir.glob("*.upload"))


def test_absolute_path_rejects_traversal(tmp_path: Path) -> None:
    storage = PaperStorage(tmp_path / "papers")

    with pytest.raises(ValueError):
        storage.absolute_path("../../etc/passwd")
    assert storage.exists("../../etc/passwd") is False


def test_promote_and_remove(tmp_path: Path) -> None:
    storage = PaperStorage(tmp_path / "papers")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    relative = storage.relative_path(1, "a" * 64)
    storage.promote(source, relative)
    assert storage.exists(relative)
    assert storage.absolute_path(relative).is_file()

    storage.remove(relative)
    assert not storage.exists(relative)

    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"keep")
    storage.remove("../outside.pdf")
    assert outside.is_file()
