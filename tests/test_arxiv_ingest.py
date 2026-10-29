from __future__ import annotations

import json
import urllib.request
from typing import Any

from scripts.arxiv_ingest import (
    ArxivEntry,
    build_multipart,
    build_parser,
    build_query_url,
    build_search_query,
    main,
    metadata_payload,
    parse_atom,
    run,
)

ATOM_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2501.01234v2</id>
    <published>2025-01-02T18:00:00Z</published>
    <title>Attention
      Is All You Need</title>
    <summary>We propose a new
      simple network architecture.</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <link title="pdf" rel="related" href="http://arxiv.org/pdf/2501.01234v2"/>
    <arxiv:doi>10.1000/xyz</arxiv:doi>
    <arxiv:journal_ref>NeurIPS 2025</arxiv:journal_ref>
    <category term="cs.CL"/>
    <category term="cs.LG"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2502.99999v1</id>
    <published>2025-02-03T00:00:00Z</published>
    <title>Retrieval Augmented Generation for Long Documents</title>
    <summary>RAG study.</summary>
    <author><name>Li Ming</name></author>
    <category term="cs.IR"/>
  </entry>
</feed>
"""


class FakeHttp:
    def __init__(
        self,
        *,
        atom: bytes = ATOM_SAMPLE,
        pdf: bytes = b"%PDF-1.4 fake",
        import_result: dict[str, Any] | None = None,
        patch_error: Exception | None = None,
        statuses: list[str] | None = None,
    ) -> None:
        self.atom = atom
        self.pdf = pdf
        self.import_result = import_result or {
            "items": [],
            "created": 0,
            "duplicates": 0,
            "rejected": 0,
        }
        self.patch_error = patch_error
        self.statuses = list(statuses or ["ready"])
        self.urls: list[str] = []
        self.polls: list[int] = []
        self.logged: tuple[str, str] | None = None
        self.imports: list[list[tuple[str, bytes]]] = []
        self.patches: list[tuple[int, dict[str, Any]]] = []

    def get_bytes(self, url: str) -> bytes:
        self.urls.append(url)
        return self.atom if "export.arxiv.org" in url else self.pdf

    def login(self, email: str, password: str) -> None:
        self.logged = (email, password)

    def import_pdfs(self, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        self.imports.append(files)
        return self.import_result

    def patch_paper(self, paper_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if self.patch_error is not None:
            raise self.patch_error
        self.patches.append((paper_id, payload))
        return {}

    def get_paper(self, paper_id: int) -> dict[str, Any]:
        self.polls.append(paper_id)
        status = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        return {"id": paper_id, "status": status}


def _args(*extra: str) -> Any:
    args = build_parser().parse_args(list(extra))
    args.delay = 0.0
    args.email = "admin@example.com"
    args.password = "secret123"
    return args


def test_parse_atom_extracts_fields() -> None:
    entries = parse_atom(ATOM_SAMPLE)

    assert [entry.arxiv_id for entry in entries] == ["2501.01234", "2502.99999"]
    first, second = entries
    assert first.title == "Attention Is All You Need"
    assert first.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert first.year == 2025
    assert first.abstract == "We propose a new simple network architecture."
    assert first.pdf_url == "http://arxiv.org/pdf/2501.01234v2"
    assert first.doi == "10.1000/xyz"
    assert first.venue == "NeurIPS 2025"
    assert first.categories == ["cs.CL", "cs.LG"]
    assert first.filename == "2501.01234.pdf"
    # 没有 pdf link 的条目回退到标准 PDF 地址
    assert second.pdf_url.endswith("/pdf/2502.99999")
    assert second.doi is None and second.venue is None


def test_parse_atom_skips_invalid_entries() -> None:
    broken = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>no id</title></entry>
      <entry><id>http://arxiv.org/abs/2503.00001v1</id></entry>
    </feed>"""

    assert parse_atom(broken) == []


def test_build_search_query() -> None:
    assert build_search_query("cs.CL", "") == "cat:cs.CL"
    assert build_search_query("", "retrieval augmented generation") == (
        'all:"retrieval augmented generation"'
    )
    assert build_search_query("cs.IR", "  rag  ") == 'cat:cs.IR AND all:"rag"'
    assert build_search_query(None, None) == "cat:cs.*"


def test_build_query_url_encodes_params() -> None:
    url = build_query_url(search_query='cat:cs.CL AND all:"rag"', start=10, max_results=5)

    assert url.startswith("https://export.arxiv.org/api/query?")
    assert "start=10" in url
    assert "max_results=5" in url
    assert "sortBy=submittedDate" in url
    assert "sortOrder=descending" in url
    assert "search_query=cat%3Acs.CL" in url


def test_build_multipart_structure() -> None:
    body, content_type = build_multipart([("2501.01234.pdf", b"%PDF-1.4 data")])

    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=", 1)[1]
    assert b'name="files"; filename="2501.01234.pdf"' in body
    assert b"Content-Type: application/pdf" in body
    assert b"%PDF-1.4 data" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_metadata_payload_only_includes_present_fields() -> None:
    entry = ArxivEntry(
        arxiv_id="2501.01234",
        title="T",
        authors=["A"],
        year=None,
        abstract="S",
        pdf_url="u",
    )

    payload = metadata_payload(entry)
    assert payload["title"] == "T"
    assert payload["authors"] == ["A"]
    assert payload["abstract"] == "S"
    assert "year" not in payload
    assert payload["venue"] == "arXiv"  # 无 journal_ref、无分类时的兜底

    entry.year = 2025
    entry.categories = ["cs.CL"]
    assert metadata_payload(entry)["year"] == 2025
    assert metadata_payload(entry)["venue"] == "arXiv cs.CL"

    entry.venue = "NeurIPS"
    assert metadata_payload(entry)["venue"] == "NeurIPS"


def test_dry_run_does_not_login_or_download() -> None:
    http = FakeHttp()
    lines: list[str] = []

    stats = run(
        _args("--dry-run", "--category", "cs.CL", "--max-results", "2"),
        http=http,
        sleep=lambda _: None,
        out=lines.append,
    )

    assert stats == {"created": 0, "duplicates": 0, "rejected": 0, "failed": 0}
    assert http.logged is None
    assert http.imports == []
    assert all("export.arxiv.org" in url for url in http.urls)  # 只查了 API，没下 PDF
    assert any("2501.01234" in line for line in lines)


def test_run_imports_and_patches_metadata() -> None:
    http = FakeHttp(
        import_result={
            "items": [
                {
                    "filename": "2501.01234.pdf",
                    "paper_id": 42,
                    "status": "queued",
                    "duplicate": False,
                    "is_archived": False,
                    "error": None,
                }
            ],
            "created": 1,
            "duplicates": 0,
            "rejected": 0,
        }
    )
    lines: list[str] = []

    stats = run(
        _args("--category", "cs.CL", "--max-results", "2", "--batch-size", "1"),
        http=http,
        sleep=lambda _: None,
        out=lines.append,
    )

    assert http.logged == ("admin@example.com", "secret123")
    assert len(http.imports) == 2  # 两篇 → 两批（每批 1 个）
    assert http.imports[0][0][0] == "2501.01234.pdf"
    assert http.imports[0][0][1].startswith(b"%PDF")
    assert stats["created"] == 2  # fake 每批都返回 created=1
    assert http.patches[0][0] == 42
    assert http.patches[0][1]["title"] == "Attention Is All You Need"
    assert http.patches[0][1]["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert http.patches[0][1]["year"] == 2025


def test_duplicates_are_not_patched() -> None:
    http = FakeHttp(
        import_result={
            "items": [
                {
                    "filename": "2501.01234.pdf",
                    "paper_id": 42,
                    "status": "ready",
                    "duplicate": True,
                    "is_archived": False,
                    "error": None,
                }
            ],
            "created": 0,
            "duplicates": 1,
            "rejected": 0,
        }
    )

    stats = run(
        _args("--category", "cs.CL", "--max-results", "2", "--batch-size", "1"),
        http=http,
        sleep=lambda _: None,
        out=lambda _: None,
    )

    assert stats["duplicates"] == 2  # 样例 Atom 有 2 条，各占一批
    assert http.patches == []


def test_metadata_patch_failure_does_not_abort(capsys) -> None:
    from scripts.arxiv_ingest import ArxivIngestError

    http = FakeHttp(
        import_result={
            "items": [
                {
                    "filename": "2501.01234.pdf",
                    "paper_id": 7,
                    "status": "queued",
                    "duplicate": False,
                    "is_archived": False,
                    "error": None,
                }
            ],
            "created": 1,
            "duplicates": 0,
            "rejected": 0,
        },
        patch_error=ArxivIngestError("PATCH 失败"),
    )
    lines: list[str] = []

    stats = run(
        _args("--category", "cs.CL", "--max-results", "1"),
        http=http,
        sleep=lambda _: None,
        out=lines.append,
    )

    assert stats["created"] == 1
    assert any("元数据修正失败" in line for line in lines)


def test_missing_credentials_raises() -> None:
    import pytest

    from scripts.arxiv_ingest import ArxivIngestError

    args = _args("--max-results", "1")
    args.email = ""
    args.password = ""

    with pytest.raises(ArxivIngestError):
        run(args, http=FakeHttp(), sleep=lambda _: None, out=lambda _: None)


def test_main_rejects_bad_max_results() -> None:
    assert main(["--max-results", "0"]) == 2
    assert main(["--max-results", "1000"]) == 2


def test_no_metadata_flag_skips_patch() -> None:
    http = FakeHttp(
        import_result={
            "items": [
                {
                    "filename": "2501.01234.pdf",
                    "paper_id": 9,
                    "status": "queued",
                    "duplicate": False,
                    "is_archived": False,
                    "error": None,
                }
            ],
            "created": 1,
            "duplicates": 0,
            "rejected": 0,
        }
    )

    run(
        _args("--category", "cs.CL", "--max-results", "1", "--no-metadata"),
        http=http,
        sleep=lambda _: None,
        out=lambda _: None,
    )

    assert http.patches == []


def test_waits_for_processing_before_patching_metadata() -> None:
    http = FakeHttp(
        statuses=["queued", "parsing", "ready"],
        import_result={
            "items": [
                {
                    "filename": "2501.01234.pdf",
                    "paper_id": 11,
                    "status": "queued",
                    "duplicate": False,
                    "is_archived": False,
                    "error": None,
                }
            ],
            "created": 1,
            "duplicates": 0,
            "rejected": 0,
        },
    )

    run(
        _args("--category", "cs.CL", "--max-results", "1", "--wait", "60"),
        http=http,
        sleep=lambda _: None,
        out=lambda _: None,
    )

    assert http.polls == [11, 11, 11]  # queued → parsing → ready
    assert [pid for pid, _ in http.patches] == [11]


def test_failed_paper_skips_metadata() -> None:
    http = FakeHttp(
        statuses=["failed"],
        import_result={
            "items": [
                {
                    "filename": "2501.01234.pdf",
                    "paper_id": 12,
                    "status": "queued",
                    "duplicate": False,
                    "is_archived": False,
                    "error": None,
                }
            ],
            "created": 1,
            "duplicates": 0,
            "rejected": 0,
        },
    )
    lines: list[str] = []

    run(
        _args("--category", "cs.CL", "--max-results", "1"),
        http=http,
        sleep=lambda _: None,
        out=lines.append,
    )

    assert http.patches == []
    assert any("状态 failed" in line for line in lines)


def test_wait_zero_disables_metadata_patch() -> None:
    http = FakeHttp(
        import_result={
            "items": [
                {
                    "filename": "2501.01234.pdf",
                    "paper_id": 13,
                    "status": "queued",
                    "duplicate": False,
                    "is_archived": False,
                    "error": None,
                }
            ],
            "created": 1,
            "duplicates": 0,
            "rejected": 0,
        },
    )

    run(
        _args("--category", "cs.CL", "--max-results", "1", "--wait", "0"),
        http=http,
        sleep=lambda _: None,
        out=lambda _: None,
    )

    assert http.polls == []
    assert http.patches == []


def test_error_body_is_reported() -> None:
    from scripts.arxiv_ingest import Http

    class _Response:
        def __init__(self) -> None:
            self.status = 200
            self.headers = {"Content-Type": "application/json"}
            self.body = json.dumps({"ok": True}).encode()

    class _Http(Http):
        def _request(self, method, url, *, data=None, headers=None):
            return (
                500,
                {"Content-Type": "application/json"},
                json.dumps({"error": {"code": "llm_failed", "message": "模型失败"}}).encode(),
            )

    http = _Http()

    import pytest

    from scripts.arxiv_ingest import ArxivIngestError

    with pytest.raises(ArxivIngestError) as excinfo:
        http.patch_paper(1, {})
    assert "模型失败" in str(excinfo.value)


class _FakeClock:
    """假时钟：不做真实等待，用单调递增的假时间判断“这条连接是不是太慢”。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        return None


class _SlowResponse:
    """每次 read 都消耗 5 秒，模拟被 arXiv CDN 限速的连接。"""

    status = 200

    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.closed = False

    def __enter__(self) -> _SlowResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        self.closed = True
        return False

    def read(self, size: int = -1) -> bytes:
        self.clock.now += 5.0
        return b"x" * 8


class _FastResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_done = False

    def __enter__(self) -> _FastResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        if self.read_done:
            return b""
        self.read_done = True
        return self.payload


class _SteadyResponse:
    """按固定速率吐数据：每个分块推进固定假时间，用来验证“大文件但速率正常”。"""

    status = 200

    def __init__(
        self,
        clock: _FakeClock,
        *,
        chunk_size: int,
        chunks: int,
        seconds_per_chunk: float,
    ) -> None:
        self.clock = clock
        self.chunk_size = chunk_size
        self.chunks = chunks
        self.seconds_per_chunk = seconds_per_chunk
        self.sent = 0

    def __enter__(self) -> _SteadyResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        if self.sent >= self.chunks:
            return b""
        self.clock.now += self.seconds_per_chunk
        self.sent += 1
        return b"x" * self.chunk_size


def test_get_bytes_retries_slow_connection(monkeypatch) -> None:
    import scripts.arxiv_ingest as arxiv_ingest

    clock = _FakeClock()
    monkeypatch.setattr(arxiv_ingest, "time", clock)
    slow = _SlowResponse(clock)
    urls: list[str] = []

    def fake_urlopen(request, timeout=None):
        urls.append(request.full_url)
        return slow if len(urls) == 1 else _FastResponse(b"%PDF-1.4 ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    http = arxiv_ingest.Http()

    payload = http.get_bytes("https://arxiv.org/pdf/2501.01234")

    assert payload == b"%PDF-1.4 ok"
    assert urls == ["https://arxiv.org/pdf/2501.01234"] * 2
    assert slow.closed is True


def test_get_bytes_reports_when_all_attempts_are_slow(monkeypatch) -> None:
    import pytest

    import scripts.arxiv_ingest as arxiv_ingest

    clock = _FakeClock()
    monkeypatch.setattr(arxiv_ingest, "time", clock)
    calls = 0

    def fake_urlopen(request, timeout=None):
        nonlocal calls
        calls += 1
        return _SlowResponse(clock)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    http = arxiv_ingest.Http()

    with pytest.raises(arxiv_ingest.ArxivIngestError) as excinfo:
        http.get_bytes("https://arxiv.org/pdf/2501.01234", attempts=2)

    assert "已重试 2 次" in str(excinfo.value)
    assert calls == 2


def test_get_bytes_retries_after_timeout(monkeypatch) -> None:
    import scripts.arxiv_ingest as arxiv_ingest

    calls = 0

    def fake_urlopen(request, timeout=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("timed out")
        return _FastResponse(b"%PDF-1.4 ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    http = arxiv_ingest.Http()

    assert http.get_bytes("https://arxiv.org/pdf/2501.01234") == b"%PDF-1.4 ok"
    assert calls == 2


def test_get_bytes_keeps_large_download_at_acceptable_rate(monkeypatch) -> None:
    """大文件（2MB）耗时超过观察窗口，但速率正常，不应被判为慢连接。"""

    import scripts.arxiv_ingest as arxiv_ingest

    clock = _FakeClock()
    monkeypatch.setattr(arxiv_ingest, "time", clock)
    calls = 0

    def fake_urlopen(request, timeout=None):
        nonlocal calls
        calls += 1
        # 32 × 64KB = 2MB，每块 0.5 秒 → 约 131KB/s，观察满 10 秒后仍高于阈值。
        return _SteadyResponse(clock, chunk_size=64 * 1024, chunks=32, seconds_per_chunk=0.5)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    http = arxiv_ingest.Http()

    payload = http.get_bytes("https://arxiv.org/pdf/2501.01234")

    assert len(payload) == 32 * 64 * 1024
    assert calls == 1
