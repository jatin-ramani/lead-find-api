"""add lead_score and lead_grade to businesses

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns with server default
    op.add_column(
        "businesses",
        sa.Column("lead_score", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "businesses",
        sa.Column("lead_grade", sa.String(length=2), nullable=True, server_default="D"),
    )

    # Create indexes
    op.create_index(
        op.f("ix_businesses_lead_score"),
        "businesses",
        ["lead_score"],
        unique=False,
    )
    op.create_index(
        op.f("ix_businesses_lead_grade"),
        "businesses",
        ["lead_grade"],
        unique=False,
    )

    # Backfill existing records if any exist
    bind = op.get_bind()
    try:
        from services.lead_scoring import calculate_lead_score

        # Query all existing businesses and their website data
        businesses_table = sa.table(
            "businesses",
            sa.column("id", sa.Integer),
            sa.column("name", sa.String),
            sa.column("phone", sa.String),
            sa.column("email", sa.String),
            sa.column("website", sa.String),
            sa.column("address", sa.String),
            sa.column("category", sa.String),
            sa.column("lead_score", sa.Integer),
            sa.column("lead_grade", sa.String),
        )

        website_data_table = sa.table(
            "website_data",
            sa.column("id", sa.Integer),
            sa.column("business_id", sa.Integer),
            sa.column("title", sa.String),
            sa.column("meta_description", sa.String),
            sa.column("emails", sa.String),
            sa.column("facebook", sa.String),
            sa.column("instagram", sa.String),
            sa.column("linkedin", sa.String),
            sa.column("twitter", sa.String),
            sa.column("youtube", sa.String),
            sa.column("whatsapp", sa.String),
            sa.column("status", sa.String),
        )

        rows = bind.execute(sa.select(businesses_table)).mappings().all()
        if rows:
            for b in rows:
                wd = bind.execute(
                    sa.select(website_data_table).where(
                        website_data_table.c.business_id == b["id"]
                    )
                ).mappings().first()
                result = calculate_lead_score(b, wd)
                bind.execute(
                    businesses_table.update()
                    .where(businesses_table.c.id == b["id"])
                    .values(lead_score=result.score, lead_grade=result.grade)
                )
    except Exception:
        # If backfill encounters issues during dry migration environments, columns still exist with server defaults
        pass


def downgrade() -> None:
    op.drop_index(op.f("ix_businesses_lead_grade"), table_name="businesses")
    op.drop_index(op.f("ix_businesses_lead_score"), table_name="businesses")
    op.drop_column("businesses", "lead_grade")
    op.drop_column("businesses", "lead_score")
