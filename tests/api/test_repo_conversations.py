from __future__ import annotations

from datetime import datetime, timedelta

from httpx import AsyncClient

from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.roles import UserRole
from app.db import get_session_factory
from app.models import Conversation, Message
from tests.api.conftest import login

REPOS = "/api/v1/repos"


async def _client_for(make_client, user) -> AsyncClient:
    client = await make_client()
    await login(client, user.email)
    return client


async def _create_repo(client: AsyncClient, name: str = "Repo", **extra: object) -> dict:
    payload: dict[str, object] = {"name": name, **extra}
    response = await client.post(REPOS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _conversation(
    repo_id: int,
    user_id: int,
    *,
    messages: list[tuple[str, str, datetime]] | None = None,
) -> Conversation:
    async with get_session_factory()() as db:
        conversation = Conversation(
            user_id=user_id,
            scope_type=ChatScope.repo.value,
            scope_id=repo_id,
        )
        db.add(conversation)
        await db.flush()
        for role, content, created_at in messages or []:
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role=role,
                    content=content,
                    status=MessageStatus.complete.value,
                    citations=[],
                    created_at=created_at,
                )
            )
        await db.commit()
        await db.refresh(conversation)
        return conversation


async def test_repo_conversations_require_login(client: AsyncClient) -> None:
    assert (await client.get(f"{REPOS}/1/conversations")).status_code == 401
    assert (await client.get(f"{REPOS}/1/conversations/1/messages")).status_code == 401


async def test_public_repo_lists_only_own_conversation(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner, visibility="public")
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    mine = await _conversation(
        repo["id"],
        other.id,
        messages=[(MessageRole.user.value, "你好", datetime(2026, 1, 1, 10, 0, 0))],
    )
    await _conversation(
        repo["id"],
        reader_user.id,
        messages=[(MessageRole.user.value, "创建者的对话", datetime(2026, 1, 1, 11, 0, 0))],
    )

    reader_view = (await reader.get(f"{REPOS}/{repo['id']}/conversations")).json()
    owner_view = (await owner.get(f"{REPOS}/{repo['id']}/conversations")).json()

    assert reader_view["total"] == 1
    assert [item["id"] for item in reader_view["items"]] == [mine.id]
    assert owner_view["total"] == 1
    assert owner_view["items"][0]["user"] == {
        "id": reader_user.id,
        "display_name": reader_user.display_name,
    }
    assert "email" not in owner_view["items"][0]["user"]


async def test_private_repo_lists_all_conversations_for_owner_and_admin(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner, "Private")
    other = await user_factory(email="other@example.com", display_name="Other")
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    first = await _conversation(
        repo["id"],
        other.id,
        messages=[(MessageRole.user.value, "一", datetime(2026, 1, 1, 9, 0, 0))],
    )
    second = await _conversation(
        repo["id"],
        reader_user.id,
        messages=[(MessageRole.user.value, "二", datetime(2026, 1, 1, 10, 0, 0))],
    )

    owner_view = (await owner.get(f"{REPOS}/{repo['id']}/conversations")).json()
    admin_view = (await admin_client.get(f"{REPOS}/{repo['id']}/conversations")).json()

    assert owner_view["total"] == 2
    assert [item["id"] for item in owner_view["items"]] == [second.id, first.id]
    assert admin_view["total"] == 2


async def test_private_repo_hidden_from_other_users(make_client, reader_user, user_factory) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner, "Private")
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)

    response = await reader.get(f"{REPOS}/{repo['id']}/conversations")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repo_not_found"


async def test_conversation_item_counts_and_null_ordering(
    make_client,
    reader_user,
    user_factory,
) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client, "Private")
    other = await user_factory(email="other@example.com", display_name="Other")
    empty = await _conversation(repo["id"], reader_user.id)
    with_system = await _conversation(
        repo["id"],
        other.id,
        messages=[
            (MessageRole.system.value, "系统", datetime(2026, 1, 1, 8, 0, 0)),
            (MessageRole.user.value, "问题", datetime(2026, 1, 1, 8, 30, 0)),
            (MessageRole.assistant.value, "回答", datetime(2026, 1, 1, 8, 45, 0)),
        ],
    )

    body = (await client.get(f"{REPOS}/{repo['id']}/conversations")).json()

    ids = [item["id"] for item in body["items"]]
    assert body["total"] == 2
    # last_message_at DESC，null 排最后。
    assert ids == [with_system.id, empty.id]
    item = next(entry for entry in body["items"] if entry["id"] == with_system.id)
    assert item["message_count"] == 3
    assert item["last_message_at"] == "2026-01-01T08:45:00Z"
    assert item["user"] == {"id": other.id, "display_name": other.display_name}
    empty_item = next(entry for entry in body["items"] if entry["id"] == empty.id)
    assert empty_item["message_count"] == 0
    assert empty_item["last_message_at"] is None


async def test_conversation_messages_visibility(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner, visibility="public")
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    conversation = await _conversation(
        repo["id"],
        other.id,
        messages=[
            (MessageRole.user.value, "问题", datetime(2026, 1, 1, 9, 0, 0)),
            (MessageRole.assistant.value, "回答", datetime(2026, 1, 1, 9, 1, 0)),
        ],
    )
    path = f"{REPOS}/{repo['id']}/conversations/{conversation.id}/messages"

    initiator = await reader.get(path)
    creator = await owner.get(path)
    admin_view = await admin_client.get(path)

    assert initiator.status_code == 200
    assert [item["content"] for item in initiator.json()["items"]] == ["回答", "问题"]
    assert creator.status_code == 404
    assert creator.json()["error"]["code"] == "conversation_not_found"
    assert admin_view.status_code == 403
    assert admin_view.json()["error"]["code"] == "forbidden"


async def test_admin_who_is_creator_gets_404(
    make_client,
    reader_user,
    user_factory,
) -> None:
    creator = await user_factory(
        email="creator-admin@example.com",
        display_name="CreatorAdmin",
        role=UserRole.admin,
    )
    creator_client = await _client_for(make_client, creator)
    repo = await _create_repo(creator_client, visibility="public")
    other = await user_factory(email="other@example.com", display_name="Other")
    conversation = await _conversation(
        repo["id"],
        other.id,
        messages=[(MessageRole.user.value, "问题", datetime(2026, 1, 1, 9, 0, 0))],
    )

    response = await creator_client.get(
        f"{REPOS}/{repo['id']}/conversations/{conversation.id}/messages"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


async def test_private_repo_owner_reads_other_conversation(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    repo = await _create_repo(owner, "Private")
    admin = await user_factory(
        email="admin2@example.com",
        display_name="Admin2",
        role=UserRole.admin,
    )
    admin_client = await _client_for(make_client, admin)
    conversation = await _conversation(
        repo["id"],
        admin.id,
        messages=[(MessageRole.user.value, "管理员的问题", datetime(2026, 1, 1, 9, 0, 0))],
    )

    owner_view = await owner.get(f"{REPOS}/{repo['id']}/conversations/{conversation.id}/messages")
    admin_view = await admin_client.get(
        f"{REPOS}/{repo['id']}/conversations/{conversation.id}/messages"
    )

    assert owner_view.status_code == 200
    assert [item["content"] for item in owner_view.json()["items"]] == ["管理员的问题"]
    assert admin_view.status_code == 200


async def test_conversation_from_other_repo_returns_404(
    make_client,
    reader_user,
) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client, "One", visibility="public")
    other_repo = await _create_repo(client, "Two", visibility="public")
    conversation = await _conversation(
        other_repo["id"],
        reader_user.id,
        messages=[(MessageRole.user.value, "问题", datetime(2026, 1, 1, 9, 0, 0))],
    )

    response = await client.get(f"{REPOS}/{repo['id']}/conversations/{conversation.id}/messages")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


async def test_conversation_messages_cursor_pagination(
    make_client,
    reader_user,
) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client, visibility="public")
    base = datetime(2026, 1, 1, 9, 0, 0)
    conversation = await _conversation(
        repo["id"],
        reader_user.id,
        messages=[
            (MessageRole.user.value, f"m{index}", base + timedelta(minutes=index))
            for index in range(5)
        ],
    )
    path = f"{REPOS}/{repo['id']}/conversations/{conversation.id}/messages"

    first = (await client.get(path, params={"limit": 3})).json()
    second = (
        await client.get(path, params={"limit": 3, "before_id": first["next_before_id"]})
    ).json()

    assert [item["content"] for item in first["items"]] == ["m4", "m3", "m2"]
    assert first["has_more"] is True
    assert [item["content"] for item in second["items"]] == ["m1", "m0"]
    assert second["has_more"] is False
    assert second["next_before_id"] is None


async def test_conversation_endpoints_invalid_ids(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    repo = await _create_repo(client, visibility="public")

    assert (await client.get(f"{REPOS}/999999/conversations")).status_code == 404
    assert (await client.get(f"{REPOS}/{repo['id']}/conversations/0/messages")).status_code == 422
    assert (await client.get(f"{REPOS}/0/conversations")).status_code == 422
