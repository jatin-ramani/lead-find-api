"""create business_follow_ups table

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create business_follow_ups table
    op.create_table(
        "business_follow_ups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("priority", sa.String(length=20), server_default="medium", nullable=False),
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
        op.f("ix_business_follow_ups_id"),
        "business_follow_ups",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_follow_ups_business_id"),
        "business_follow_ups",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_follow_ups_status"),
        "business_follow_ups",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_follow_ups_priority"),
        "business_follow_ups",
        ["priority"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_follow_ups_due_at"),
        "business_follow_ups",
        ["due_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_follow_ups_created_at"),
        "business_follow_ups",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_business_follow_ups_biz_due",
        "business_follow_ups",
        ["business_id", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_business_follow_ups_biz_status",
        "business_follow_ups",
        ["business_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_business_follow_ups_biz_status", table_name="business_follow_ups")
    op.drop_index("ix_business_follow_ups_biz_due", table_name="business_follow_ups")
    op.drop_index(op.f("ix_business_follow_ups_created_at"), table_name="business_follow_ups")
    op.drop_index(op.f("ix_business_follow_ups_due_at"), table_name="business_follow_ups")
    op.drop_index(op.f("ix_business_follow_ups_priority"), table_name="business_follow_ups")
    op.drop_index(op.f("ix_business_follow_ups_status"), table_name="business_follow_ups")
    op.drop_index(op.f("ix_business_follow_ups_business_id"), table_name="business_follow_ups")
    op.drop_index(op.f("ix_business_follow_ups_id"), table_name="business_follow_ups")
    op.drop_table("business_follow_ups")
