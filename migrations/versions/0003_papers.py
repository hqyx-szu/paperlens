"""create papers, paper_files and paper_chunks

Revision ID: 0003_papers
Revises: 0002_user_roles
Create Date: 2026-10-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0003_papers"
down_revision: str | Sequence[str] | None = "0002_user_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.String(length=255), nullable=True),
        sa.Column("abstract", mysql.MEDIUMTEXT(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_papers_content_hash", "papers", ["content_hash"], unique=False)
    op.create_index("ix_papers_is_archived", "papers", ["is_archived"], unique=False)
    op.create_index("ix_papers_status", "papers", ["status"], unique=False)
    op.create_index(
        "ft_papers_title_abstract",
        "papers",
        ["title", "abstract"],
        mysql_prefix="FULLTEXT",
        mysql_with_parser="ngram",
    )

    op.create_table(
        "paper_files",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_id", name="uq_paper_files_paper_id"),
        sa.UniqueConstraint("sha256", name="uq_paper_files_sha256"),
    )

    op.create_table(
        "paper_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "paper_id",
            "chunk_index",
            name="uq_paper_chunks_paper_index",
        ),
    )
    op.create_index("ix_paper_chunks_paper_id", "paper_chunks", ["paper_id"], unique=False)
    op.create_index(
        "ft_paper_chunks_content",
        "paper_chunks",
        ["content"],
        mysql_prefix="FULLTEXT",
        mysql_with_parser="ngram",
    )


def downgrade() -> None:
    op.drop_table("paper_chunks")
    op.drop_table("paper_files")
    op.drop_table("papers")
