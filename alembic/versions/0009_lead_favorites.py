"""add is_favorite to businesses

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add is_favorite column with default false
    op.add_column(
        "businesses",
        sa.Column(
            "is_favorite",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # 2. Create index for efficient querying
    op.create_index(
        op.f("ix_businesses_is_favorite"),
        "businesses",
        ["is_favorite"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_businesses_is_favorite"), table_name="businesses")
    op.drop_column("businesses", "is_favorite")
