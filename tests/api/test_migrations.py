from sqlalchemy import text

from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Conversation, Message, Paper


async def test_repo_tables_constraints_and_defaults_exist() -> None:
    async with get_session_factory()() as db:
        tables = (
            (
                await db.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = DATABASE()
                          AND table_name IN ('repos', 'repo_papers', 'repo_stars')
                        """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(tables) == {"repos", "repo_papers", "repo_stars"}

        unique_indexes = (
            await db.execute(
                text(
                    """
                    SELECT table_name, index_name
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND non_unique = 0
                      AND index_name IN (
                        'uq_repos_owner_name',
                        'uq_repo_papers_repo_paper',
                        'uq_repo_stars_repo_user'
                      )
                    """
                )
            )
        ).all()
        assert {(row[0], row[1]) for row in unique_indexes} == {
            ("repos", "uq_repos_owner_name"),
            ("repo_papers", "uq_repo_papers_repo_paper"),
            ("repo_stars", "uq_repo_stars_repo_user"),
        }

        repo_columns = (
            await db.execute(
                text(
                    """
                    SELECT column_name, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = 'repos'
                      AND column_name IN ('visibility', 'description')
                    """
                )
            )
        ).all()
        columns = {row[0]: row[1:] for row in repo_columns}

    assert columns["visibility"][0] == "NO"
    assert columns["visibility"][1] == "private"
    assert columns["description"][1] == ""


async def test_paper_tables_and_fulltext_indexes_exist() -> None:
    async with get_session_factory()() as db:
        tables = (
            (
                await db.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = DATABASE()
                          AND table_name IN ('papers', 'paper_files', 'paper_chunks')
                        """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(tables) == {"papers", "paper_files", "paper_chunks"}

        fulltext_indexes = (
            await db.execute(
                text(
                    """
                    SELECT table_name, index_name
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND table_name IN ('papers', 'paper_chunks')
                      AND index_type = 'FULLTEXT'
                    """
                )
            )
        ).all()

    assert {(row[0], row[1]) for row in fulltext_indexes} == {
        ("papers", "ft_papers_title_abstract"),
        ("paper_chunks", "ft_paper_chunks_content"),
    }


async def test_paper_defaults() -> None:
    async with get_session_factory()() as db:
        paper = Paper(title="Defaults test")
        db.add(paper)
        await db.commit()
        await db.refresh(paper)

        assert paper.title == "Defaults test"
        assert paper.authors == []
        assert paper.status == PaperStatus.queued.value
        assert paper.is_archived is False
        assert paper.created_at is not None
        assert paper.updated_at is not None


async def test_chat_tables_constraints_and_defaults_exist(user_factory) -> None:
    chat_tables = {
        "search_sessions",
        "session_papers",
        "session_evidence",
        "conversations",
        "messages",
    }
    async with get_session_factory()() as db:
        tables = (
            (
                await db.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = DATABASE()
                          AND table_name IN (
                            'search_sessions', 'session_papers', 'session_evidence',
                            'conversations', 'messages'
                          )
                        """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(tables) == chat_tables

        unique_indexes = (
            await db.execute(
                text(
                    """
                    SELECT table_name, index_name
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND non_unique = 0
                      AND index_name IN (
                        'uq_conversations_user_scope',
                        'uq_session_papers_session_paper',
                        'uq_session_evidence_session_chunk'
                      )
                    """
                )
            )
        ).all()
        assert {(row[0], row[1]) for row in unique_indexes} == {
            ("conversations", "uq_conversations_user_scope"),
            ("session_papers", "uq_session_papers_session_paper"),
            ("session_evidence", "uq_session_evidence_session_chunk"),
        }

        message_columns = (
            await db.execute(
                text(
                    """
                    SELECT column_name, is_nullable, column_default, data_type
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = 'messages'
                      AND column_name IN ('status', 'citations')
                    """
                )
            )
        ).all()
        columns = {row[0]: row[1:] for row in message_columns}

    assert columns["status"][0] == "NO"
    assert columns["status"][1] == MessageStatus.complete.value
    assert columns["citations"][0] == "NO"
    assert columns["citations"][1] is None
    assert columns["citations"][2] == "json"

    user = await user_factory()
    async with get_session_factory()() as db:
        conversation = Conversation(
            user_id=user.id,
            scope_type=ChatScope.session.value,
            scope_id=1,
        )
        db.add(conversation)
        await db.flush()
        message = Message(
            conversation_id=conversation.id,
            role=MessageRole.user.value,
            content="",
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)

    assert message.status == MessageStatus.complete.value
    assert message.citations == []
