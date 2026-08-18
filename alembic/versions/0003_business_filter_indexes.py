"""add business filter indexes for city and category

Add B-tree indexes on businesses.city and businesses.category to optimize
GET /businesses filtering, category searches, and pagination counts.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_businesses_city",
        "businesses",
        ["city"],
        unique=False,
    )
    op.create_index(
        "ix_businesses_category",
        "businesses",
        ["category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_businesses_category",
        table_name="businesses",
    )
    op.drop_index(
        "ix_businesses_city",
        table_name="businesses",
    )
