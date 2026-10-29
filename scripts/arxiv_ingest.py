"""从 arXiv 批量采集论文并导入 PaperLens。

用法（需要目标服务已启动，并用管理员账号）：

    # 只看检索结果，不下载、不写库
    uv run python -m scripts.arxiv_ingest --category cs.CL --max-results 5 --dry-run

    # 采集 20 篇 cs.IR 的最新论文
    PAPERLENS_ADMIN_EMAIL=admin@example.com PAPERLENS_ADMIN_PASSWORD='...' \\
        uv run python -m scripts.arxiv_ingest --category cs.IR --max-results 20

    # 关键词 + 分类，只取 2026 年以来
    uv run python -m scripts.arxiv_ingest --query "retrieval augmented generation" \\
        --category cs.CL --max-results 50 --start 0

流程：arXiv API 查询 → 下载 PDF → `POST /api/v1/admin/papers/import`
（SHA-256 去重、解析、切块、向量）→ 用 arXiv 元数据 `PATCH /api/v1/admin/papers/{id}`
修正标题/作者/年份/venue/摘要。

设计说明：
- 只依赖标准库（urllib），不引入新依赖；请求间隔默认 3 秒，遵守 arXiv 的访问约定。
- 文件名用 arXiv ID（去掉版本号），重复运行会被服务端 SHA-256 判重，不会产生副本。
- 每批 ≤20 个文件 / ≤100 MB（服务端限制），默认 10 个一批。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"
DEFAULT_USER_AGENT = "PaperLens/0.1 (arxiv ingest; contact: admin@example.com)"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
MAX_BATCH_FILES = 20
MAX_BATCH_BYTES = 100 * 1024 * 1024
# arXiv 的 CDN 会随机把个别连接限速到 ~15KB/s（换一条连接就恢复正常），
# 所以传输一开始就按速率判断：观察满 DOWNLOAD_SLOW_SECONDS 秒后若平均速率
# 仍低于 DOWNLOAD_MIN_BYTES_PER_SEC，就断开换连接重试；大文件在正常速率下
# 不会被误杀。
DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_SLOW_SECONDS = 10.0
DOWNLOAD_MIN_BYTES_PER_SEC = 100_000
DOWNLOAD_CHUNK = 64 * 1024
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class ArxivIngestError(RuntimeError):
    """采集或导入过程中的可预期错误（打印给用户，不抛栈）。"""


class _SlowDownloadError(RuntimeError):
    """单条连接慢到没有意义，断开后换连接重试。"""


class HttpLike(Protocol):
    """run() 需要的最小接口，测试可注入假实现。"""

    def get_bytes(self, url: str) -> bytes: ...

    def login(self, email: str, password: str) -> None: ...

    def import_pdfs(self, files: list[tuple[str, bytes]]) -> dict[str, Any]: ...

    def patch_paper(self, paper_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get_paper(self, paper_id: int) -> dict[str, Any]: ...


@dataclass
class ArxivEntry:
    arxiv_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    pdf_url: str
    doi: str | None = None
    venue: str | None = None
    categories: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{self.arxiv_id}.pdf"


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _arxiv_id_from_url(url: str) -> str:
    """`http://arxiv.org/abs/2501.01234v2` → `2501.01234`（去版本号，便于去重与命名）。"""
    raw = url.rstrip("/").rsplit("/", 1)[-1]
    return raw.split("v")[0] if "v" in raw else raw


def parse_atom(xml_bytes: bytes) -> list[ArxivEntry]:
    """解析 arXiv Atom 响应；缺字段的条目跳过而不是整体失败。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:  # pragma: no cover - 只在响应异常时触发
        raise ArxivIngestError(f"arXiv 返回的 XML 无法解析：{exc}") from exc

    entries: list[ArxivEntry] = []
    for node in root.findall(f"{ATOM}entry"):
        id_text = _clean(node.findtext(f"{ATOM}id") or "")
        title = _clean(node.findtext(f"{ATOM}title"))
        if not id_text or not title:
            continue
        arxiv_id = _arxiv_id_from_url(id_text)
        authors = [
            _clean(name.text)
            for author in node.findall(f"{ATOM}author")
            if (name := author.find(f"{ATOM}name")) is not None and _clean(name.text)
        ]
        published = _clean(node.findtext(f"{ATOM}published"))
        year: int | None = None
        if len(published) >= 4 and published[:4].isdigit():
            year = int(published[:4])
        pdf_url = ""
        for link in node.findall(f"{ATOM}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href") or ""
                break
        if not pdf_url:
            pdf_url = ARXIV_PDF.format(arxiv_id=arxiv_id)
        categories = [
            item.get("term", "") for item in node.findall(f"{ATOM}category") if item.get("term")
        ]
        entries.append(
            ArxivEntry(
                arxiv_id=arxiv_id,
                title=title[:500],
                authors=authors[:50],
                year=year,
                abstract=_clean(node.findtext(f"{ATOM}summary"))[:20000],
                pdf_url=pdf_url,
                doi=_clean(node.findtext(f"{ARXIV_NS}doi")) or None,
                venue=_clean(node.findtext(f"{ARXIV_NS}journal_ref"))[:255] or None,
                categories=categories,
            )
        )
    return entries


def build_search_query(category: str | None, keywords: str | None) -> str:
    """组 arXiv search_query：分类 + 全字段短语，二者都必须命中。"""
    parts: list[str] = []
    if category:
        parts.append(f"cat:{category.strip()}")
    phrase = _clean(keywords)
    if phrase:
        parts.append(f'all:"{phrase}"')
    return " AND ".join(parts) or "cat:cs.*"


def build_query_url(
    *,
    search_query: str,
    start: int = 0,
    max_results: int = 50,
    sort: str = "submittedDate",
) -> str:
    params = {
        "search_query": search_query,
        "start": str(start),
        "max_results": str(max_results),
        "sortBy": sort,
        "sortOrder": "descending",
    }
    return f"{ARXIV_API}?{urllib.parse.urlencode(params)}"


def _lower_headers(headers: Any) -> dict[str, str]:
    """urllib 的 headers 大小写不定（dict() 会小写），统一成小写键的普通字典。"""
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def build_multipart(
    files: list[tuple[str, bytes]],
    field_name: str = "files",
    boundary: str = "----PaperLensBoundary7MA4YWxkTrZu0gW",
) -> tuple[bytes, str]:
    """拼 multipart/form-data；只用标准库，便于离线测试。"""
    lines: list[bytes] = []
    for filename, payload in files:
        safe = filename.replace('"', "_")
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{safe}"\r\n'.encode()
        )
        lines.append(b"Content-Type: application/pdf\r\n\r\n")
        lines.append(payload)
        lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())
    body = b"".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


class Http:
    """极简 HTTP 客户端：JSON + multipart + Cookie，全部基于 urllib。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookie: str | None = None

    def _absolute(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}"

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(
            url, data=data, method=method, headers={"User-Agent": DEFAULT_USER_AGENT}
        )
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.status, _lower_headers(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, _lower_headers(exc.headers), exc.read()
        except urllib.error.URLError as exc:
            raise ArxivIngestError(f"请求失败：{url}（{exc.reason}）") from exc

    def get_bytes(
        self,
        url: str,
        *,
        attempts: int = DOWNLOAD_ATTEMPTS,
        slow_after: float = DOWNLOAD_SLOW_SECONDS,
        min_rate: float = DOWNLOAD_MIN_BYTES_PER_SEC,
    ) -> bytes:
        """下载 URL 内容；单条连接被限速时换连接重试。

        arXiv 的 CDN 会对部分连接做带宽限制（实测 ~15KB/s，换连接后 ~1MB/s），
        所以按平均速率判断“慢连接”：观察满 ``slow_after`` 秒后仍然低于
        ``min_rate``（字节/秒）就断开重试。
        """
        tries = max(1, attempts)
        for _ in range(tries):
            try:
                return self._get_bytes_once(url, slow_after=slow_after, min_rate=min_rate)
            except _SlowDownloadError:
                continue
        raise ArxivIngestError(f"下载过慢，已重试 {tries} 次：{url}")

    def _get_bytes_once(
        self,
        url: str,
        *,
        slow_after: float,
        min_rate: float,
    ) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        started = time.monotonic()
        chunks: list[bytes] = []
        received = 0
        try:
            # socket 超时也用 slow_after：连接卡死时最多等这么久就换连接，
            # 而不是被 Http.timeout（120s，给本地 API 用）拖住。
            timeout = max(1.0, slow_after)
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise ArxivIngestError(f"下载失败（HTTP {response.status}）：{url}")
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        return b"".join(chunks)
                    chunks.append(chunk)
                    received += len(chunk)
                    elapsed = time.monotonic() - started
                    if elapsed > slow_after and received < min_rate * elapsed:
                        raise _SlowDownloadError(url)
        except urllib.error.HTTPError as exc:
            raise ArxivIngestError(f"下载失败（HTTP {exc.code}）：{url}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise _SlowDownloadError(url) from exc
            raise ArxivIngestError(f"请求失败：{url}（{exc.reason}）") from exc
        except TimeoutError as exc:
            raise _SlowDownloadError(url) from exc

    def _json(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        status, response_headers, body = self._request(
            method, self._absolute(path), data=data, headers=headers
        )
        parsed = json.loads(body or b"{}") if body else {}
        if status >= 400:
            message = (parsed.get("error") or {}).get("message") or f"HTTP {status}"
            raise ArxivIngestError(f"{path} 失败：{message}")
        cookies = response_headers.get("set-cookie")
        if cookies and "paperlens_session=" in cookies:
            self.cookie = cookies.split(";", 1)[0]
        return parsed

    def login(self, email: str, password: str) -> None:
        self._json("POST", "/api/v1/auth/login", {"email": email, "password": password})
        if not self.cookie:
            raise ArxivIngestError("登录成功但没有拿到 paperlens_session Cookie")

    def import_pdfs(self, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        body, content_type = build_multipart(files)
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        status, _, raw = self._request(
            "POST", self._absolute("/api/v1/admin/papers/import"), data=body, headers=headers
        )
        parsed = json.loads(raw or b"{}") if raw else {}
        if status >= 400:
            message = (parsed.get("error") or {}).get("message") or f"HTTP {status}"
            raise ArxivIngestError(f"导入失败：{message}")
        return parsed

    def patch_paper(self, paper_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json("PATCH", f"/api/v1/admin/papers/{paper_id}", payload)

    def get_paper(self, paper_id: int) -> dict[str, Any]:
        return self._json("GET", f"/api/v1/admin/papers/{paper_id}", None)


def metadata_payload(entry: ArxivEntry) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": entry.title,
        "authors": entry.authors,
        "abstract": entry.abstract,
    }
    if entry.year:
        payload["year"] = entry.year
    # arXiv 预印本通常没有 journal_ref；此时用主分类作为来源标识，
    # 避免 PDF 解析出的 "arXiv:xxxx [cs.CL] ..." 噪声留在 venue 里。
    venue = entry.venue or (f"arXiv {entry.categories[0]}" if entry.categories else "arXiv")
    payload["venue"] = venue[:255]
    return payload


def fetch_entries(
    *,
    search_query: str,
    start: int,
    max_results: int,
    sort: str,
    http: HttpLike,
) -> list[ArxivEntry]:
    url = build_query_url(
        search_query=search_query, start=start, max_results=max_results, sort=sort
    )
    return parse_atom(http.get_bytes(url))


def run(
    args: argparse.Namespace,
    *,
    http: HttpLike | None = None,
    sleep: Any = time.sleep,
    out: Any = print,
) -> dict[str, int]:
    http = http or Http(base_url=args.base_url)
    search_query = build_search_query(args.category, args.query)
    out(f"检索：{search_query}（start={args.start}，max={args.max_results}）")
    entries = fetch_entries(
        search_query=search_query,
        start=args.start,
        max_results=args.max_results,
        sort=args.sort,
        http=http,
    )
    out(f"命中 {len(entries)} 篇")
    if not entries:
        return {"created": 0, "duplicates": 0, "rejected": 0, "failed": 0}

    if args.dry_run:
        for entry in entries:
            out(f"  {entry.arxiv_id}  {entry.year or '----'}  {entry.title[:80]}")
        return {"created": 0, "duplicates": 0, "rejected": 0, "failed": 0}

    email = args.email or os.getenv("PAPERLENS_ADMIN_EMAIL")
    password = args.password or os.getenv("PAPERLENS_ADMIN_PASSWORD")
    if not email or not password:
        raise ArxivIngestError(
            "需要管理员账号：--email/--password 或 PAPERLENS_ADMIN_EMAIL/PAPERLENS_ADMIN_PASSWORD"
        )
    http.login(email, password)
    out("已登录管理员")

    stats = {"created": 0, "duplicates": 0, "rejected": 0, "failed": 0}
    batch_size = max(1, min(args.batch_size, MAX_BATCH_FILES))
    for offset in range(0, len(entries), batch_size):
        batch = entries[offset : offset + batch_size]
        files: list[tuple[str, bytes]] = []
        for entry in batch:
            try:
                files.append((entry.filename, http.get_bytes(entry.pdf_url)))
                out(f"  下载 {entry.filename}（{entry.title[:50]}）")
            except ArxivIngestError as exc:
                stats["failed"] += 1
                out(f"  跳过 {entry.arxiv_id}：{exc}")
            sleep(args.delay)

        if not files:
            continue
        total_bytes = sum(len(payload) for _, payload in files)
        if total_bytes > MAX_BATCH_BYTES:
            out(f"  本批 {total_bytes // 1024 // 1024} MB 超过 100 MB，拆分后重试")
            for filename, payload in files:
                result = http.import_pdfs([(filename, payload)])
                stats = _accumulate(
                    stats, result, batch, entry_by_file(batch), http, args, out, sleep
                )
                sleep(args.delay)
            continue

        result = http.import_pdfs(files)
        stats = _accumulate(stats, result, batch, entry_by_file(batch), http, args, out, sleep)
        sleep(args.delay)

    out(
        "完成：新建 {created}，重复 {duplicates}，拒绝 {rejected}，下载失败 {failed}".format(
            **stats
        )
    )
    return stats


def entry_by_file(batch: Iterable[ArxivEntry]) -> dict[str, ArxivEntry]:
    return {entry.filename: entry for entry in batch}


PROCESSING_STATUSES = ("queued", "parsing", "indexing")


def _wait_until_processed(
    http: HttpLike,
    paper_id: int,
    *,
    timeout: float,
    interval: float,
    sleep: Any,
    out: Any,
) -> str | None:
    """等论文离开处理中状态（导入返回 queued，处理中不能 PATCH 元数据）。"""
    if timeout <= 0:
        return None
    deadline = time.monotonic() + timeout
    while True:
        try:
            detail = http.get_paper(paper_id)
        except ArxivIngestError as exc:
            out(f"  查询 #{paper_id} 失败：{exc}")
            return None
        status = str(detail.get("status") or "")
        if status not in PROCESSING_STATUSES:
            return status
        if time.monotonic() >= deadline:
            out(f"  等待 #{paper_id} 处理超时（{timeout:g}s），跳过元数据修正")
            return None
        sleep(interval)


def _accumulate(
    stats: dict[str, int],
    result: dict[str, Any],
    batch: list[ArxivEntry],
    by_file: dict[str, ArxivEntry],
    http: HttpLike,
    args: argparse.Namespace,
    out: Any,
    sleep: Any,
) -> dict[str, int]:
    stats["created"] += int(result.get("created", 0))
    stats["duplicates"] += int(result.get("duplicates", 0))
    stats["rejected"] += int(result.get("rejected", 0))
    for item in result.get("items", []):
        if item.get("error"):
            out(f"  拒绝 {item.get('filename')}：{item['error']}")
        if item.get("duplicate"):
            out(f"  已存在 {item.get('filename')}")
        paper_id = item.get("paper_id")
        entry = by_file.get(str(item.get("filename", "")))
        if not paper_id or not entry or item.get("duplicate") or args.no_metadata:
            continue
        status = _wait_until_processed(
            http,
            int(paper_id),
            timeout=float(getattr(args, "wait", 180)),
            interval=float(getattr(args, "poll_interval", 3)),
            sleep=sleep,
            out=out,
        )
        if status != "ready":
            if status:
                out(f"  #{paper_id} 状态 {status}，跳过元数据修正")
            continue
        try:
            http.patch_paper(int(paper_id), metadata_payload(entry))
            out(f"  元数据已修正 #{paper_id} {entry.title[:50]}")
        except ArxivIngestError as exc:
            out(f"  元数据修正失败 #{paper_id}：{exc}")
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.arxiv_ingest",
        description="从 arXiv 批量采集论文并导入 PaperLens（经管理员导入接口）。",
    )
    parser.add_argument(
        "--query", default="", help='关键词短语，例如 "retrieval augmented generation"'
    )
    parser.add_argument(
        "--category",
        default="cs.*",
        help="arXiv 分类，例如 cs.CL / cs.IR / cs.LG；默认 cs.*（全部计算机领域）",
    )
    parser.add_argument("--max-results", type=int, default=20, help="本次最多取多少篇（默认 20）")
    parser.add_argument("--start", type=int, default=0, help="结果偏移，用于翻页（默认 0）")
    parser.add_argument(
        "--sort",
        choices=("relevance", "lastUpdatedDate", "submittedDate"),
        default="submittedDate",
        help="排序，默认按提交时间倒序",
    )
    parser.add_argument("--batch-size", type=int, default=10, help="每批导入数量（≤20，默认 10）")
    parser.add_argument(
        "--delay", type=float, default=3.0, help="每次 arXiv 请求后的等待秒数（默认 3）"
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=180.0,
        help="导入后等待论文处理完成以修正元数据的最长秒数（0 表示不等、不修正）",
    )
    parser.add_argument("--poll-interval", type=float, default=3.0, help="轮询处理状态的间隔秒数")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="PaperLens 服务地址")
    parser.add_argument("--email", default="", help="管理员邮箱（或用 PAPERLENS_ADMIN_EMAIL）")
    parser.add_argument(
        "--password", default="", help="管理员密码（或用 PAPERLENS_ADMIN_PASSWORD）"
    )
    parser.add_argument("--no-metadata", action="store_true", help="导入后不修正 arXiv 元数据")
    parser.add_argument("--dry-run", action="store_true", help="只列检索结果，不下载、不写库")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_results <= 0 or args.max_results > 500:
        print("--max-results 需在 1–500 之间", file=sys.stderr)
        return 2
    try:
        run(args)
    except ArxivIngestError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
