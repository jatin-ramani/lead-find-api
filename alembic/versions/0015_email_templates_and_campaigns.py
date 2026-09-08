"""create email_templates, email_campaigns and email_campaign_recipients tables

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create email_templates table
    op.create_table(
        "email_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_email_templates_id"),
        "email_templates",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_templates_name"),
        "email_templates",
        ["name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_templates_is_archived"),
        "email_templates",
        ["is_archived"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_templates_created_at"),
        "email_templates",
        ["created_at"],
        unique=False,
    )

    # 2. Create email_campaigns table
    op.create_table(
        "email_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="draft", nullable=False),
        sa.Column("filter_criteria", sa.Text(), server_default="{}", nullable=False),
        sa.Column("recipient_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["email_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_email_campaigns_id"),
        "email_campaigns",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaigns_name"),
        "email_campaigns",
        ["name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaigns_template_id"),
        "email_campaigns",
        ["template_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaigns_status"),
        "email_campaigns",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaigns_scheduled_at"),
        "email_campaigns",
        ["scheduled_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaigns_created_at"),
        "email_campaigns",
        ["created_at"],
        unique=False,
    )

    # 3. Create email_campaign_recipients table
    op.create_table(
        "email_campaign_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("recipient_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["email_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_email_campaign_recipients_id"),
        "email_campaign_recipients",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaign_recipients_campaign_id"),
        "email_campaign_recipients",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaign_recipients_business_id"),
        "email_campaign_recipients",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaign_recipients_status"),
        "email_campaign_recipients",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_campaign_recipients_created_at"),
        "email_campaign_recipients",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "uq_campaign_business",
        "email_campaign_recipients",
        ["campaign_id", "business_id"],
        unique=True,
    )
    op.create_index(
        "ix_campaign_recipient_status",
        "email_campaign_recipients",
        ["campaign_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_recipient_status", table_name="email_campaign_recipients")
    op.drop_index("uq_campaign_business", table_name="email_campaign_recipients")
    op.drop_index(op.f("ix_email_campaign_recipients_created_at"), table_name="email_campaign_recipients")
    op.drop_index(op.f("ix_email_campaign_recipients_status"), table_name="email_campaign_recipients")
    op.drop_index(op.f("ix_email_campaign_recipients_business_id"), table_name="email_campaign_recipients")
    op.drop_index(op.f("ix_email_campaign_recipients_campaign_id"), table_name="email_campaign_recipients")
    op.drop_index(op.f("ix_email_campaign_recipients_id"), table_name="email_campaign_recipients")
    op.drop_table("email_campaign_recipients")

    op.drop_index(op.f("ix_email_campaigns_created_at"), table_name="email_campaigns")
    op.drop_index(op.f("ix_email_campaigns_scheduled_at"), table_name="email_campaigns")
    op.drop_index(op.f("ix_email_campaigns_status"), table_name="email_campaigns")
    op.drop_index(op.f("ix_email_campaigns_template_id"), table_name="email_campaigns")
    op.drop_index(op.f("ix_email_campaigns_name"), table_name="email_campaigns")
    op.drop_index(op.f("ix_email_campaigns_id"), table_name="email_campaigns")
    op.drop_table("email_campaigns")

    op.drop_index(op.f("ix_email_templates_created_at"), table_name="email_templates")
    op.drop_index(op.f("ix_email_templates_is_archived"), table_name="email_templates")
    op.drop_index(op.f("ix_email_templates_name"), table_name="email_templates")
    op.drop_index(op.f("ix_email_templates_id"), table_name="email_templates")
    op.drop_table("email_templates")
