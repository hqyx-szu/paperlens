from __future__ import annotations

from datetime import datetime, timedelta

from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.db import get_session_factory
from app.models import Conversation, Message, Paper, PaperChunk, SearchSession
from tests.api.conftest import login


async def _create_session(
    user_id: int,
    *,
    query: str = "session",
    created_at: datetime | None = None,
    with_system: bool = False,
) -> tuple[int, int]:
    async with get_session_factory()() as db:
        session = SearchSession(
            user_id=user_id,
            query=query,
            query_type="text",
            paper_count=0,
            evidence_count=0,
            system_message="system message",
            created_at=created_at or datetime(2026, 10, 28, 12, 0, 0),
        )
        db.add(session)
        await db.flush()
        conversation = Conversation(
            user_id=user_id,
            scope_type=ChatScope.session.value,
            scope_id=session.id,
        )
        db.add(conversation)
        await db.flush()
        if with_system:
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role=MessageRole.system.value,
                    content="system message",
                    status=MessageStatus.complete.value,
                    citations=[],
                )
            )
        await db.commit()
        return session.id, conversation.id


async def _create_paper(content: str) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(
            title="Session paper",
            authors=["Author"],
            status="ready",
            is_archived=False,
        )
        db.add(paper)
        await db.flush()
        db.add(
            PaperChunk(
                paper_id=paper.id,
                chunk_index=0,
                page=1,
                content=content,
                char_count=len(content),
            )
        )
        await db.commit()
        await db.refresh(paper)
        return paper


async def test_list_sessions_only_current_user(reader_user, reader_client) -> None:
    base = datetime(2026, 10, 28, 10, 0, 0)
    first, _ = await _create_session(reader_user.id, query="first", created_at=base)
    second, _ = await _create_session(
        reader_user.id,
        query="second",
        created_at=base + timedelta(minutes=1),
    )
    third, _ = await _create_session(
        reader_user.id,
        query="third",
        created_at=base + timedelta(minutes=2),
    )

    response = await reader_client.get("/api/v1/sessions?page=1&page_size=20")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [third, second, first]
    assert body["page"] == 1
    assert body["page_size"] == 20


async def test_list_sessions_pagination(reader_user, reader_client) -> None:
    base = datetime(2026, 10, 28, 10, 0, 0)
    ids = [
        (
            await _create_session(
                reader_user.id,
                query=f"session-{index}",
                created_at=base + timedelta(minutes=index),
            )
        )[0]
        for index in range(3)
    ]

    response = await reader_client.get("/api/v1/sessions?page=1&page_size=2")
    assert [item["id"] for item in response.json()["items"]] == [ids[2], ids[1]]
    response = await reader_client.get("/api/v1/sessions?page=2&page_size=2")
    assert [item["id"] for item in response.json()["items"]] == [ids[0]]
    response = await reader_client.get("/api/v1/sessions?page=9&page_size=2")
    assert response.status_code == 200
    assert response.json()["items"] == []

    for query in ("page=0", "page_size=0", "page_size=101"):
        response = await reader_client.get(f"/api/v1/sessions?{query}")
        assert response.status_code == 422


async def test_get_session_detail_fields(reader_client) -> None:
    paper = await _create_paper("evidence for detail")
    response = await reader_client.post("/api/v1/search", json={"query": "evidence"})
    assert response.status_code == 201
    session_id = response.json()["id"]

    response = await reader_client.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "evidence"
    assert body["query_type"] == "text"
    assert body["paper_count"] == 1
    assert body["evidence_count"] >= 1
    assert body["has_evidence"] is True
    assert body["related_repo_count"] == 0
    assert body["conversation_id"] > 0
    assert body["system_message"]
    assert [item["id"] for item in body["papers"]] == [paper.id]
    assert body["created_at"].endswith("Z")


async def test_get_session_owner_only_and_admin_404(
    reader_client,
    make_client,
    user_factory,
) -> None:
    other = await user_factory(email="session-other@example.com")
    session_id, _ = await _create_session(other.id)

    response = await reader_client.get(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"

    admin = await user_factory(email="session-admin@example.com", role="admin")
    admin_client = await make_client()
    await login(admin_client, admin.email)
    response = await admin_client.get(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 404


async def test_session_papers_use_current_metadata(reader_client) -> None:
    paper = await _create_paper("snapshot evidence")
    response = await reader_client.post("/api/v1/search", json={"query": "snapshot"})
    assert response.status_code == 201
    session_id = response.json()["id"]

    async with get_session_factory()() as db:
        stored = await db.get(Paper, paper.id)
        assert stored is not None
        stored.title = "changed metadata"
        stored.is_archived = True
        await db.commit()

    response = await reader_client.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["paper_count"] == 1, body
    assert body["papers"][0]["title"] == "changed metadata"
    assert body["papers"][0]["is_archived"] is True
    assert body["paper_count"] == 1


async def _add_messages(conversation_id: int, contents: list[str]) -> list[int]:
    async with get_session_factory()() as db:
        messages = [
            Message(
                conversation_id=conversation_id,
                role=MessageRole.user.value,
                content=content,
                status=MessageStatus.complete.value,
                citations=[],
            )
            for content in contents
        ]
        db.add_all(messages)
        await db.commit()
        return [message.id for message in messages]


async def test_messages_cursor_pagination(reader_user, reader_client) -> None:
    session_id, conversation_id = await _create_session(reader_user.id)
    ids = await _add_messages(conversation_id, [f"message-{index}" for index in range(5)])

    response = await reader_client.get(f"/api/v1/sessions/{session_id}/messages?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [ids[4], ids[3]]
    assert body["has_more"] is True
    assert body["next_before_id"] == ids[3]
    assert [item["role"] for item in body["items"]] == ["user", "user"]

    response = await reader_client.get(
        f"/api/v1/sessions/{session_id}/messages?limit=2&before_id={body['next_before_id']}"
    )
    assert [item["id"] for item in response.json()["items"]] == [ids[2], ids[1]]
    assert response.json()["has_more"] is True
    assert response.json()["next_before_id"] == ids[1]

    response = await reader_client.get(
        f"/api/v1/sessions/{session_id}/messages?limit=2&before_id={ids[1]}"
    )
    assert [item["id"] for item in response.json()["items"]] == [ids[0]]
    assert response.json()["has_more"] is False
    assert response.json()["next_before_id"] is None


async def test_messages_include_system_message_on_oldest_page(reader_user, reader_client) -> None:
    session_id, conversation_id = await _create_session(
        reader_user.id,
        with_system=True,
    )
    await _add_messages(conversation_id, ["user-1", "user-2", "user-3", "user-4"])

    response = await reader_client.get(f"/api/v1/sessions/{session_id}/messages?limit=10")

    assert response.status_code == 200
    assert response.json()["items"][-1]["role"] == "system"
    assert response.json()["has_more"] is False
    assert response.json()["next_before_id"] is None

    response = await reader_client.get(f"/api/v1/sessions/{session_id}/messages?limit=4")
    assert all(item["role"] != "system" for item in response.json()["items"])


async def test_messages_empty_conversation(reader_user, reader_client) -> None:
    session_id, _ = await _create_session(reader_user.id)

    response = await reader_client.get(f"/api/v1/sessions/{session_id}/messages")

    assert response.status_code == 200
    assert response.json() == {"items": [], "has_more": False, "next_before_id": None}


async def test_session_messages_validation(reader_user, reader_client) -> None:
    session_id, _ = await _create_session(reader_user.id)

    for query in ("limit=0", "limit=201", "before_id=0"):
        response = await reader_client.get(f"/api/v1/sessions/{session_id}/messages?{query}")
        assert response.status_code == 422
