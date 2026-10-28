from sqlalchemy import text

from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Paper


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
