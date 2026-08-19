"""add opaque administrative sessions

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_admin_sessions_expires_at"),
        "admin_sessions",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_sessions_expires_at"), table_name="admin_sessions")
    op.drop_table("admin_sessions")
