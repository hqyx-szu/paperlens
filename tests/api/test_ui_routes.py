import json
from pathlib import Path

from fastapi import FastAPI
from httpx import AsyncClient

CONTRACT = Path(__file__).resolve().parents[2] / "doc" / "paperlens-openapi.json"
METHODS = {"get", "post", "patch", "delete"}


async def test_session_alias_redirects(client: AsyncClient) -> None:
    response = await client.get("/sessions/42")

    assert response.status_code == 307
    assert response.headers["location"] == "/app#/sessions/42"


async def test_repo_alias_redirects(client: AsyncClient) -> None:
    response = await client.get("/repos/7")

    assert response.status_code == 307
    assert response.headers["location"] == "/app#/repos/7"


async def test_paper_alias_redirects(client: AsyncClient) -> None:
    response = await client.get("/papers/123")

    assert response.status_code == 307
    assert response.headers["location"] == "/app#/papers/123"


async def test_paper_alias_keeps_query_string(client: AsyncClient) -> None:
    """引用定位链接带 ?page=&quote=，重定向必须把查询串并进 hash 片段。"""
    response = await client.get("/papers/123?page=3&quote=abc")

    assert response.status_code == 307
    assert response.headers["location"] == "/app#/papers/123?page=3&quote=abc"


async def test_admin_view_aliases_redirect(client: AsyncClient) -> None:
    for view in ("overview", "papers", "repos", "users"):
        response = await client.get(f"/admin/{view}")
        assert response.status_code == 307, view
        assert response.headers["location"] == f"/admin#/{view}", view


async def test_admin_alias_rejects_unknown_view(client: AsyncClient) -> None:
    response = await client.get("/admin/nope")

    assert response.status_code == 422


async def test_alias_requires_integer_id(client: AsyncClient) -> None:
    """非整数 id 不匹配别名路由，直接 404（不会误跳到页面）。"""
    for path in ("/papers/abc", "/sessions/x", "/repos/1.5"):
        response = await client.get(path)
        assert response.status_code == 404, path


async def test_alias_does_not_shadow_api(client: AsyncClient) -> None:
    response = await client.get("/api/v1/papers")

    assert response.status_code == 401
    assert "location" not in response.headers
    assert response.headers["content-type"].startswith("application/json")


def test_openapi_still_matches_contract(app: FastAPI) -> None:
    live = app.openapi()
    implemented = {
        (method, path)
        for path, operations in live["paths"].items()
        for method in operations
        if method in METHODS
    }
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    documented = {
        (method, path)
        for path, operations in contract["paths"].items()
        for method in operations
        if method in METHODS
    }

    assert len(implemented) == 46
    assert implemented == documented
