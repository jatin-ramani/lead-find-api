"""scrape job concurrency constraint

Enforce at most one Pending or Running scrape job in the database.
Uses a partial unique index on constant expression (1) where status IN ('Pending', 'Running').

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_scrape_jobs_single_active",
        "scrape_jobs",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("status IN ('Pending', 'Running')"),
        sqlite_where=sa.text("status IN ('Pending', 'Running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_scrape_jobs_single_active",
        table_name="scrape_jobs",
        postgresql_where=sa.text("status IN ('Pending', 'Running')"),
        sqlite_where=sa.text("status IN ('Pending', 'Running')"),
    )
