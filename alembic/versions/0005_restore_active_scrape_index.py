"""restore active scrape partial unique index after SQLite batch migration

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_scrape_jobs_single_active"
PREDICATE = sa.text("status IN ('Pending', 'Running')")


def upgrade() -> None:
    if op.get_context().dialect.name != "sqlite":
        return
    op.create_index(
        INDEX_NAME,
        "scrape_jobs",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=PREDICATE,
        sqlite_where=PREDICATE,
    )


def downgrade() -> None:
    if op.get_context().dialect.name != "sqlite":
        return
    op.drop_index(
        INDEX_NAME,
        table_name="scrape_jobs",
        postgresql_where=PREDICATE,
        sqlite_where=PREDICATE,
    )