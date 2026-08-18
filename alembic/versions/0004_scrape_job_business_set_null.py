"""set scrape job business pointer to null on delete

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _existing_fk_name() -> str:
    return ("scrape_jobs_current_business_id_fkey"
            if op.get_context().dialect.name == "postgresql"
            else "fk_scrape_jobs_current_business_id_businesses")


def upgrade() -> None:
    with op.batch_alter_table("scrape_jobs", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint(_existing_fk_name(), type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_scrape_jobs_current_business_id_businesses", "businesses",
            ["current_business_id"], ["id"], ondelete="SET NULL",
        )

def downgrade() -> None:
    with op.batch_alter_table("scrape_jobs", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint("fk_scrape_jobs_current_business_id_businesses", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_scrape_jobs_current_business_id_businesses", "businesses",
            ["current_business_id"], ["id"],
        )
