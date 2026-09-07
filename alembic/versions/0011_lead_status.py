"""add lead_status to businesses

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add lead_status column with default "new"
    op.add_column(
        "businesses",
        sa.Column(
            "lead_status",
            sa.String(length=30),
            nullable=False,
            server_default="new",
        ),
    )

    # 2. Create index for efficient filtering and sorting
    op.create_index(
        op.f("ix_businesses_lead_status"),
        "businesses",
        ["lead_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_businesses_lead_status"), table_name="businesses")
    op.drop_column("businesses", "lead_status")
