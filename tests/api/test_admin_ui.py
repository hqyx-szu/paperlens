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
