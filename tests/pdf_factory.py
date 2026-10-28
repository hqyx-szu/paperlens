from __future__ import annotations

import math
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

_FONT_NAME = "STSong-Light"
_FONT_SIZE = 11
_LEFT_MARGIN = 50
_TOP_MARGIN = 790
_LINE_HEIGHT = 14
_MAX_WIDTH = 500

_DEFAULT_BODY = """NeurIPS 2017
Abstract
This paper studies sequence transduction with attention.
1 Introduction
Transformer models replace recurrence with self-attention.
DOI: 10.1000/example.2017"""

pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))


def _wrap_text(text: str) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and pdfmetrics.stringWidth(candidate, _FONT_NAME, _FONT_SIZE) > _MAX_WIDTH:
                lines.append(current)
                current = char
            else:
                current = candidate
        lines.append(current)
    return lines or [""]


def make_pdf_bytes(
    *,
    title: str = "Attention Is All You Need",
    authors: str = "Ashish Vaswani, Noam Shazeer",
    subject: str = "NeurIPS 2017",
    body: str | None = None,
    pages: int = 1,
    blank: bool = False,
    encrypted: bool = False,
) -> bytes:
    """Build a small PDF for upload/parser tests without touching the filesystem."""
    if pages < 1:
        raise ValueError("pages must be at least 1")

    buffer = BytesIO()
    encryption = StandardEncryption("secret") if encrypted else None
    canvas = Canvas(buffer, pagesize=A4, encrypt=encryption)
    canvas.setTitle(title)
    canvas.setAuthor(authors)
    canvas.setSubject(subject)

    if blank:
        for _ in range(pages):
            canvas.showPage()
        canvas.save()
        return buffer.getvalue()

    lines = _wrap_text(body if body is not None else _DEFAULT_BODY)
    lines_per_page = max(1, math.ceil(len(lines) / pages))
    page_lines = [
        lines[index : index + lines_per_page] for index in range(0, len(lines), lines_per_page)
    ]
    while len(page_lines) < pages:
        page_lines.append([])

    for page_index, current_lines in enumerate(page_lines[:pages], start=1):
        canvas.setFont(_FONT_NAME, _FONT_SIZE)
        y = _TOP_MARGIN
        if page_index == 1 and title:
            canvas.drawString(_LEFT_MARGIN, y, title)
            y -= _LINE_HEIGHT * 1.5
        for line in current_lines:
            canvas.drawString(_LEFT_MARGIN, y, line)
            y -= _LINE_HEIGHT
        canvas.showPage()

    canvas.save()
    return buffer.getvalue()


def upload(
    name: str,
    content: bytes,
    content_type: str = "application/pdf",
) -> tuple[str, bytes, str]:
    return name, content, content_type
