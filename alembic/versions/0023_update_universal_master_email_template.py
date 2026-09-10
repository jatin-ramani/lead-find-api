"""update universal master cold email template with modern web and AI content

Revision ID: 0023
Revises: 0022
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNIVERSAL_TEMPLATE = {
    "name": "Universal Master Cold Email",
    "description": "Master cold email outreach template for Codebait modern websites and AI systems.",
    "subject": "Quick idea for {{Business Name}}",
    "body": (
        "<p>Hi {{Contact Name}},</p>\n\n"
        "<p>I came across {{Business Name}} in {{City}}.</p>\n\n"
        "<p>We build modern websites and AI-powered systems that help businesses <strong>look more credible, capture more leads and turn visitors into customers.</strong></p>\n\n"
        "<p>These days, a website isn't just an online presence — it can become one of the strongest channels for <strong>new customers, enquiries and appointments.</strong></p>\n\n"
        "<p>Would you be interested in seeing a quick demo?</p>\n\n"
        "<p>Best,<br>\n"
        "<strong>Jatin Ramani</strong><br>\n"
        "Founder, Codebait<br>\n"
        "7861035002</p>"
    ),
}


def upgrade() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    email_templates_table = sa.table(
        "email_templates",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("subject", sa.String),
        sa.column("body", sa.Text),
        sa.column("is_archived", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    # Safe update condition: only update default universal templates or templates storing the legacy mockup text
    update_condition = sa.or_(
        email_templates_table.c.name == "Universal Master Cold Email — Website Mockup",
        email_templates_table.c.name == "Universal Master Cold Email",
        email_templates_table.c.subject == "A free website mockup for {{business_name}}?",
        sa.and_(
            email_templates_table.c.name.ilike("Universal Master Cold Email%"),
            email_templates_table.c.body.like("%mockup%"),
        ),
    )

    if context.is_offline_mode():
        op.execute(
            email_templates_table.update()
            .where(update_condition)
            .values(
                name=UNIVERSAL_TEMPLATE["name"],
                description=UNIVERSAL_TEMPLATE["description"],
                subject=UNIVERSAL_TEMPLATE["subject"],
                body=UNIVERSAL_TEMPLATE["body"],
                updated_at=now,
            )
        )
        return

    connection = op.get_bind()
    if connection is not None:
        connection.execute(
            email_templates_table.update()
            .where(update_condition)
            .values(
                name=UNIVERSAL_TEMPLATE["name"],
                description=UNIVERSAL_TEMPLATE["description"],
                subject=UNIVERSAL_TEMPLATE["subject"],
                body=UNIVERSAL_TEMPLATE["body"],
                updated_at=now,
            )
        )


def downgrade() -> None:
    pass
