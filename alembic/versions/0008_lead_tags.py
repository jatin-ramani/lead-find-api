"""add lead tags and business tags association

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create tags table
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tags_id"), "tags", ["id"], unique=False)
    op.create_index(op.f("ix_tags_name"), "tags", ["name"], unique=True)
    op.create_index(op.f("ix_tags_slug"), "tags", ["slug"], unique=True)

    # 2. Create business_tags association table
    op.create_table(
        "business_tags",
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("business_id", "tag_id"),
    )
    op.create_index(
        op.f("ix_business_tags_business_id"),
        "business_tags",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_tags_tag_id"),
        "business_tags",
        ["tag_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_business_tags_tag_id"), table_name="business_tags")
    op.drop_index(op.f("ix_business_tags_business_id"), table_name="business_tags")
    op.drop_table("business_tags")

    op.drop_index(op.f("ix_tags_slug"), table_name="tags")
    op.drop_index(op.f("ix_tags_name"), table_name="tags")
    op.drop_index(op.f("ix_tags_id"), table_name="tags")
    op.drop_table("tags")
