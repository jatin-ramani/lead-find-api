"""create gmail_oauth_credentials table

Revision ID: 0016
Revises: 0015
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gmail_oauth_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email_address", sa.String(length=255), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("token_expiry", sa.DateTime(), nullable=False),
        sa.Column("scopes", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("daily_send_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("daily_send_reset_date", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_address", name="uq_gmail_oauth_credentials_email"),
    )
    op.create_index(
        "ix_gmail_oauth_credentials_email",
        "gmail_oauth_credentials",
        ["email_address"],
        unique=True,
    )
    op.create_index(
        "ix_gmail_oauth_credentials_active",
        "gmail_oauth_credentials",
        ["is_active"],
    )
    op.create_index(
        "ix_gmail_oauth_credentials_expiry",
        "gmail_oauth_credentials",
        ["token_expiry"],
    )


def downgrade() -> None:
    op.drop_index("ix_gmail_oauth_credentials_expiry", table_name="gmail_oauth_credentials")
    op.drop_index("ix_gmail_oauth_credentials_active", table_name="gmail_oauth_credentials")
    op.drop_index("ix_gmail_oauth_credentials_email", table_name="gmail_oauth_credentials")
    op.drop_table("gmail_oauth_credentials")
