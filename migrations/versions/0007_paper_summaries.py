"""paper summaries (four-dimension cache)

Revision ID: 0007_paper_summaries
Revises: 0006_chunk_embeddings
Create Date: 2026-10-29 11:21:08.827723

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_paper_summaries"
down_revision: str | Sequence[str] | None = "0006_chunk_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "paper_summaries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "paper_id",
            "level",
            "content_hash",
            "prompt_version",
            "model",
            name="uq_paper_summaries_cache_key",
        ),
    )
    op.create_index(
        op.f("ix_paper_summaries_paper_id"),
        "paper_summaries",
        ["paper_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema.

    MySQL 1553：只 drop_table，让 MySQL 自己处理索引与外键（与其它迁移一致）。
    """
    op.drop_table("paper_summaries")
