from httpx import AsyncClient


async def test_admin_page_is_served(client: AsyncClient) -> None:
    response = await client.get("/admin")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "PaperLens 管理后台" in response.text
    assert "论文目录" in response.text
    assert "导入 PDF" in response.text
    assert "/admin/papers" in response.text


async def test_login_page_is_served(client: AsyncClient) -> None:
    response = await client.get("/login")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "管理后台" in response.text


async def test_admin_page_has_repos_tab(client: AsyncClient) -> None:
    response = await client.get("/admin")

    assert response.status_code == 200
    assert 'data-view="repos"' in response.text
    assert 'id="reposView"' in response.text
    assert 'id="repoRows"' in response.text
    assert "/admin/repos" in response.text
    assert "可见性" in response.text


async def test_admin_page_has_repo_manage_dialog(client: AsyncClient) -> None:
    response = await client.get("/admin")

    assert 'id="repoDialog"' in response.text
    assert 'id="repoAdminPapers"' in response.text
    assert 'id="repoAdminConversations"' in response.text
    assert 'id="repoPaperPicker"' in response.text
    assert "公开仓库仅创建者可在用户端管理" in response.text


async def test_admin_page_has_overview_and_sidebar(client: AsyncClient) -> None:
    response = await client.get("/admin")

    assert 'id="adminNav"' in response.text
    for view in ("overview", "papers", "repos", "users"):
        assert f'data-view="{view}"' in response.text
    for stat_id in (
        "statUsersTotal",
        "statUsersActive",
        "statPapers",
        "statPapersReady",
        "statPapersProcessing",
        "statPapersFailed",
        "statPapersArchived",
        "statRepos",
        "statReposPrivate",
    ):
        assert f'id="{stat_id}"' in response.text
    for quick_id in ("quickImport", "quickPapers", "quickRepos", "quickUsers"):
        assert f'id="{quick_id}"' in response.text
