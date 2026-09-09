"""add next_attempt_at and paused_reason for durable campaign queue hardening

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add next_attempt_at to email_campaign_recipients
    with op.batch_alter_table("email_campaign_recipients", schema=None) as batch_op:
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch_op.create_index(
            "ix_email_campaign_recipients_next_attempt_at",
            ["next_attempt_at"],
            unique=False,
        )

    # 2. Add paused_reason to email_campaigns
    with op.batch_alter_table("email_campaigns", schema=None) as batch_op:
        batch_op.add_column(sa.Column("paused_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    # 1. Remove paused_reason from email_campaigns
    with op.batch_alter_table("email_campaigns", schema=None) as batch_op:
        batch_op.drop_column("paused_reason")

    # 2. Remove next_attempt_at from email_campaign_recipients
    with op.batch_alter_table("email_campaign_recipients", schema=None) as batch_op:
        batch_op.drop_index("ix_email_campaign_recipients_next_attempt_at")
        batch_op.drop_column("next_attempt_at")
