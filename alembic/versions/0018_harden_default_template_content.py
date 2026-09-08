"""harden default grade email template content for deliverability

Revision ID: 0018
Revises: 0017
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HARDENED_GRADE_TEMPLATES = [
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

PREVIOUS_GRADE_TEMPLATES = [
    {
        "name": "Grade A — High Priority Lead",
        "subject": "Partnership opportunity for {{business_name}}",
        "body": (
            "<p>Hi {{contact_name}},</p>\n\n"
            "<p>I came across {{business_name}} while researching top-performing businesses in your area. "
            "Given your strong track record and positive market presence, I wanted to reach out directly.</p>\n\n"
            "<p>We specialize in helping established companies optimize their digital customer acquisition and "
            "improve local conversion rates.</p>\n\n"
            "<p>Would you be open to a brief 10-minute conversation this week to see how we could help "
            "{{business_name}} accelerate its growth?</p>\n\n"
            "<p>Best regards,<br>Growth &amp; Partnerships Team</p>"
        ),
    },
    {
        "name": "Grade B — Good Lead",
        "subject": "Growth ideas for {{business_name}}",
        "body": (
            "<p>Hi {{contact_name}},</p>\n\n"
            "<p>I recently reviewed {{business_name}} and noticed several solid strengths in your current offering. "
            "With a few targeted digital optimizations, there is a clear opportunity to increase your inbound inquiries.</p>\n\n"
            "<p>We work with businesses to streamline their online presence and capture high-intent local demand.</p>\n\n"
            "<p>Would you be open to a quick call sometime this week to discuss a few practical recommendations for "
            "{{business_name}}?</p>\n\n"
            "<p>Best regards,<br>Client Strategy Team</p>"
        ),
    },
    {
        "name": "Grade C — Potential Lead",
        "subject": "Quick question regarding {{business_name}}",
        "body": (
            "<p>Hello {{contact_name}},</p>\n\n"
            "<p>I hope your week is going well. We recently conducted a preliminary digital visibility review "
            "for businesses in your sector and identified a few quick areas for improvement for {{business_name}}.</p>\n\n"
            "<p>If you're interested, I'd be happy to share a brief summary of our findings with your team.</p>\n\n"
            "<p>Let me know if you would like me to send that over.</p>\n\n"
            "<p>Best regards,<br>Business Development Team</p>"
        ),
    },
    {
        "name": "Grade D — Low Priority Lead",
        "subject": "Exploring opportunities for {{business_name}}",
        "body": (
            "<p>Hi {{contact_name}},</p>\n\n"
            "<p>I am reaching out to see if {{business_name}} is currently looking for new ways to expand its "
            "customer reach and optimize its digital channels.</p>\n\n"
            "<p>If this is on your roadmap for this quarter, feel free to reply and we can schedule a quick "
            "introductory chat.</p>\n\n"
            "<p>Thanks for your time,<br>Outreach Team</p>"
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
        for tpl in HARDENED_GRADE_TEMPLATES:
            op.execute(
                email_templates_table.update()
                .where(email_templates_table.c.name == tpl["name"])
                .values(
                    subject=tpl["subject"],
                    body=tpl["body"],
                    description=tpl["description"],
                    updated_at=now,
                )
            )
        return

    connection = op.get_bind()
    for tpl in HARDENED_GRADE_TEMPLATES:
        res = connection.execute(
            email_templates_table.update()
            .where(
                sa.and_(
                    email_templates_table.c.is_archived.is_(False),
                    sa.or_(
                        email_templates_table.c.name == tpl["name"],
                        email_templates_table.c.name.ilike(f"{tpl['name'][:7]}%"),
                    ),
                )
            )
            .values(
                subject=tpl["subject"],
                body=tpl["body"],
                description=tpl["description"],
                updated_at=now,
            )
        )
        if res.rowcount == 0:
            op.bulk_insert(
                email_templates_table,
                [
                    {
                        "name": tpl["name"],
                        "description": tpl["description"],
                        "subject": tpl["subject"],
                        "body": tpl["body"],
                        "is_archived": False,
                        "created_at": now,
                        "updated_at": now,
                    }
                ],
            )


def downgrade() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    email_templates_table = sa.table(
        "email_templates",
        sa.column("name", sa.String),
        sa.column("subject", sa.String),
        sa.column("body", sa.Text),
        sa.column("updated_at", sa.DateTime),
    )

    for tpl in PREVIOUS_GRADE_TEMPLATES:
        op.execute(
            email_templates_table.update()
            .where(email_templates_table.c.name == tpl["name"])
            .values(
                subject=tpl["subject"],
                body=tpl["body"],
                updated_at=now,
            )
        )
