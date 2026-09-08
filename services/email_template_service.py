"""
Email Template Service for Lead Finder CRM.

Provides template CRUD, search, archive management, safe deletion checks,
and preview rendering using the allowlisted template engine.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.models import Business, EmailCampaign, EmailTemplate
from services.template_engine import render_template

logger = logging.getLogger(__name__)

# Deterministic default email templates for Lead Grades A, B, C, D
DEFAULT_GRADE_TEMPLATES = [
    {
        "grade": "A",
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
        "grade": "B",
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
        "grade": "C",
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
        "grade": "D",
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


def seed_default_templates(db: Session) -> List[EmailTemplate]:
    """
    Ensure the 4 deterministic default grade templates (Grade A, B, C, D) exist in the database.
    Idempotent:
    - If an active template for a grade already exists, it is preserved without re-creating.
    - Never creates duplicates.
    """
    created: List[EmailTemplate] = []
    now = datetime.now(timezone.utc)

    for item in DEFAULT_GRADE_TEMPLATES:
        grade = item["grade"]
        existing = (
            db.query(EmailTemplate)
            .filter(
                EmailTemplate.is_archived.is_(False),
                or_(
                    EmailTemplate.name == item["name"],
                    EmailTemplate.name.ilike(f"Grade {grade} — %"),
                    EmailTemplate.name.ilike(f"Grade {grade} - %"),
                    EmailTemplate.name.ilike(f"%(Grade {grade})%"),
                ),
            )
            .first()
        )
        if not existing:
            tpl = EmailTemplate(
                name=item["name"],
                description=item["description"],
                subject=item["subject"],
                body=item["body"],
                is_archived=False,
                created_at=now,
                updated_at=now,
            )
            db.add(tpl)
            created.append(tpl)

    if created:
        db.commit()
        for tpl in created:
            db.refresh(tpl)

    return created


def get_default_grade_template(db: Session, grade: str) -> Optional[EmailTemplate]:
    """Fetch the active template associated with a specific lead grade (A, B, C, D)."""
    norm_grade = grade.upper().strip()
    return (
        db.query(EmailTemplate)
        .filter(
            EmailTemplate.is_archived.is_(False),
            or_(
                EmailTemplate.name.ilike(f"Grade {norm_grade} — %"),
                EmailTemplate.name.ilike(f"Grade {norm_grade} - %"),
                EmailTemplate.name.ilike(f"%(Grade {norm_grade})%"),
                EmailTemplate.name.ilike(f"%Grade {norm_grade}%"),
                EmailTemplate.description.ilike(f"%Grade {norm_grade}%"),
            ),
        )
        .order_by(EmailTemplate.id.asc())
        .first()
    )


def create_template(
    db: Session,
    name: str,
    subject: str,
    body: str,
    description: Optional[str] = None,
    commit: bool = True,
) -> EmailTemplate:
    """Create a new reusable email template."""
    cleaned_name = name.strip()
    cleaned_subject = subject.strip()
    cleaned_body = body.strip()
    cleaned_desc = description.strip() if description else None

    now = datetime.now(timezone.utc)
    template = EmailTemplate(
        name=cleaned_name,
        description=cleaned_desc,
        subject=cleaned_subject,
        body=cleaned_body,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(template)
    if commit:
        db.commit()
        db.refresh(template)
    return template


def get_template(db: Session, template_id: int) -> Optional[EmailTemplate]:
    """Fetch an email template by ID."""
    return db.query(EmailTemplate).filter(EmailTemplate.id == template_id).first()


def list_templates(
    db: Session,
    search: Optional[str] = None,
    is_archived: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[EmailTemplate]:
    """List email templates with optional search and archive status filtering."""
    query = db.query(EmailTemplate)

    if is_archived is not None:
        query = query.filter(EmailTemplate.is_archived == is_archived)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                EmailTemplate.name.ilike(term),
                EmailTemplate.subject.ilike(term),
                EmailTemplate.description.ilike(term),
            )
        )

    offset = max(0, (page - 1) * page_size)
    return (
        query.order_by(EmailTemplate.created_at.desc(), EmailTemplate.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )


def count_templates(
    db: Session,
    search: Optional[str] = None,
    is_archived: Optional[bool] = None,
) -> int:
    """Count total templates matching filters."""
    query = db.query(EmailTemplate)

    if is_archived is not None:
        query = query.filter(EmailTemplate.is_archived == is_archived)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                EmailTemplate.name.ilike(term),
                EmailTemplate.subject.ilike(term),
                EmailTemplate.description.ilike(term),
            )
        )

    return query.count()


def update_template(
    db: Session,
    template_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    is_archived: Optional[bool] = None,
    commit: bool = True,
) -> Optional[EmailTemplate]:
    """Update template attributes."""
    template = get_template(db, template_id)
    if not template:
        return None

    changed = False
    if name is not None and template.name != name.strip():
        template.name = name.strip()
        changed = True
    if description is not None:
        cleaned_desc = description.strip() if description else None
        if template.description != cleaned_desc:
            template.description = cleaned_desc
            changed = True
    if subject is not None and template.subject != subject.strip():
        template.subject = subject.strip()
        changed = True
    if body is not None and template.body != body.strip():
        template.body = body.strip()
        changed = True
    if is_archived is not None and template.is_archived != is_archived:
        template.is_archived = is_archived
        changed = True

    if changed:
        template.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(template)

    return template


def toggle_template_archive(
    db: Session,
    template_id: int,
    is_archived: Optional[bool] = None,
    commit: bool = True,
) -> Optional[EmailTemplate]:
    """Toggle or set archive status for a template."""
    template = get_template(db, template_id)
    if not template:
        return None

    target = not template.is_archived if is_archived is None else is_archived
    if template.is_archived != target:
        template.is_archived = target
        template.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(template)

    return template


class TemplateInUseError(Exception):
    """Raised when trying to delete a template referenced by active or historical campaigns."""
    def __init__(self, campaign_count: int):
        self.campaign_count = campaign_count
        super().__init__(f"Cannot delete template because it is referenced by {campaign_count} campaign(s). Please archive it instead.")


def delete_template(
    db: Session,
    template_id: int,
    commit: bool = True,
) -> bool:
    """
    Safely delete an email template.
    If referenced by any campaigns, raises TemplateInUseError to prevent corrupting campaign history.
    """
    template = get_template(db, template_id)
    if not template:
        return False

    referencing_campaigns = (
        db.query(EmailCampaign)
        .filter(EmailCampaign.template_id == template_id)
        .count()
    )
    if referencing_campaigns > 0:
        raise TemplateInUseError(referencing_campaigns)

    db.delete(template)
    if commit:
        db.commit()
    return True


def preview_template_content(
    db: Session,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    template_id: Optional[int] = None,
    business_id: Optional[int] = None,
    custom_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Render subject and body preview with sample or real business data.
    """
    raw_subject = subject or ""
    raw_body = body or ""

    if template_id and (not raw_subject or not raw_body):
        tmpl = get_template(db, template_id)
        if tmpl:
            raw_subject = raw_subject or tmpl.subject
            raw_body = raw_body or tmpl.body

    context: Dict[str, Any] = {
        "business_name": "Apex Dental & Healthcare",
        "contact_name": "Dr. Sarah Smith",
        "email": "contact@apexdental.example",
        "phone": "+1 (555) 234-5678",
        "website": "https://apexdental.example",
        "lead_status": "Interested",
        "lead_score": "85",
        "follow_up_title": "Product demonstration & pricing",
        "follow_up_due_at": "2026-09-15 14:00 UTC",
    }

    if business_id:
        biz = db.query(Business).filter(Business.id == business_id).first()
        if biz:
            context.update({
                "business_name": biz.name or "",
                "contact_name": "",
                "email": biz.email or "",
                "phone": biz.phone or "",
                "website": biz.website or "",
                "lead_status": getattr(biz, "lead_status", "new") or "new",
                "lead_score": str(getattr(biz, "lead_score", 0) or 0),
            })

    if custom_context:
        context.update(custom_context)

    rendered_subject = render_template(raw_subject, context, escape_html=False)
    rendered_body = render_template(raw_body, context, escape_html=True)

    return {
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "context_used": context,
    }
