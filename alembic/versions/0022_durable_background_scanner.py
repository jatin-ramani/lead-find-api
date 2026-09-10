"""add durable background scanning fields to scan_jobs and create scan_search_units table

Revision ID: 0022
Revises: 0021
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Alter scan_jobs table to add continuous scanning metrics and lifecycle fields
    with op.batch_alter_table("scan_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("category_family", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("subcategories_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("scan_radius_km", sa.Integer(), server_default="25", nullable=True))
        batch_op.add_column(sa.Column("center_latitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("center_longitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("total_search_units", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("completed_search_units", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("failed_search_units", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("coverage_progress", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("processed_progress", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("businesses_found", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("businesses_stored", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("businesses_skipped_no_contact", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("businesses_duplicates", sa.Integer(), server_default="0", nullable=True))
        batch_op.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("paused_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True))
        batch_op.create_index("ix_scan_jobs_city", ["city"], unique=False)
        batch_op.create_index("ix_scan_jobs_status", ["status"], unique=False)

    # 2. Create scan_search_units table
    op.create_table(
        "scan_search_units",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("scan_job_id", sa.Integer(), sa.ForeignKey("scan_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cell_index", sa.Integer(), nullable=False),
        sa.Column("cell_latitude", sa.Float(), nullable=False),
        sa.Column("cell_longitude", sa.Float(), nullable=False),
        sa.Column("cell_radius_meters", sa.Integer(), nullable=False),
        sa.Column("cell_label", sa.String(length=150), nullable=False),
        sa.Column("category_key", sa.String(length=150), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("results_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stored_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_no_contact_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicates_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    with op.batch_alter_table("scan_search_units", schema=None) as batch_op:
        batch_op.create_index("ix_scan_search_units_scan_job_id", ["scan_job_id"], unique=False)
        batch_op.create_index("ix_scan_search_units_status", ["status"], unique=False)
        batch_op.create_index("ix_scan_search_units_next_attempt_at", ["next_attempt_at"], unique=False)
        batch_op.create_index(
            "ix_search_unit_job_cell_cat",
            ["scan_job_id", "cell_index", "category_key"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_table("scan_search_units")
    with op.batch_alter_table("scan_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_scan_jobs_status")
        batch_op.drop_index("ix_scan_jobs_city")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("error_message")
        batch_op.drop_column("paused_at")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("businesses_duplicates")
        batch_op.drop_column("businesses_skipped_no_contact")
        batch_op.drop_column("businesses_stored")
        batch_op.drop_column("businesses_found")
        batch_op.drop_column("processed_progress")
        batch_op.drop_column("coverage_progress")
        batch_op.drop_column("failed_search_units")
        batch_op.drop_column("completed_search_units")
        batch_op.drop_column("total_search_units")
        batch_op.drop_column("center_longitude")
        batch_op.drop_column("center_latitude")
        batch_op.drop_column("scan_radius_km")
        batch_op.drop_column("subcategories_json")
        batch_op.drop_column("category_family")
