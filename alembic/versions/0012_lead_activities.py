"""create business_activities table

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create business_activities table
    op.create_table(
        "business_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("activity_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_business_activities_id"),
        "business_activities",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_activities_business_id"),
        "business_activities",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_activities_activity_type"),
        "business_activities",
        ["activity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_activities_created_at"),
        "business_activities",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_business_activities_biz_created",
        "business_activities",
        ["business_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_business_activities_biz_created", table_name="business_activities")
    op.drop_index(op.f("ix_business_activities_created_at"), table_name="business_activities")
    op.drop_index(op.f("ix_business_activities_activity_type"), table_name="business_activities")
    op.drop_index(op.f("ix_business_activities_business_id"), table_name="business_activities")
    op.drop_index(op.f("ix_business_activities_id"), table_name="business_activities")
    op.drop_table("business_activities")
