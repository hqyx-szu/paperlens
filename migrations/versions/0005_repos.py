"""repos, repo papers and repo stars

Revision ID: 0005_repos
Revises: 0004_sessions_chat
Create Date: 2026-10-28 20:45:43.162148

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_repos"
down_revision: str | Sequence[str] | None = "0004_sessions_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "repos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), server_default="", nullable=False),
        sa.Column("visibility", sa.String(length=16), server_default="private", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_repos_owner_name"),
    )
    op.create_index(op.f("ix_repos_owner_id"), "repos", ["owner_id"], unique=False)
    op.create_index("ix_repos_owner_updated", "repos", ["owner_id", "updated_at"], unique=False)

    op.create_table(
        "repo_papers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "paper_id", name="uq_repo_papers_repo_paper"),
    )
    op.create_index(op.f("ix_repo_papers_paper_id"), "repo_papers", ["paper_id"], unique=False)
    op.create_index(op.f("ix_repo_papers_repo_id"), "repo_papers", ["repo_id"], unique=False)

    op.create_table(
        "repo_stars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "user_id", name="uq_repo_stars_repo_user"),
    )
    op.create_index(op.f("ix_repo_stars_repo_id"), "repo_stars", ["repo_id"], unique=False)
    op.create_index(op.f("ix_repo_stars_user_id"), "repo_stars", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    MySQL 1553: 删除被外键使用的索引会失败，因此只按依赖反序 drop_table，
    让 MySQL 自己处理索引与外键（与 0002/0003/0004 保持一致）。
    """
    op.drop_table("repo_stars")
    op.drop_table("repo_papers")
    op.drop_table("repos")
