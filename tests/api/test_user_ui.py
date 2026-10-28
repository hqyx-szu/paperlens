from pathlib import Path

from httpx import AsyncClient


async def test_app_page_served(client: AsyncClient) -> None:
    response = await client.get("/app")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "PaperLens" in response.text
    assert "搜索" in response.text
    assert "会话" in response.text


async def test_app_page_trailing_slash(client: AsyncClient) -> None:
    response = await client.get("/app/")
    assert response.status_code == 200


async def test_root_remains_api_info(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json()["name"] == "PaperLens"
    assert response.headers["content-type"].startswith("application/json")


def test_app_static_has_no_external_assets() -> None:
    html = (Path(__file__).resolve().parents[2] / "web" / "app" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "http://" not in html
    assert "https://" not in html
    assert "<script src=" not in html
    assert 'rel="stylesheet"' not in html
