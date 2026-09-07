"""create email_automations and email_automation_executions tables

Revision ID: 0014
Revises: 0013
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create email_automations table
    op.create_table(
        "email_automations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("trigger_type", sa.String(length=50), nullable=False),
        sa.Column("subject_template", sa.String(length=255), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("delay_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="3", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_email_automations_id"),
        "email_automations",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automations_enabled"),
        "email_automations",
        ["enabled"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automations_trigger_type"),
        "email_automations",
        ["trigger_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automations_created_at"),
        "email_automations",
        ["created_at"],
        unique=False,
    )

    # Create email_automation_executions table
    op.create_table(
        "email_automation_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("automation_id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("follow_up_id", sa.Integer(), nullable=True),
        sa.Column("trigger_event", sa.String(length=50), nullable=False),
        sa.Column("trigger_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="scheduled", nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_rendered", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_id"],
            ["email_automations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["follow_up_id"],
            ["business_follow_ups.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_email_automation_executions_id"),
        "email_automation_executions",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automation_executions_automation_id"),
        "email_automation_executions",
        ["automation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automation_executions_business_id"),
        "email_automation_executions",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automation_executions_follow_up_id"),
        "email_automation_executions",
        ["follow_up_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automation_executions_trigger_event"),
        "email_automation_executions",
        ["trigger_event"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automation_executions_trigger_key"),
        "email_automation_executions",
        ["trigger_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_email_automation_executions_status"),
        "email_automation_executions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automation_executions_scheduled_at"),
        "email_automation_executions",
        ["scheduled_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_automation_executions_created_at"),
        "email_automation_executions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_exec_status_sched",
        "email_automation_executions",
        ["status", "scheduled_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_email_exec_status_sched", table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_created_at"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_scheduled_at"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_status"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_trigger_key"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_trigger_event"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_follow_up_id"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_business_id"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_automation_id"), table_name="email_automation_executions")
    op.drop_index(op.f("ix_email_automation_executions_id"), table_name="email_automation_executions")
    op.drop_table("email_automation_executions")

    op.drop_index(op.f("ix_email_automations_created_at"), table_name="email_automations")
    op.drop_index(op.f("ix_email_automations_trigger_type"), table_name="email_automations")
    op.drop_index(op.f("ix_email_automations_enabled"), table_name="email_automations")
    op.drop_index(op.f("ix_email_automations_id"), table_name="email_automations")
    op.drop_table("email_automations")
