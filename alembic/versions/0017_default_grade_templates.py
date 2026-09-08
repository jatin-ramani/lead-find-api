"""seed default grade email templates (Grade A, B, C, D)

Revision ID: 0017
Revises: 0016
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_GRADE_TEMPLATES = [
    {
        "name": "Grade A — High Priority Lead",
        "description": "Default outreach template for Grade A (High Priority) leads.",
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
        "description": "Default outreach template for Grade B (Good) leads.",
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
        "description": "Default outreach template for Grade C (Potential) leads.",
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
        "description": "Default outreach template for Grade D (Low Priority) leads.",
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
    email_templates = sa.table(
        "email_templates",
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("subject", sa.String),
        sa.column("body", sa.Text),
        sa.column("is_archived", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    rows_to_insert = [
        {
            "name": item["name"],
            "description": item["description"],
            "subject": item["subject"],
            "body": item["body"],
            "is_archived": False,
            "created_at": now,
            "updated_at": now,
        }
        for item in DEFAULT_GRADE_TEMPLATES
    ]

    if context.is_offline_mode():
        op.bulk_insert(email_templates, rows_to_insert)
    else:
        bind = op.get_bind()
        if bind is not None:
            for row in rows_to_insert:
                existing = bind.execute(
                    sa.select(email_templates.c.name).where(
                        email_templates.c.name == row["name"]
                    )
                ).first()
                if not existing:
                    bind.execute(email_templates.insert().values(**row))
        else:
            op.bulk_insert(email_templates, rows_to_insert)


def downgrade() -> None:
    email_templates = sa.table(
        "email_templates",
        sa.column("name", sa.String),
    )
    default_names = [item["name"] for item in DEFAULT_GRADE_TEMPLATES]
    if context.is_offline_mode():
        op.execute(
            email_templates.delete().where(
                email_templates.c.name.in_(default_names)
            )
        )
    else:
        bind = op.get_bind()
        if bind is not None:
            bind.execute(
                email_templates.delete().where(
                    email_templates.c.name.in_(default_names)
                )
            )
        else:
            op.execute(
                email_templates.delete().where(
                    email_templates.c.name.in_(default_names)
                )
            )
