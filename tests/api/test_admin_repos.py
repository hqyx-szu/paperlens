from __future__ import annotations

from datetime import datetime

from httpx import AsyncClient
from sqlalchemy import update

from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Paper, Repo, RepoStar
from tests.api.conftest import login

ADMIN_REPOS = "/api/v1/admin/repos"
REPOS = "/api/v1/repos"


async def _client_for(make_client, user) -> AsyncClient:
    client = await make_client()
    await login(client, user.email)
    return client


async def _create_repo(client: AsyncClient, name: str, **extra: object) -> dict:
    payload: dict[str, object] = {"name": name, **extra}
    response = await client.post(REPOS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _paper(title: str = "P", *, archived: bool = False) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(
            title=title,
            authors=[],
            status=PaperStatus.ready.value,
            is_archived=archived,
        )
        db.add(paper)
        await db.commit()
        await db.refresh(paper)
        return paper


async def _set_updated_at(repo_id: int, value: datetime) -> None:
    async with get_session_factory()() as db:
        await db.execute(update(Repo).where(Repo.id == repo_id).values(updated_at=value))
        await db.commit()


async def test_admin_repos_requires_admin(make_client, reader_user) -> None:
    anonymous = await make_client()
    assert (await anonymous.get(ADMIN_REPOS)).status_code == 401

    reader = await _client_for(make_client, reader_user)
    response = await reader.get(ADMIN_REPOS)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_admin_repos_lists_all_with_owner_email(
    make_client,
    reader_user,
    admin_client: AsyncClient,
) -> None:
    owner = await _client_for(make_client, reader_user)
    public_repo = await _create_repo(owner, "Public repo", visibility="public")
    private_repo = await _create_repo(owner, "Private repo")
    paper = await _paper("常规论文")
    archived = await _paper("归档论文")
    assert (
        await owner.post(
            f"{REPOS}/{public_repo['id']}/papers",
            json={"paper_ids": [paper.id, archived.id]},
        )
    ).status_code == 200
    async with get_session_factory()() as db:
        await db.execute(update(Paper).where(Paper.id == archived.id).values(is_archived=True))
        db.add(RepoStar(repo_id=private_repo["id"], user_id=reader_user.id))
        await db.commit()

    body = (await admin_client.get(ADMIN_REPOS)).json()

    assert body["total"] == 2
    by_id = {item["id"]: item for item in body["items"]}
    public_item = by_id[public_repo["id"]]
    assert public_item["owner_email"] == reader_user.email
    assert public_item["owner"] == {
        "id": reader_user.id,
        "display_name": reader_user.display_name,
    }
    assert public_item["paper_count"] == 2
    assert public_item["visibility"] == "public"
    private_item = by_id[private_repo["id"]]
    assert private_item["star_count"] == 0
    assert private_item["visibility"] == "private"
    assert private_item["updated_at"].endswith("Z")


async def test_admin_repos_ordering(make_client, reader_user, admin_client: AsyncClient) -> None:
    owner = await _client_for(make_client, reader_user)
    first = await _create_repo(owner, "First")
    second = await _create_repo(owner, "Second")
    await _set_updated_at(first["id"], datetime(2026, 1, 1, 9, 0, 0))
    await _set_updated_at(second["id"], datetime(2026, 1, 1, 10, 0, 0))

    items = (await admin_client.get(ADMIN_REPOS)).json()["items"]

    assert [item["id"] for item in items] == [second["id"], first["id"]]


async def test_admin_repos_filters(
    make_client,
    reader_user,
    admin_client: AsyncClient,
) -> None:
    owner = await _client_for(make_client, reader_user)
    await _create_repo(owner, "100% 精度", visibility="public")
    await _create_repo(owner, "私有整理")

    by_name = (await admin_client.get(ADMIN_REPOS, params={"q": "精度"})).json()
    by_email = (await admin_client.get(ADMIN_REPOS, params={"q": reader_user.email})).json()
    by_display = (
        await admin_client.get(ADMIN_REPOS, params={"q": reader_user.display_name})
    ).json()
    by_literal = (await admin_client.get(ADMIN_REPOS, params={"q": "%"})).json()
    public_only = (await admin_client.get(ADMIN_REPOS, params={"visibility": "public"})).json()
    private_only = (await admin_client.get(ADMIN_REPOS, params={"visibility": "private"})).json()

    assert by_name["total"] == 1
    assert by_email["total"] == 2
    assert by_display["total"] == 2
    # % 被转义，只匹配真的包含 % 的仓库名，而不是全部。
    assert by_literal["total"] == 1
    assert [item["name"] for item in public_only["items"]] == ["100% 精度"]
    assert [item["name"] for item in private_only["items"]] == ["私有整理"]


async def test_admin_repos_pagination_and_validation(
    make_client,
    reader_user,
    admin_client: AsyncClient,
) -> None:
    owner = await _client_for(make_client, reader_user)
    for index in range(3):
        await _create_repo(owner, f"Repo {index}", visibility="public")

    first = (await admin_client.get(ADMIN_REPOS, params={"page_size": 2})).json()
    second = (await admin_client.get(ADMIN_REPOS, params={"page": 2, "page_size": 2})).json()

    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert (await admin_client.get(ADMIN_REPOS, params={"page_size": 101})).status_code == 422
    assert (await admin_client.get(ADMIN_REPOS, params={"q": "x" * 201})).status_code == 422
    assert (await admin_client.get(ADMIN_REPOS, params={"visibility": "secret"})).status_code == 422


async def test_admin_stats_counts_repos(
    make_client,
    reader_user,
    admin_client: AsyncClient,
) -> None:
    owner = await _client_for(make_client, reader_user)
    await _create_repo(owner, "Public", visibility="public")
    await _create_repo(owner, "Private one")
    await _create_repo(owner, "Private two")

    body = (await admin_client.get("/api/v1/admin/stats")).json()

    assert body["repos_total"] == 3
    assert body["repos_private"] == 2
