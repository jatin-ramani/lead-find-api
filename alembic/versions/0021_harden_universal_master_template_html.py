"""harden universal master cold email and default grade templates with semantic HTML

Revision ID: 0021
Revises: 0020
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNIVERSAL_TEMPLATE = {
    "name": "Universal Master Cold Email — Website Mockup",
    "description": "Master cold email outreach template for Codebait website mockup offer.",
    "subject": "A free website mockup for {{business_name}}?",
    "body": (
        "<p>Hi {{business_name}} team,</p>\n\n"
        "<p>A strong website can completely change how a potential customer sees a business before they ever make a call.</p>\n\n"
        "<p>We're <strong>Codebait</strong>, a web design studio helping local businesses build modern, high-converting websites — "
        "from complete redesigns to AI-powered features like smart chatbots and automated booking.</p>\n\n"
        "<p>Instead of sending you a long sales pitch, we'd rather <strong>show you what your business could look like online</strong>.</p>\n\n"
        "<p>Reply to this email and we'll create a <strong>free, no-obligation website mockup</strong> for {{business_name}} — "
        "completely free, with no commitment required.</p>\n\n"
        "<p>If you like what you see, we can talk about taking it further. If not, no problem.</p>\n\n"
        "<p><strong>Would you be open to seeing the mockup?</strong></p>\n\n"
        "<p>Best,<br>\n"
        "<strong>Jatin Ramani</strong><br>\n"
        "Founder, Codebait<br>\n"
        "7861035002<br>\n"
        "jatinrmn@gmail.com</p>"
    ),
}

GRADE_TEMPLATES = [
    {
        "name": "Grade A — High Priority Lead",
        "description": "Default outreach template for Grade A (High Priority) leads.",
        "subject": "Introduction regarding {{business_name}}",
        "body": (
            "<p>Hi {{contact_name}},</p>\n\n"
            "<p>I am reaching out to introduce our team to {{business_name}}. We work with local businesses "
            "to support digital customer acquisition and help streamline online inquiries.</p>\n\n"
            "<p>If you are exploring new growth channels this quarter, would you be open to a brief 10-minute "
            "introductory call next week?</p>\n\n"
            "<p>Best regards,<br>Partnerships Team</p>"
        ),
    },
    {
        "name": "Grade B — Good Lead",
        "description": "Default outreach template for Grade B (Good) leads.",
        "subject": "Connecting with {{business_name}}",
        "body": (
            "<p>Hi {{contact_name}},</p>\n\n"
            "<p>I hope you are having a productive week. I wanted to reach out and introduce our services "
            "to {{business_name}}.</p>\n\n"
            "<p>We help businesses improve their local web visibility and connect with more potential customers. "
            "If this is an area of focus for your team, I would be happy to share a few ideas.</p>\n\n"
            "<p>Would you have a few minutes for a quick chat sometime this week?</p>\n\n"
            "<p>Best regards,<br>Outreach Team</p>"
        ),
    },
    {
        "name": "Grade C — Potential Lead",
        "description": "Default outreach template for Grade C (Potential) leads.",
        "subject": "Quick question for {{business_name}}",
        "body": (
            "<p>Hello {{contact_name}},</p>\n\n"
            "<p>I am reaching out to see if {{business_name}} is currently looking for support with website "
            "optimization or local customer outreach.</p>\n\n"
            "<p>If so, let me know if it would be helpful to send over a brief overview of how we assist "
            "businesses in your area.</p>\n\n"
            "<p>Best regards,<br>Client Relations Team</p>"
        ),
    },
    {
        "name": "Grade D — Low Priority Lead",
        "description": "Default outreach template for Grade D (Low Priority) leads.",
        "subject": "Inquiry for {{business_name}}",
        "body": (
            "<p>Hi {{contact_name}},</p>\n\n"
            "<p>I am reaching out with a quick note to see if {{business_name}} is exploring any new marketing "
            "or web development initiatives at the moment.</p>\n\n"
            "<p>If this is something on your radar, please feel free to reply and we can connect.</p>\n\n"
            "<p>Thank you for your time,<br>Support Team</p>"
        ),
    },
]


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

    if context.is_offline_mode():
        # Only repair unhardened Universal Master Cold Email templates lacking <p>
        op.execute(
            email_templates_table.update()
            .where(
                sa.and_(
                    email_templates_table.c.name == UNIVERSAL_TEMPLATE["name"],
                    sa.not_(email_templates_table.c.body.like("%<p>%")),
                )
            )
            .values(
                body=UNIVERSAL_TEMPLATE["body"],
                subject=UNIVERSAL_TEMPLATE["subject"],
                updated_at=now,
            )
        )
        for tpl in GRADE_TEMPLATES:
            op.execute(
                email_templates_table.update()
                .where(
                    sa.and_(
                        email_templates_table.c.name == tpl["name"],
                        sa.not_(email_templates_table.c.body.like("%<p>%")),
                    )
                )
                .values(
                    body=tpl["body"],
                    subject=tpl["subject"],
                    updated_at=now,
                )
            )
        return

    connection = op.get_bind()
    if connection is not None:
        # Strictly repair default Universal Master template if missing <p> tags, avoiding custom templates
        connection.execute(
            email_templates_table.update()
            .where(
                sa.and_(
                    email_templates_table.c.name == UNIVERSAL_TEMPLATE["name"],
                    sa.not_(email_templates_table.c.body.like("%<p>%")),
                )
            )
            .values(
                body=UNIVERSAL_TEMPLATE["body"],
                subject=UNIVERSAL_TEMPLATE["subject"],
                updated_at=now,
            )
        )

        # Strictly repair default grade templates if missing <p> tags
        for tpl in GRADE_TEMPLATES:
            connection.execute(
                email_templates_table.update()
                .where(
                    sa.and_(
                        email_templates_table.c.name == tpl["name"],
                        sa.not_(email_templates_table.c.body.like("%<p>%")),
                    )
                )
                .values(
                    body=tpl["body"],
                    subject=tpl["subject"],
                    updated_at=now,
                )
            )


def downgrade() -> None:
    pass
