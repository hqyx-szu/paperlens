from __future__ import annotations

from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.core.chat import ChatScope, MessageRole
from app.core.papers import PaperStatus
from app.core.roles import UserRole
from app.db import get_session_factory
from app.models import Conversation, Message, Paper, Repo, RepoPaper, RepoStar
from tests.api.conftest import login

REPOS = "/api/v1/repos"


async def _client_for(make_client, user) -> AsyncClient:
    client = await make_client()
    await login(client, user.email)
    return client


async def _create_repo(client: AsyncClient, name: str = "Repo A", **extra: object) -> dict:
    payload: dict[str, object] = {"name": name, **extra}
    response = await client.post(REPOS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_paper(title: str = "Repo paper") -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(title=title, authors=["A"], status=PaperStatus.ready.value)
        db.add(paper)
        await db.commit()
        await db.refresh(paper)
        return paper


async def _set_updated_at(repo_id: int, value: datetime) -> None:
    async with get_session_factory()() as db:
        await db.execute(update(Repo).where(Repo.id == repo_id).values(updated_at=value))
        await db.commit()


async def _count(sql) -> int:
    async with get_session_factory()() as db:
        return await db.scalar(sql) or 0


async def test_repos_require_login(client: AsyncClient) -> None:
    assert (await client.get(REPOS)).status_code == 401
    assert (await client.post(REPOS, json={"name": "x"})).status_code == 401
    assert (await client.get(f"{REPOS}/1")).status_code == 401
    assert (await client.patch(f"{REPOS}/1", json={"name": "y"})).status_code == 401
    assert (await client.delete(f"{REPOS}/1")).status_code == 401


async def test_create_repo_defaults(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)

    response = await client.post(REPOS, json={"name": "  My Repo  "})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "My Repo"
    assert body["description"] == ""
    assert body["visibility"] == "private"
    assert body["paper_count"] == 0
    assert body["star_count"] == 0
    assert body["is_starred"] is False
    assert body["owner"] == {
        "id": reader_user.id,
        "display_name": reader_user.display_name,
    }
    assert "email" not in body["owner"]
    assert body["permissions"] == {
        "can_edit": True,
        "can_delete": True,
        "can_manage_papers": True,
        "can_star": False,
        "can_chat": True,
    }
    assert body["created_at"].endswith("Z")
    assert body["updated_at"].endswith("Z")


async def test_create_repo_trims_description(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)

    trimmed = await client.post(REPOS, json={"name": "R1", "description": "  desc  "})
    blank = await client.post(REPOS, json={"name": "R2", "description": "   "})

    assert trimmed.json()["description"] == "desc"
    assert blank.json()["description"] == ""


async def test_create_repo_name_taken_case_insensitive(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    await _create_repo(client, "Repo")

    duplicate = await client.post(REPOS, json={"name": "Repo"})
    case_only = await client.post(REPOS, json={"name": "repo"})

    for response in (duplicate, case_only):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "repo_name_taken"


async def test_create_repo_same_name_for_different_owner(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner_client = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    other_client = await _client_for(make_client, other)
    await _create_repo(owner_client, "Shared")

    response = await other_client.post(REPOS, json={"name": "Shared"})

    assert response.status_code == 201
    assert response.json()["owner"]["id"] == other.id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 65},
        {"name": "ok", "unknown": 1},
        {"name": "ok", "visibility": "secret"},
        {"name": "ok", "description": "x" * 501},
    ],
)
async def test_create_repo_validation(make_client, reader_user, payload: dict) -> None:
    client = await _client_for(make_client, reader_user)

    response = await client.post(REPOS, json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_repos_scopes(make_client, reader_user, user_factory) -> None:
    mine = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    theirs = await _client_for(make_client, other)

    private_repo = await _create_repo(mine, "Mine private")
    public_repo = await _create_repo(mine, "Mine public", visibility="public")
    their_public = await _create_repo(theirs, "Theirs public", visibility="public")
    await _create_repo(theirs, "Theirs private")

    mine_page = (await mine.get(REPOS, params={"scope": "mine"})).json()
    public_page = (await mine.get(REPOS, params={"scope": "public"})).json()

    assert {item["id"] for item in mine_page["items"]} == {private_repo["id"], public_repo["id"]}
    assert mine_page["total"] == 2
    assert {item["id"] for item in public_page["items"]} == {public_repo["id"], their_public["id"]}
    assert public_page["total"] == 2
    own_public = next(item for item in public_page["items"] if item["id"] == public_repo["id"])
    assert own_public["is_starred"] is False


async def test_list_repos_ordering(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    first = await _create_repo(client, "First")
    second = await _create_repo(client, "Second")
    third = await _create_repo(client, "Third")

    await _set_updated_at(first["id"], datetime(2026, 1, 1, 10, 0, 0))
    await _set_updated_at(second["id"], datetime(2026, 1, 1, 11, 0, 0))
    await _set_updated_at(third["id"], datetime(2026, 1, 1, 11, 0, 0))

    items = (await client.get(REPOS)).json()["items"]

    # updated_at DESC，同秒按 id DESC。
    assert [item["id"] for item in items] == [third["id"], second["id"], first["id"]]


async def test_list_repos_pagination(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    for index in range(3):
        await _create_repo(client, f"Repo {index}")

    first_page = (await client.get(REPOS, params={"page_size": 2})).json()
    second_page = (await client.get(REPOS, params={"page": 2, "page_size": 2})).json()
    beyond = await client.get(REPOS, params={"page": 5, "page_size": 2})

    assert len(first_page["items"]) == 2
    assert first_page["total"] == 3
    assert len(second_page["items"]) == 1
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 3

    assert (await client.get(REPOS, params={"page_size": 101})).status_code == 422
    assert (await client.get(REPOS, params={"page": 0})).status_code == 422
    assert (await client.get(REPOS, params={"scope": "all"})).status_code == 422


async def test_repo_detail_permission_matrix(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)

    public_repo = await _create_repo(owner, "Public repo", visibility="public")
    private_repo = await _create_repo(owner, "Private repo")

    reader_public = (await reader.get(f"{REPOS}/{public_repo['id']}")).json()["permissions"]
    admin_public = (await admin_client.get(f"{REPOS}/{public_repo['id']}")).json()["permissions"]
    admin_private = (await admin_client.get(f"{REPOS}/{private_repo['id']}")).json()["permissions"]
    owner_private = (await owner.get(f"{REPOS}/{private_repo['id']}")).json()["permissions"]

    assert reader_public == {
        "can_edit": False,
        "can_delete": False,
        "can_manage_papers": False,
        "can_star": True,
        "can_chat": True,
    }
    assert admin_public == reader_public
    assert admin_private == {
        "can_edit": True,
        "can_delete": True,
        "can_manage_papers": True,
        "can_star": False,
        "can_chat": True,
    }
    assert owner_private == admin_private


async def test_repo_detail_private_hidden_and_invalid_id(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    private_repo = await _create_repo(owner, "Private repo")

    hidden = await reader.get(f"{REPOS}/{private_repo['id']}")
    missing = await reader.get(f"{REPOS}/999999")

    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "repo_not_found"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "repo_not_found"
    assert (await reader.get(f"{REPOS}/abc")).status_code == 422
    assert (await reader.get(f"{REPOS}/0")).status_code == 422


async def test_patch_repo_fields(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client, "Old name")
    await _set_updated_at(repo["id"], datetime(2026, 1, 1, 0, 0, 0))

    renamed = await client.patch(
        f"{REPOS}/{repo['id']}",
        json={"name": "New name", "description": "新简介", "visibility": "public"},
    )

    assert renamed.status_code == 200
    body = renamed.json()
    assert body["name"] == "New name"
    assert body["description"] == "新简介"
    assert body["visibility"] == "public"
    assert body["permissions"]["can_star"] is False
    assert body["updated_at"] != "2026-01-01T00:00:00Z"

    # 值未变化也算一次成功写入，updated_at 仍被刷新。
    await _set_updated_at(repo["id"], datetime(2026, 1, 1, 0, 0, 0))
    again = await client.patch(f"{REPOS}/{repo['id']}", json={"description": "新简介"})
    assert again.status_code == 200
    assert again.json()["updated_at"] != "2026-01-01T00:00:00Z"


async def test_patch_repo_empty_and_null(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client, "Repo")

    empty = await client.patch(f"{REPOS}/{repo['id']}", json={})
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "bad_request"

    for payload in ({"name": None}, {"description": None}, {"visibility": None}):
        response = await client.patch(f"{REPOS}/{repo['id']}", json=payload)
        assert response.status_code == 422

    assert (await client.patch(f"{REPOS}/{repo['id']}", json={"unknown": 1})).status_code == 422
    assert (await client.patch(f"{REPOS}/{repo['id']}", json={"name": ""})).status_code == 422
    assert (await client.patch(f"{REPOS}/{repo['id']}", json={"name": "   "})).status_code == 422
    assert (await client.patch(f"{REPOS}/{repo['id']}", json={"name": "x" * 65})).status_code == 422


async def test_patch_repo_name_conflicts(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    first = await _create_repo(client, "First")
    second = await _create_repo(client, "Second")

    conflict = await client.patch(f"{REPOS}/{second['id']}", json={"name": "First"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "repo_name_taken"

    # 改大小写属于自己的名字，不算冲突。
    renamed = await client.patch(f"{REPOS}/{second['id']}", json={"name": "second"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "second"
    assert first["id"] != second["id"]


async def test_patch_visibility_switch_changes_permissions(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    repo = await _create_repo(owner, "Switch", visibility="public")

    to_private = await owner.patch(f"{REPOS}/{repo['id']}", json={"visibility": "private"})

    assert to_private.status_code == 200
    assert to_private.json()["permissions"]["can_star"] is False
    assert (await reader.get(f"{REPOS}/{repo['id']}")).status_code == 404
    admin_permissions = (await admin_client.get(f"{REPOS}/{repo['id']}")).json()["permissions"]
    assert admin_permissions["can_edit"] is True

    to_public = await owner.patch(f"{REPOS}/{repo['id']}", json={"visibility": "public"})

    assert to_public.status_code == 200
    admin_permissions = (await admin_client.get(f"{REPOS}/{repo['id']}")).json()["permissions"]
    assert admin_permissions["can_edit"] is False
    assert (await reader.get(f"{REPOS}/{repo['id']}")).status_code == 200


async def test_patch_repo_forbidden(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    public_repo = await _create_repo(owner, "Public repo", visibility="public")
    private_repo = await _create_repo(owner, "Private repo")

    reader_public = await reader.patch(f"{REPOS}/{public_repo['id']}", json={"name": "Hijack"})
    admin_public = await admin_client.patch(f"{REPOS}/{public_repo['id']}", json={"name": "Hijack"})
    reader_private = await reader.patch(f"{REPOS}/{private_repo['id']}", json={"name": "Hijack"})
    admin_private = await admin_client.patch(
        f"{REPOS}/{private_repo['id']}",
        json={"description": "管理员可管理私有仓库"},
    )

    assert reader_public.status_code == 403
    assert reader_public.json()["error"]["code"] == "forbidden"
    assert admin_public.status_code == 403
    assert reader_private.status_code == 404
    assert reader_private.json()["error"]["code"] == "repo_not_found"
    assert admin_private.status_code == 200


async def test_delete_repo_cascades(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    repo = await _create_repo(owner, "Cascade", visibility="public")
    paper = await _create_paper()

    async with get_session_factory()() as db:
        db.add(RepoPaper(repo_id=repo["id"], paper_id=paper.id))
        db.add(RepoStar(repo_id=repo["id"], user_id=other.id))
        conversation = Conversation(
            user_id=other.id,
            scope_type=ChatScope.repo.value,
            scope_id=repo["id"],
        )
        db.add(conversation)
        await db.flush()
        conversation_id = conversation.id
        db.add(
            Message(
                conversation_id=conversation_id,
                role=MessageRole.user.value,
                content="仓库里的对话",
            )
        )
        await db.commit()

    response = await owner.delete(f"{REPOS}/{repo['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert (await owner.get(f"{REPOS}/{repo['id']}")).status_code == 404
    assert await _count(select(func.count()).select_from(RepoPaper)) == 0
    assert await _count(select(func.count()).select_from(RepoStar)) == 0
    assert (
        await _count(
            select(func.count()).select_from(Conversation).where(Conversation.id == conversation_id)
        )
        == 0
    )
    assert (
        await _count(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        == 0
    )

    async with get_session_factory()() as db:
        assert await db.get(Paper, paper.id) is not None


async def test_delete_repo_forbidden_and_missing(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    public_repo = await _create_repo(owner, "Public repo", visibility="public")
    private_repo = await _create_repo(owner, "Private repo")

    assert (await reader.delete(f"{REPOS}/{public_repo['id']}")).status_code == 403
    assert (await admin_client.delete(f"{REPOS}/{public_repo['id']}")).status_code == 403
    assert (await admin_client.delete(f"{REPOS}/{private_repo['id']}")).status_code == 204
    assert (await admin_client.delete(f"{REPOS}/{private_repo['id']}")).status_code == 404
    assert (await owner.delete(f"{REPOS}/{public_repo['id']}")).status_code == 204
