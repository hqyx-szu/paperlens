from __future__ import annotations

from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.papers import PaperStatus
from app.core.roles import UserRole
from app.db import get_session_factory
from app.models import Paper, Repo, RepoPaper
from tests.api.conftest import login

REPOS = "/api/v1/repos"
OLD = datetime(2026, 1, 1, 0, 0, 0)


async def _client_for(make_client, user) -> AsyncClient:
    client = await make_client()
    await login(client, user.email)
    return client


async def _create_repo(client: AsyncClient, name: str = "Repo", **extra: object) -> dict:
    payload: dict[str, object] = {"name": name, **extra}
    response = await client.post(REPOS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _paper(
    title: str = "Paper",
    *,
    status: str = PaperStatus.ready.value,
    archived: bool = False,
) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(title=title, authors=["A"], status=status, is_archived=archived)
        db.add(paper)
        await db.commit()
        await db.refresh(paper)
        return paper


async def _set_repo_updated_at(repo_id: int, value: datetime = OLD) -> None:
    async with get_session_factory()() as db:
        await db.execute(update(Repo).where(Repo.id == repo_id).values(updated_at=value))
        await db.commit()


async def _set_added_at(repo_id: int, paper_id: int, value: datetime) -> None:
    async with get_session_factory()() as db:
        await db.execute(
            update(RepoPaper)
            .where(RepoPaper.repo_id == repo_id, RepoPaper.paper_id == paper_id)
            .values(added_at=value)
        )
        await db.commit()


async def _archive_paper(paper_id: int) -> None:
    async with get_session_factory()() as db:
        await db.execute(update(Paper).where(Paper.id == paper_id).values(is_archived=True))
        await db.commit()


async def _repo_updated_at(client: AsyncClient, repo_id: int) -> str:
    response = await client.get(f"{REPOS}/{repo_id}")
    assert response.status_code == 200, response.text
    return response.json()["updated_at"]


async def _add(client: AsyncClient, repo_id: int, paper_ids: list[int]):
    return await client.post(f"{REPOS}/{repo_id}/papers", json={"paper_ids": paper_ids})


async def test_repo_papers_require_login(client: AsyncClient) -> None:
    assert (await client.get(f"{REPOS}/1/papers")).status_code == 401
    assert (await client.post(f"{REPOS}/1/papers", json={"paper_ids": [1]})).status_code == 401
    assert (await client.delete(f"{REPOS}/1/papers/1")).status_code == 401


async def test_add_papers_returns_briefs(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    paper = await _paper("Attention")
    await _set_repo_updated_at(repo["id"])

    response = await _add(client, repo["id"], [paper.id])

    assert response.status_code == 200
    body = response.json()
    assert body["ignored"] == []
    assert body["added"] == [
        {
            "id": paper.id,
            "title": "Attention",
            "authors": ["A"],
            "year": None,
            "venue": None,
            "is_archived": False,
        }
    ]
    assert await _repo_updated_at(client, repo["id"]) != "2026-01-01T00:00:00Z"


async def test_add_papers_ignored_reason_order(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    ready = await _paper("ready")
    parsing = await _paper("parsing", status=PaperStatus.parsing.value)
    archived = await _paper("archived", archived=True)
    assert (await _add(client, repo["id"], [ready.id])).status_code == 200

    response = await _add(client, repo["id"], [999999, ready.id, parsing.id, archived.id])

    assert response.status_code == 200
    body = response.json()
    assert body["added"] == []
    assert body["ignored"] == [
        {"paper_id": 999999, "reason": "not_found"},
        {"paper_id": ready.id, "reason": "duplicate"},
        {"paper_id": parsing.id, "reason": "not_ready"},
        {"paper_id": archived.id, "reason": "archived"},
    ]


async def test_add_papers_dedupes_and_preserves_order(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    first = await _paper("first")
    second = await _paper("second")

    response = await _add(client, repo["id"], [second.id, first.id, second.id])

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["added"]] == [second.id, first.id]


async def test_add_papers_all_ignored_keeps_updated_at(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    archived = await _paper("archived", archived=True)
    await _set_repo_updated_at(repo["id"])

    response = await _add(client, repo["id"], [archived.id])

    assert response.status_code == 200
    assert response.json()["added"] == []
    assert await _repo_updated_at(client, repo["id"]) == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "payload",
    [
        {"paper_ids": []},
        {"paper_ids": list(range(1, 102))},
        {"paper_ids": [0]},
        {"paper_ids": [-1]},
        {"paper_ids": [1], "unknown": True},
        {},
    ],
)
async def test_add_papers_validation(make_client, reader_user, payload: dict) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)

    response = await client.post(f"{REPOS}/{repo['id']}/papers", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_repo_papers_includes_archived_and_orders(
    make_client,
    reader_user,
) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    first = await _paper("first")
    second = await _paper("second")
    third = await _paper("third")
    assert (await _add(client, repo["id"], [first.id, second.id, third.id])).status_code == 200
    await _archive_paper(second.id)

    await _set_added_at(repo["id"], first.id, datetime(2026, 1, 1, 9, 0, 0))
    await _set_added_at(repo["id"], second.id, datetime(2026, 1, 1, 10, 0, 0))
    await _set_added_at(repo["id"], third.id, datetime(2026, 1, 1, 10, 0, 0))

    body = (await client.get(f"{REPOS}/{repo['id']}/papers")).json()

    # added_at DESC，同刻按 paper_id DESC；已归档论文仍在列表中。
    assert [item["id"] for item in body["items"]] == [third.id, second.id, first.id]
    assert body["total"] == 3
    archived_item = next(item for item in body["items"] if item["id"] == second.id)
    assert archived_item["is_archived"] is True


async def test_list_repo_papers_pagination(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    papers = [await _paper(f"p{index}") for index in range(3)]
    await _add(client, repo["id"], [paper.id for paper in papers])

    page = (await client.get(f"{REPOS}/{repo['id']}/papers", params={"page_size": 2})).json()
    beyond = (
        await client.get(f"{REPOS}/{repo['id']}/papers", params={"page": 9, "page_size": 2})
    ).json()

    assert len(page["items"]) == 2
    assert page["total"] == 3
    assert beyond["items"] == []
    assert beyond["total"] == 3


async def test_list_repo_papers_excludes_removed(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    keep = await _paper("keep")
    drop = await _paper("drop")
    await _add(client, repo["id"], [keep.id, drop.id])

    assert (await client.delete(f"{REPOS}/{repo['id']}/papers/{drop.id}")).status_code == 204

    body = (await client.get(f"{REPOS}/{repo['id']}/papers")).json()
    assert [item["id"] for item in body["items"]] == [keep.id]
    assert body["total"] == 1


async def test_remove_paper_idempotent_and_refreshes_updated_at(
    make_client,
    reader_user,
) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)
    paper = await _paper()
    await _add(client, repo["id"], [paper.id])
    await _set_repo_updated_at(repo["id"])

    first = await client.delete(f"{REPOS}/{repo['id']}/papers/{paper.id}")
    refreshed = await _repo_updated_at(client, repo["id"])
    await _set_repo_updated_at(repo["id"])
    second = await client.delete(f"{REPOS}/{repo['id']}/papers/{paper.id}")
    unchanged = await _repo_updated_at(client, repo["id"])

    assert first.status_code == 204
    assert second.status_code == 204
    assert refreshed != "2026-01-01T00:00:00Z"
    assert unchanged == "2026-01-01T00:00:00Z"

    async with get_session_factory()() as db:
        assert await db.get(Paper, paper.id) is not None
        rows = (await db.scalars(select(RepoPaper).where(RepoPaper.repo_id == repo["id"]))).all()
        assert rows == []


async def test_remove_paper_invalid_id(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client)

    assert (await client.delete(f"{REPOS}/{repo['id']}/papers/0")).status_code == 422
    assert (await client.delete(f"{REPOS}/{repo['id']}/papers/abc")).status_code == 422


async def test_repo_paper_permissions(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    public_repo = await _create_repo(owner, "Public", visibility="public")
    private_repo = await _create_repo(owner, "Private")
    paper = await _paper()

    reader_add = await _add(reader, public_repo["id"], [paper.id])
    admin_add = await _add(admin_client, public_repo["id"], [paper.id])
    admin_private_add = await _add(admin_client, private_repo["id"], [paper.id])
    reader_list_private = await reader.get(f"{REPOS}/{private_repo['id']}/papers")
    reader_remove = await reader.delete(f"{REPOS}/{public_repo['id']}/papers/{paper.id}")
    admin_remove = await admin_client.delete(f"{REPOS}/{public_repo['id']}/papers/{paper.id}")

    assert reader_add.status_code == 403
    assert reader_add.json()["error"]["code"] == "forbidden"
    assert admin_add.status_code == 403
    assert admin_private_add.status_code == 200
    assert reader_list_private.status_code == 404
    assert reader_list_private.json()["error"]["code"] == "repo_not_found"
    assert reader_remove.status_code == 403
    assert admin_remove.status_code == 403


async def test_repo_papers_unknown_repo(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)

    response = await client.get(f"{REPOS}/999999/papers")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repo_not_found"
