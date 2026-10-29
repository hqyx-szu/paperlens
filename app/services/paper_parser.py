from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pypdf import PdfReader

_CHUNK_TARGET = 1000
_CHUNK_OVERLAP = 150
_CHUNK_MIN = 50
_CHUNK_MAX = 2000
_ABSTRACT_MAX = 20000

# 损坏的 ToUnicode 映射会抽出孤立代理项（\ud800-\udfff），它们无法编码成 UTF-8，
# 一路保留会让 content_hash 计算与入库直接抛 UnicodeEncodeError。
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_HORIZONTAL_WS_RE = re.compile(r"[^\S\n]+")
_MULTI_BLANK_LINE_RE = re.compile(r"\n{3,}")
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_AUTHOR_SPLIT_RE = re.compile(r"[,;、]| and ", re.IGNORECASE)
_SECTION_RE = re.compile(
    r"^(?:\d+[.)]?\s*)?(?:abstract|摘要|introduction|引言|keywords?|关键词)",
    re.IGNORECASE,
)
_ABSTRACT_START_RE = re.compile(r"^(?:abstract|摘要)\s*[:：]?\s*(.*)$", re.IGNORECASE)
_ABSTRACT_STOP_RE = re.compile(
    r"^(?:\d+\.?\s*)?(?:introduction|keywords?)\b|^(?:引言|关键词)\s*[:：]?$",
    re.IGNORECASE,
)
_VENUE_RE = re.compile(
    r"Proceedings|Conference|Journal|Transactions|Symposium|Workshop|"
    r"arXiv|IEEE|ACM|NeurIPS|ICML|ICLR|CVPR|ACL",
    re.IGNORECASE,
)
_TRAILING_DOI_PUNCTUATION = ".,;:)]}，。；：）】"


class PdfParseError(Exception):
    def __init__(
        self,
        code: Literal["corrupt", "encrypted", "no_text_layer", "too_many_pages"],
        user_message: str,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    page: int | None
    content: str
    char_count: int


@dataclass(frozen=True, slots=True)
class ParsedPaper:
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    abstract: str | None
    doi: str | None
    content_hash: str
    chunks: list[ParsedChunk]


def normalize_text(value: str) -> str:
    value = _SURROGATE_RE.sub("", value)
    value = value.replace("\x00", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _HORIZONTAL_WS_RE.sub(" ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = _MULTI_BLANK_LINE_RE.sub("\n\n", value)
    return value.strip()


def _truncate(value: str, limit: int) -> str:
    return value[:limit]


def _metadata_value(metadata: object | None, key: str, attribute: str) -> str | None:
    if metadata is None:
        return None
    value: object | None = None
    try:
        value = metadata.get(key)  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        value = None
    if value is None:
        value = getattr(metadata, attribute, None)
    if value is None:
        return None
    text = normalize_text(str(value))
    return text or None


def _read_pdf(
    path: Path,
    *,
    max_pages: int | None = None,
) -> tuple[list[tuple[int, str]], object | None]:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfParseError("encrypted", "PDF 已加密，无法解析")
        if max_pages is not None and len(reader.pages) > max_pages:
            raise PdfParseError("too_many_pages", "PDF 页数超过限制")

        pages: list[tuple[int, str]] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = normalize_text(page.extract_text() or "")
            if text:
                pages.append((page_number, text))
        return pages, reader.metadata
    except PdfParseError:
        raise
    except Exception as exc:
        raise PdfParseError("corrupt", "文件已损坏，无法解析 PDF") from exc


def extract_pdf_pages(
    path: Path,
    *,
    max_pages: int | None = None,
) -> list[tuple[int, str]]:
    return _read_pdf(path, max_pages=max_pages)[0]


def _first_meaningful_title_line(pages: list[tuple[int, str]]) -> str | None:
    if not pages:
        return None
    for raw_line in pages[0][1].splitlines():
        line = raw_line.strip()
        if not 3 <= len(line) <= 200:
            continue
        lowered = line.lower()
        if _SECTION_RE.match(line) or _VENUE_RE.search(line):
            continue
        if "doi" in lowered or "arxiv" in lowered or "http" in lowered:
            continue
        if re.fullmatch(r"[\d\W_]+", line):
            continue
        if len(line) > 40 and line[-1] in ".。!?":
            continue
        return _truncate(line, 500)
    return None


def _split_authors(value: str | None) -> list[str]:
    if not value:
        return []
    seen: set[str] = set()
    authors: list[str] = []
    for raw_name in _AUTHOR_SPLIT_RE.split(value):
        name = raw_name.strip()
        if not name:
            continue
        name = _truncate(name, 200)
        if name in seen:
            continue
        seen.add(name)
        authors.append(name)
        if len(authors) == 50:
            break
    return authors


def _looks_like_author_line(line: str) -> bool:
    if not 3 <= len(line) <= 200:
        return False
    lowered = line.lower()
    if _SECTION_RE.match(line) or _VENUE_RE.search(line):
        return False
    if "doi" in lowered or "arxiv" in lowered or "@" in line:
        return False
    return not lowered.startswith(("this paper", "we propose", "we present", "we introduce"))


def _authors_from_lines(pages: list[tuple[int, str]], title: str) -> list[str]:
    if not pages:
        return []
    lines = [line.strip() for line in pages[0][1].splitlines() if line.strip()]
    started = False
    authors: list[str] = []
    for line in lines:
        if not started:
            if line == title or title in line:
                started = True
            continue
        if _looks_like_author_line(line):
            authors.extend(_split_authors(line))
            if len(authors) >= 3:
                break
    return _split_authors(", ".join(authors))


def _extract_year(pages: list[tuple[int, str]], metadata: object | None) -> int | None:
    for _, text in pages[:2]:
        for line in text.splitlines():
            lowered = line.lower()
            if "doi" in lowered or "arxiv" in lowered:
                continue
            for match in _YEAR_RE.finditer(line):
                year = int(match.group())
                if 1900 <= year <= 2100:
                    return year

    created = getattr(metadata, "creation_date", None) if metadata is not None else None
    if isinstance(created, datetime):
        return created.year if 1900 <= created.year <= 2100 else None
    if created:
        match = _YEAR_RE.search(str(created))
        if match:
            year = int(match.group())
            if 1900 <= year <= 2100:
                return year
    return None


def _looks_like_venue(value: str | None) -> bool:
    return bool(value and len(value) <= 255 and _VENUE_RE.search(value))


def _extract_venue(pages: list[tuple[int, str]], metadata: object | None) -> str | None:
    subject = _metadata_value(metadata, "/Subject", "subject")
    if _looks_like_venue(subject):
        return _truncate(subject or "", 255)
    for _, text in pages[:3]:
        for line in text.splitlines():
            line = line.strip()
            if _looks_like_venue(line) and not _SECTION_RE.match(line):
                return _truncate(line, 255)
    return None


def _extract_abstract(pages: list[tuple[int, str]]) -> str | None:
    for _, text in pages[:3]:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            match = _ABSTRACT_START_RE.match(line.strip())
            if match is None:
                continue
            parts: list[str] = []
            remainder = match.group(1).strip()
            if remainder:
                parts.append(remainder)
            for following in lines[index + 1 :]:
                if _ABSTRACT_STOP_RE.match(following.strip()):
                    break
                parts.append(following)
            abstract = "\n".join(parts).strip()
            if abstract:
                return _truncate(abstract, _ABSTRACT_MAX)
    return None


def _extract_doi(pages: list[tuple[int, str]]) -> str | None:
    for _, text in pages:
        for match in _DOI_RE.finditer(text):
            doi = match.group(0).rstrip(_TRAILING_DOI_PUNCTUATION)
            if doi:
                return _truncate(doi, 255)
    return None


def _find_break(text: str, lower: int, upper: int) -> int:
    for index in range(min(upper, len(text)) - 1, lower - 1, -1):
        if text[index] in "\n。.！？":
            return index
    return -1


def _chunk_page_text(text: str, page: int) -> list[ParsedChunk]:
    if len(text) < _CHUNK_MIN:
        return []

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        if length <= start + _CHUNK_TARGET:
            end = length
        else:
            break_at = _find_break(
                text,
                start + _CHUNK_TARGET // 2,
                start + _CHUNK_TARGET + 100,
            )
            end = break_at + 1 if break_at >= 0 else start + _CHUNK_TARGET

        if end - start < _CHUNK_MIN:
            break
        if end - start > _CHUNK_MAX:
            end = start + _CHUNK_MAX
        chunks.append(text[start:end])
        if end >= length:
            break
        start = max(0, end - _CHUNK_OVERLAP)

    if chunks and len(chunks[-1]) < _CHUNK_MIN:
        chunks.pop()
    if not chunks:
        return []
    return [ParsedChunk(page=page, content=content, char_count=len(content)) for content in chunks]


def _extract_chunks(pages: list[tuple[int, str]]) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    for page_number, text in pages:
        chunks.extend(_chunk_page_text(text, page_number))
    return chunks


def parse_pdf(path: Path, *, fallback_title: str) -> ParsedPaper:
    pages, metadata = _read_pdf(path)
    if not pages:
        raise PdfParseError(
            "no_text_layer",
            "PDF 没有可提取的文本层（扫描件/图片型 PDF 暂不支持）",
        )

    chunks = _extract_chunks(pages)
    if not chunks:
        raise PdfParseError(
            "no_text_layer",
            "PDF 没有可提取的文本层（扫描件/图片型 PDF 暂不支持）",
        )

    metadata_title = _metadata_value(metadata, "/Title", "title")
    title = metadata_title or _first_meaningful_title_line(pages)
    if not title:
        title = fallback_title.strip() or "未命名论文"
    title = _truncate(title, 500) or "未命名论文"

    metadata_authors = _metadata_value(metadata, "/Author", "author")
    authors = _split_authors(metadata_authors)
    if not authors:
        authors = _authors_from_lines(pages, title)

    content = "\n".join(text for _, text in pages)
    return ParsedPaper(
        title=title,
        authors=authors,
        year=_extract_year(pages, metadata),
        venue=_extract_venue(pages, metadata),
        abstract=_extract_abstract(pages),
        doi=_extract_doi(pages),
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        chunks=chunks,
    )


__all__ = [
    "ParsedChunk",
    "ParsedPaper",
    "PdfParseError",
    "extract_pdf_pages",
    "normalize_text",
    "parse_pdf",
]
