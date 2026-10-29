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


def test_app_has_paper_detail_view() -> None:
    html = (Path(__file__).resolve().parents[2] / "web" / "app" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="paperView"' in html
    assert 'id="paperDetailTitle"' in html
    assert 'id="paperSummaryLevel"' in html
    assert 'id="paperMessages"' in html
    assert "#/papers/" in html
    assert "引用定位" in html


def test_app_has_citation_jump_and_summary_levels() -> None:
    html = (Path(__file__).resolve().parents[2] / "web" / "app" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="citationOpen"' in html
    assert "打开论文" in html
    for level in ("brief", "standard", "deep"):
        assert f'value="{level}"' in html


def test_app_has_navigation_and_home_panels() -> None:
    html = (Path(__file__).resolve().parents[2] / "web" / "app" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="adminEntry"' in html
    assert 'id="homeMyRepos"' in html
    assert 'id="homePublicRepos"' in html
    assert "我的仓库" in html and "公开仓库" in html


def test_app_repo_page_is_two_column_with_collapsible_chat() -> None:
    html = (Path(__file__).resolve().parents[2] / "web" / "app" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="repoWorkspace"' in html
    assert 'id="repoChatToggle"' in html
    assert "repo-workspace" in html
    assert "collapsed" in html


def test_app_locate_banner_highlights_quote() -> None:
    html = (Path(__file__).resolve().parents[2] / "web" / "app" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'createElement("mark")' in html
    assert "复制摘录" in html
