"""create business_notes table

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create business_notes table
    op.create_table(
        "business_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_business_notes_id"),
        "business_notes",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_notes_business_id"),
        "business_notes",
        ["business_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_business_notes_business_id"), table_name="business_notes")
    op.drop_index(op.f("ix_business_notes_id"), table_name="business_notes")
    op.drop_table("business_notes")
