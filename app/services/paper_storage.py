from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_FILES = 20
MAX_BATCH_BYTES = 100 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


class BatchTooLarge(Exception):
    """本次请求总大小超过上限。"""


@dataclass(slots=True)
class StagedUpload:
    display_name: str
    tmp_path: Path | None
    size: int
    sha256: str | None
    rejection: str | None


def safe_basename(filename: str | None) -> str | None:
    if filename is None:
        return None
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."} or "\x00" in name or len(name) > 255:
        return None
    return name


class PaperStorage:
    def __init__(self, root: Path) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def tmp_dir(self) -> Path:
        return self._root / ".tmp"

    def relative_path(self, paper_id: int, sha256: str) -> str:
        return f"{paper_id}/{sha256}.pdf"

    def absolute_path(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("path escapes paper storage root")
        return path

    def exists(self, relative: str) -> bool:
        try:
            return self.absolute_path(relative).is_file()
        except (ValueError, OSError):
            return False

    def promote(self, tmp_path: Path, relative: str) -> None:
        destination = self.absolute_path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, destination)

    def remove(self, relative: str) -> None:
        with contextlib.suppress(OSError, ValueError):
            self.absolute_path(relative).unlink()

    async def stage(
        self,
        upload: UploadFile,
        *,
        already_bytes: int,
        max_batch_bytes: int | None = None,
    ) -> StagedUpload:
        filename = safe_basename(upload.filename)
        if filename is None:
            return StagedUpload("", None, 0, None, "文件名不合法")

        content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if content_type != "application/pdf":
            return StagedUpload(filename, None, 0, None, "仅支持 PDF 文件")

        limit = MAX_BATCH_BYTES if max_batch_bytes is None else max_batch_bytes
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.tmp_dir / f"{uuid4().hex}.upload"
        digest = hashlib.sha256()
        size = 0
        rejection: str | None = None

        try:
            with tmp_path.open("wb") as target:
                while chunk := await upload.read(_CHUNK_SIZE):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        rejection = "文件大小超过 20MB"
                        break
                    if already_bytes + size > limit:
                        raise BatchTooLarge
                    target.write(chunk)
                    digest.update(chunk)

            if rejection is None and size == 0:
                rejection = "文件为空"
            if rejection is None:
                with tmp_path.open("rb") as source:
                    if source.read(5) != b"%PDF-":
                        rejection = "文件不是 PDF"
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        if rejection is not None:
            tmp_path.unlink(missing_ok=True)
            return StagedUpload(filename, None, size, digest.hexdigest(), rejection)

        return StagedUpload(filename, tmp_path, size, digest.hexdigest(), None)
