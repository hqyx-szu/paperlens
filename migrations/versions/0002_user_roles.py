"""add user role

Revision ID: 0002_user_roles
Revises: 0001_auth_tables
Create Date: 2026-10-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_user_roles"
down_revision: str | Sequence[str] | None = "0001_auth_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="reader"),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
