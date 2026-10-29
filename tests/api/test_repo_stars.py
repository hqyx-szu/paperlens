from __future__ import annotations

from datetime import datetime

from httpx import AsyncClient
from sqlalchemy import update

from app.core.roles import UserRole
from app.db import get_session_factory
from app.models import Repo, RepoStar
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


async def _set_repo_updated_at(repo_id: int, value: datetime = OLD) -> None:
    async with get_session_factory()() as db:
        await db.execute(update(Repo).where(Repo.id == repo_id).values(updated_at=value))
        await db.commit()


async def _insert_star(repo_id: int, user_id: int) -> None:
    async with get_session_factory()() as db:
        db.add(RepoStar(repo_id=repo_id, user_id=user_id))
        await db.commit()


async def _detail(client: AsyncClient, repo_id: int) -> dict:
    response = await client.get(f"{REPOS}/{repo_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def test_star_requires_login(client: AsyncClient) -> None:
    assert (await client.post(f"{REPOS}/1/star")).status_code == 401
    assert (await client.delete(f"{REPOS}/1/star")).status_code == 401


async def test_star_and_unstar_are_idempotent(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    repo = await _create_repo(owner, "Public", visibility="public")

    first = await reader.post(f"{REPOS}/{repo['id']}/star")
    second = await reader.post(f"{REPOS}/{repo['id']}/star")

    assert first.status_code == 200
    assert first.json() == {"starred": True, "star_count": 1}
    assert second.json() == {"starred": True, "star_count": 1}

    owner_view = await _detail(owner, repo["id"])
    reader_view = await _detail(reader, repo["id"])
    assert owner_view["star_count"] == 1
    assert owner_view["is_starred"] is False
    assert reader_view["is_starred"] is True

    unstar = await reader.delete(f"{REPOS}/{repo['id']}/star")
    again = await reader.delete(f"{REPOS}/{repo['id']}/star")

    assert unstar.status_code == 200
    assert unstar.json() == {"starred": False, "star_count": 0}
    assert again.json() == {"starred": False, "star_count": 0}


async def test_cannot_star_own_public_repo(make_client, reader_user) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner, "Mine", visibility="public")

    response = await owner.post(f"{REPOS}/{repo['id']}/star")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cannot_star_own_repo"


async def test_private_repo_cannot_be_starred(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    private_repo = await _create_repo(owner, "Private")

    owner_star = await owner.post(f"{REPOS}/{private_repo['id']}/star")
    admin_star = await admin_client.post(f"{REPOS}/{private_repo['id']}/star")
    reader_star = await reader.post(f"{REPOS}/{private_repo['id']}/star")
    reader_unstar = await reader.delete(f"{REPOS}/{private_repo['id']}/star")

    assert owner_star.status_code == 400
    assert owner_star.json()["error"]["code"] == "repo_not_public"
    assert admin_star.status_code == 400
    assert admin_star.json()["error"]["code"] == "repo_not_public"
    assert reader_star.status_code == 404
    assert reader_star.json()["error"]["code"] == "repo_not_found"
    assert reader_unstar.status_code == 404


async def test_star_count_includes_disabled_users(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    inactive = await user_factory(
        email="inactive@example.com",
        display_name="Inactive",
        is_active=False,
    )
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    repo = await _create_repo(owner, "Public", visibility="public")
    await _insert_star(repo["id"], inactive.id)

    response = await reader.post(f"{REPOS}/{repo['id']}/star")

    assert response.status_code == 200
    assert response.json() == {"starred": True, "star_count": 2}
    assert (await _detail(owner, repo["id"]))["star_count"] == 2


async def test_private_switch_hides_stars(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    repo = await _create_repo(owner, "Switch", visibility="public")
    assert (await reader.post(f"{REPOS}/{repo['id']}/star")).status_code == 200

    await owner.patch(f"{REPOS}/{repo['id']}", json={"visibility": "private"})

    hidden = await _detail(owner, repo["id"])
    assert hidden["star_count"] == 0
    assert hidden["is_starred"] is False

    await owner.patch(f"{REPOS}/{repo['id']}", json={"visibility": "public"})

    restored = await _detail(owner, repo["id"])
    assert restored["star_count"] == 1

    # 历史 star 记录保留，取消后归零。
    unstar = await reader.delete(f"{REPOS}/{repo['id']}/star")
    assert unstar.json() == {"starred": False, "star_count": 0}


async def test_star_does_not_refresh_updated_at(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    repo = await _create_repo(owner, "Public", visibility="public")
    await _set_repo_updated_at(repo["id"])

    assert (await reader.post(f"{REPOS}/{repo['id']}/star")).status_code == 200

    assert (await _detail(owner, repo["id"]))["updated_at"] == "2026-01-01T00:00:00Z"


async def test_star_unknown_and_invalid_repo(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)

    missing = await client.post(f"{REPOS}/999999/star")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "repo_not_found"
    assert (await client.post(f"{REPOS}/0/star")).status_code == 422
    assert (await client.post(f"{REPOS}/abc/star")).status_code == 422
