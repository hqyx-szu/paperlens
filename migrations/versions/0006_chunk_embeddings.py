"""chunk embeddings for vector retrieval

Revision ID: 0006_chunk_embeddings
Revises: 0005_repos
Create Date: 2026-10-29 11:08:02.263676

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_chunk_embeddings"
down_revision: str | Sequence[str] | None = "0005_repos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["paper_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("chunk_embeddings")
