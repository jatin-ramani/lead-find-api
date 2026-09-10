"""add composite index on business_id and status for fast sent-lead exclusion queries

Revision ID: 0020
Revises: 0019
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add composite index on email_campaign_recipients(business_id, status)
    with op.batch_alter_table("email_campaign_recipients", schema=None) as batch_op:
        batch_op.create_index(
            "ix_recipient_business_status",
            ["business_id", "status"],
            unique=False,
        )

    # 2. Add composite index on email_automation_executions(business_id, status)
    with op.batch_alter_table("email_automation_executions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_exec_business_status",
            ["business_id", "status"],
            unique=False,
        )


def downgrade() -> None:
    # 1. Drop composite index on email_automation_executions
    with op.batch_alter_table("email_automation_executions", schema=None) as batch_op:
        batch_op.drop_index("ix_exec_business_status")

    # 2. Drop composite index on email_campaign_recipients
    with op.batch_alter_table("email_campaign_recipients", schema=None) as batch_op:
        batch_op.drop_index("ix_recipient_business_status")
