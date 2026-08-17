"""initial schema

Baseline migration describing the whole schema as it stands today:
businesses, scan_jobs, scrape_jobs and website_data.

WHY THIS ONE IS CONDITIONAL
---------------------------
This project ran on ``Base.metadata.create_all()`` before Alembic existed, so
databases in the wild are in mixed states — the development database, for
example, already had ``businesses`` and ``scan_jobs`` (with real rows) but was
missing ``scrape_jobs`` and ``website_data`` entirely.

Each table is therefore created only when it is absent. That makes this one
migration safe to run against a clean database *and* against a partially
created one, without dropping data and without the usual `alembic stamp`
dance that every existing deployment would otherwise need.

This concession applies to the baseline only. Migrations generated from here
on must NOT be conditional — they are the schema history and have to be
deterministic.

Revision ID: 0001
Revises:
Create Date: 2026-08-07

"""
from typing import Sequence, Set, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> Set[str]:
    """
    Table names already present in the target database.

    In offline mode (``alembic upgrade head --sql``) there is no connection to
    inspect, so the answer is "none" and the full DDL is emitted. That is the
    right answer for the offline use case: the output is a script for a fresh
    database or for a reviewer, not something applied blind to a live one.
    Without this the command dies on `sa.inspect(None)`.
    """

    if context.is_offline_mode():
        return set()

    bind = op.get_bind()

    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "businesses" not in existing:
        op.create_table(
            "businesses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("website", sa.String(), nullable=True),
            sa.Column("city", sa.String(), nullable=True),
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("address", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("place_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("place_id"),
        )
        op.create_index(
            op.f("ix_businesses_id"), "businesses", ["id"], unique=False
        )

    if "scan_jobs" not in existing:
        op.create_table(
            "scan_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("city", sa.String(), nullable=True),
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("progress", sa.Integer(), nullable=True),
            sa.Column("total_businesses", sa.Integer(), nullable=True),
            sa.Column("new_businesses", sa.Integer(), nullable=True),
            sa.Column("total_cells", sa.Integer(), nullable=True),
            sa.Column("completed_cells", sa.Integer(), nullable=True),
            sa.Column("current_cell", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_scan_jobs_id"), "scan_jobs", ["id"], unique=False
        )

    if "scrape_jobs" not in existing:
        op.create_table(
            "scrape_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("progress", sa.Integer(), nullable=True),
            sa.Column("total_websites", sa.Integer(), nullable=True),
            sa.Column("completed", sa.Integer(), nullable=True),
            sa.Column("success", sa.Integer(), nullable=True),
            sa.Column("failed", sa.Integer(), nullable=True),
            sa.Column("current_business_id", sa.Integer(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["current_business_id"],
                ["businesses.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_scrape_jobs_id"), "scrape_jobs", ["id"], unique=False
        )

    if "website_data" not in existing:
        op.create_table(
            "website_data",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("business_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("meta_description", sa.Text(), nullable=True),
            sa.Column("emails", sa.Text(), nullable=True),
            sa.Column("facebook", sa.String(), nullable=True),
            sa.Column("instagram", sa.String(), nullable=True),
            sa.Column("linkedin", sa.String(), nullable=True),
            sa.Column("youtube", sa.String(), nullable=True),
            sa.Column("twitter", sa.String(), nullable=True),
            sa.Column("whatsapp", sa.String(), nullable=True),
            sa.Column("scraped_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(
                ["business_id"],
                ["businesses.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_website_data_business_id"),
            "website_data",
            ["business_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_website_data_id"), "website_data", ["id"], unique=False
        )


def downgrade() -> None:
    # Dropped children first so the foreign keys never block the drop.
    existing = _existing_tables()

    if "website_data" in existing:
        op.drop_index(op.f("ix_website_data_id"), table_name="website_data")
        op.drop_index(
            op.f("ix_website_data_business_id"), table_name="website_data"
        )
        op.drop_table("website_data")

    if "scrape_jobs" in existing:
        op.drop_index(op.f("ix_scrape_jobs_id"), table_name="scrape_jobs")
        op.drop_table("scrape_jobs")

    if "scan_jobs" in existing:
        op.drop_index(op.f("ix_scan_jobs_id"), table_name="scan_jobs")
        op.drop_table("scan_jobs")

    if "businesses" in existing:
        op.drop_index(op.f("ix_businesses_id"), table_name="businesses")
        op.drop_table("businesses")
