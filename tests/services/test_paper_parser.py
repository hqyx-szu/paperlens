from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.paper_parser import (
    PdfParseError,
    extract_pdf_pages,
    normalize_text,
    parse_pdf,
)
from tests.pdf_factory import make_pdf_bytes


def _write_pdf(tmp_path: Path, **kwargs: object) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(make_pdf_bytes(**kwargs))
    return path


def test_extracts_metadata_and_abstract(tmp_path: Path) -> None:
    parsed = parse_pdf(_write_pdf(tmp_path), fallback_title="fallback")

    assert parsed.title == "Attention Is All You Need"
    assert parsed.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert parsed.year == 2017
    assert parsed.venue == "NeurIPS 2017"
    assert "sequence transduction with attention" in (parsed.abstract or "")
    assert parsed.doi == "10.1000/example.2017"
    assert re.fullmatch(r"[0-9a-f]{64}", parsed.content_hash)


def test_chunks_have_pages_and_overlap(tmp_path: Path) -> None:
    body = "Transformer models replace recurrence with self-attention. " * 70
    parsed = parse_pdf(_write_pdf(tmp_path, body=body, pages=3), fallback_title="fallback")

    assert {chunk.page for chunk in parsed.chunks} == {1, 2, 3}
    assert all(50 <= chunk.char_count <= 2000 for chunk in parsed.chunks)
    assert all(chunk.char_count == len(chunk.content) for chunk in parsed.chunks)

    by_page: dict[int | None, list[str]] = {}
    for chunk in parsed.chunks:
        by_page.setdefault(chunk.page, []).append(chunk.content)
    for contents in by_page.values():
        for previous, current in zip(contents, contents[1:], strict=False):
            assert previous[-150:] == current[:150]


def test_chunk_index_is_contiguous(tmp_path: Path) -> None:
    body = "Attention is all you need. " * 200
    parsed = parse_pdf(_write_pdf(tmp_path, body=body, pages=2), fallback_title="fallback")

    indices = list(range(len(parsed.chunks)))
    assert indices == list(range(indices[0], indices[-1] + 1))
    assert [chunk.page for chunk in parsed.chunks] == sorted(chunk.page for chunk in parsed.chunks)


def test_blank_pdf_raises_no_text_layer(tmp_path: Path) -> None:
    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_write_pdf(tmp_path, blank=True), fallback_title="fallback")

    assert exc_info.value.code == "no_text_layer"


def test_corrupt_pdf_raises_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a valid pdf")

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(path, fallback_title="fallback")

    assert exc_info.value.code == "corrupt"


def test_encrypted_pdf_raises_encrypted(tmp_path: Path) -> None:
    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_write_pdf(tmp_path, encrypted=True), fallback_title="fallback")

    assert exc_info.value.code == "encrypted"


def test_title_falls_back_to_filename(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, title="")
    parsed = parse_pdf(path, fallback_title="from-filename")
    assert parsed.title == "from-filename"

    parsed_without_fallback = parse_pdf(path, fallback_title="")
    assert parsed_without_fallback.title == "未命名论文"


def test_content_hash_is_stable(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path)
    assert (
        parse_pdf(path, fallback_title="fallback").content_hash
        == parse_pdf(path, fallback_title="fallback").content_hash
    )


def test_extract_pdf_pages_keeps_page_numbers(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path,
        body="First page text. " * 20 + "\nSecond page text. " * 20,
        pages=2,
    )
    pages = extract_pdf_pages(path)

    assert [page for page, _ in pages] == [1, 2]
    assert pages[0][1]


def test_extract_pdf_pages_respects_max_pages(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, pages=51)

    with pytest.raises(PdfParseError) as exc_info:
        extract_pdf_pages(path, max_pages=50)

    assert exc_info.value.code == "too_many_pages"


def test_normalize_text_drops_lone_surrogates() -> None:
    assert normalize_text("attention \ud83d models") == "attention models"
    assert normalize_text("\ud800\udc00ok\udfff").encode("utf-8") == b"ok"
