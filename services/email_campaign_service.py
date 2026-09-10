"""
Email Campaign Service for Lead Finder CRM.

Provides Campaign lifecycle management (draft, scheduled, running, completed, cancelled, failed),
canonical lead recipient filtering and snapshot materialization, batch execution,
per-recipient error isolation, and activity history logging.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.crud import _apply_business_filters, HAS_EMAIL
from database.models import Business, EmailCampaign, EmailCampaignRecipient, EmailTemplate
from providers.email_provider import get_email_provider
from services.activity_service import (
    ACTIVITY_EMAIL_CAMPAIGN_CANCELLED,
    ACTIVITY_EMAIL_CAMPAIGN_COMPLETED,
    ACTIVITY_EMAIL_CAMPAIGN_CREATED,
    ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_FAILED,
    ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_SENT,
    ACTIVITY_EMAIL_CAMPAIGN_SCHEDULED,
    ACTIVITY_EMAIL_CAMPAIGN_STARTED,
    create_activity,
)
from services.email_template_service import get_template
from services.template_engine import ensure_html_email, html_to_plain_text, render_template


logger = logging.getLogger(__name__)

# Campaign Statuses
STATUS_DRAFT = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

VALID_CAMPAIGN_STATUSES = {
    STATUS_DRAFT,
    STATUS_SCHEDULED,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
}

# Recipient Execution Statuses
RECIPIENT_PENDING = "pending"
RECIPIENT_PROCESSING = "processing"
RECIPIENT_SENT = "sent"
RECIPIENT_FAILED = "failed"
RECIPIENT_CANCELLED = "cancelled"


class CampaignStateError(Exception):
    """Raised when an illegal campaign state transition is attempted."""
    pass


def _serialize_filters(filters: Optional[Dict[str, Any]]) -> str:
    """Normalize and JSON serialize filter criteria."""
    if not filters:
        return "{}"
    return json.dumps(filters, sort_keys=True, ensure_ascii=False)


def _deserialize_filters(filters_json: Optional[str]) -> Dict[str, Any]:
    """Parse filter criteria JSON into dict."""
    if not filters_json:
        return {}
    try:
        return json.loads(filters_json)
    except Exception:
        return {}


# ============================================================================
# Campaign CRUD
# ============================================================================

def create_campaign(
    db: Session,
    name: str,
    template_id: int,
    description: Optional[str] = None,
    filter_criteria: Optional[Dict[str, Any]] = None,
    scheduled_at: Optional[datetime] = None,
    commit: bool = True,
) -> EmailCampaign:
    """Create a new email campaign."""
    template = get_template(db, template_id)
    if not template:
        raise ValueError(f"EmailTemplate with ID {template_id} does not exist.")
    if template.is_archived:
        raise ValueError("Cannot create campaign with an archived template.")

    cleaned_name = name.strip()
    cleaned_desc = description.strip() if description else None
    filters_json = _serialize_filters(filter_criteria)

    status = STATUS_SCHEDULED if scheduled_at else STATUS_DRAFT
    now = datetime.now(timezone.utc)

    campaign = EmailCampaign(
        name=cleaned_name,
        description=cleaned_desc,
        template_id=template_id,
        status=status,
        filter_criteria_json=filters_json,
        recipient_count=0,
        sent_count=0,
        failed_count=0,
        scheduled_at=scheduled_at,
        started_at=None,
        completed_at=None,
        snapshot_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(campaign)
    db.flush()

    if commit:
        db.commit()
        db.refresh(campaign)

    return campaign


def get_campaign(db: Session, campaign_id: int) -> Optional[EmailCampaign]:
    """Fetch campaign by ID."""
    return db.query(EmailCampaign).filter(EmailCampaign.id == campaign_id).first()


def list_campaigns(
    db: Session,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[EmailCampaign]:
    """List paginated campaigns with optional status and search filtering."""
    query = db.query(EmailCampaign)

    if status:
        query = query.filter(EmailCampaign.status == status.lower())

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                EmailCampaign.name.ilike(term),
                EmailCampaign.description.ilike(term),
            )
        )

    offset = max(0, (page - 1) * page_size)
    return (
        query.order_by(EmailCampaign.created_at.desc(), EmailCampaign.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )


def count_campaigns(
    db: Session,
    status: Optional[str] = None,
    search: Optional[str] = None,
) -> int:
    """Count total campaigns matching filters."""
    query = db.query(EmailCampaign)

    if status:
        query = query.filter(EmailCampaign.status == status.lower())

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                EmailCampaign.name.ilike(term),
                EmailCampaign.description.ilike(term),
            )
        )

    return query.count()


def update_campaign(
    db: Session,
    campaign_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    template_id: Optional[int] = None,
    filter_criteria: Optional[Dict[str, Any]] = None,
    scheduled_at: Optional[datetime] = None,
    commit: bool = True,
) -> Optional[EmailCampaign]:
    """Update campaign metadata before execution."""
    campaign = get_campaign(db, campaign_id)
    if not campaign:
        return None

    if campaign.status in (STATUS_RUNNING, STATUS_COMPLETED, STATUS_CANCELLED):
        raise CampaignStateError(f"Cannot edit campaign in '{campaign.status}' state.")

    changed = False
    if name is not None and campaign.name != name.strip():
        campaign.name = name.strip()
        changed = True
    if description is not None:
        cleaned_desc = description.strip() if description else None
        if campaign.description != cleaned_desc:
            campaign.description = cleaned_desc
            changed = True
    if template_id is not None and campaign.template_id != template_id:
        tmpl = get_template(db, template_id)
        if not tmpl:
            raise ValueError(f"EmailTemplate with ID {template_id} not found.")
        if tmpl.is_archived:
            raise ValueError("Cannot assign an archived template.")
        campaign.template_id = template_id
        changed = True
    if filter_criteria is not None:
        new_filters_json = _serialize_filters(filter_criteria)
        if campaign.filter_criteria_json != new_filters_json:
            campaign.filter_criteria_json = new_filters_json
            # Clear old materialized recipients if filters changed in draft/scheduled
            if campaign.status in (STATUS_DRAFT, STATUS_SCHEDULED):
                db.query(EmailCampaignRecipient).filter(
                    EmailCampaignRecipient.campaign_id == campaign.id
                ).delete(synchronize_session=False)
                campaign.recipient_count = 0
                campaign.snapshot_at = None
            changed = True
    if scheduled_at is not None:
        campaign.scheduled_at = scheduled_at
        campaign.status = STATUS_SCHEDULED
        changed = True

    if changed:
        campaign.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(campaign)

    return campaign


def delete_campaign(
    db: Session,
    campaign_id: int,
    commit: bool = True,
) -> bool:
    """Delete a campaign and cascade its recipient logs."""
    campaign = get_campaign(db, campaign_id)
    if not campaign:
        return False

    if campaign.status == STATUS_RUNNING:
        raise CampaignStateError("Cannot delete an active running campaign. Cancel it first.")

    db.delete(campaign)
    if commit:
        db.commit()
    return True


def cancel_campaign(
    db: Session,
    campaign_id: int,
    commit: bool = True,
) -> Optional[EmailCampaign]:
    """Cancel a scheduled or running campaign and cancel all remaining pending recipients."""
    campaign = get_campaign(db, campaign_id)
    if not campaign:
        return None

    if campaign.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
        raise CampaignStateError(f"Campaign is already {campaign.status}.")

    now = datetime.now(timezone.utc)
    campaign.status = STATUS_CANCELLED
    campaign.completed_at = now
    campaign.updated_at = now

    # Cancel pending recipients
    db.query(EmailCampaignRecipient).filter(
        EmailCampaignRecipient.campaign_id == campaign_id,
        EmailCampaignRecipient.status.in_([RECIPIENT_PENDING, RECIPIENT_PROCESSING]),
    ).update(
        {"status": RECIPIENT_CANCELLED, "updated_at": now},
        synchronize_session=False,
    )

    if commit:
        db.commit()
        db.refresh(campaign)

    return campaign


# ============================================================================
# Recipient Snapshot & Canonical Filtering
# ============================================================================

def _build_filtered_leads_query(db: Session, filter_criteria: Dict[str, Any]):
    """Apply canonical business filters and enforce non-empty email."""
    base_query = db.query(Business).filter(HAS_EMAIL)
    return _apply_business_filters(
        base_query,
        search=filter_criteria.get("search"),
        city=filter_criteria.get("city"),
        category=filter_criteria.get("category"),
        has_website=filter_criteria.get("has_website"),
        has_email=True,  # Mandatory for email campaigns
        has_phone=filter_criteria.get("has_phone"),
        lead_grade=filter_criteria.get("lead_grade"),
        min_lead_score=filter_criteria.get("min_lead_score"),
        max_lead_score=filter_criteria.get("max_lead_score"),
        tags=filter_criteria.get("tags"),
        is_favorite=filter_criteria.get("is_favorite"),
        lead_status=filter_criteria.get("lead_status"),
        business_ids=filter_criteria.get("business_ids"),
    )


def preview_eligible_recipients(
    db: Session,
    filter_criteria: Dict[str, Any],
    limit_samples: int = 10,
) -> Dict[str, Any]:
    """Count eligible businesses matching filter criteria and return sample preview."""
    query = _build_filtered_leads_query(db, filter_criteria)
    total_count = query.count()
    sample_leads = (
        query.order_by(Business.id.asc())
        .limit(limit_samples)
        .all()
    )

    return {
        "total_eligible_leads": total_count,
        "sample_leads": [
            {
                "id": b.id,
                "name": b.name,
                "email": b.email,
                "city": b.city,
                "category": b.category,
                "lead_grade": b.lead_grade,
                "lead_score": b.lead_score,
                "lead_status": b.lead_status,
            }
            for b in sample_leads
        ],
    }


def materialize_recipients(
    db: Session,
    campaign_id: int,
    commit: bool = True,
) -> int:
    """
    Snapshot eligible leads into `email_campaign_recipients`.
    Deduplicates recipients and enforces valid non-empty emails.
    """
    campaign = get_campaign(db, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign {campaign_id} not found.")

    filters = _deserialize_filters(campaign.filter_criteria_json)
    query = _build_filtered_leads_query(db, filters).order_by(Business.id.asc())

    leads = query.all()
    now = datetime.now(timezone.utc)
    inserted_count = 0
    seen_biz_ids = set()

    for lead in leads:
        if not lead.email or not lead.email.strip():
            continue
        if lead.id in seen_biz_ids:
            continue
        seen_biz_ids.add(lead.id)

        try:
            with db.begin_nested():
                recipient = EmailCampaignRecipient(
                    campaign_id=campaign_id,
                    business_id=lead.id,
                    recipient_email=lead.email.strip(),
                    recipient_name=lead.name or "",
                    status=RECIPIENT_PENDING,
                    attempt_count=0,
                    error_message=None,
                    provider_message_id=None,
                    created_at=now,
                    updated_at=now,
                )
                db.add(recipient)
                db.flush()
                inserted_count += 1
        except IntegrityError:
            # Duplicate recipient row caught safely
            continue

    campaign.recipient_count = inserted_count
    campaign.snapshot_at = now
    campaign.updated_at = now

    if commit:
        db.commit()
        db.refresh(campaign)

    return inserted_count


# ============================================================================
# Campaign Execution & Batch Queue Processing
# ============================================================================

def start_campaign(
    db: Session,
    campaign_id: int,
    batch_size: int = 50,
    commit: bool = True,
) -> EmailCampaign:
    """
    Transition a campaign into `running` state, materialize recipients if not yet done,
    and process the initial batch of recipients.
    """
    campaign = get_campaign(db, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign {campaign_id} not found.")

    if campaign.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
        raise CampaignStateError(f"Cannot start campaign in '{campaign.status}' state.")

    now = datetime.now(timezone.utc)

    # Materialize snapshot if needed
    if not campaign.snapshot_at or campaign.recipient_count == 0:
        materialize_recipients(db, campaign_id, commit=False)

    if campaign.recipient_count == 0:
        # No recipients found for this criteria
        campaign.status = STATUS_COMPLETED
        campaign.completed_at = now
        campaign.updated_at = now
        if commit:
            db.commit()
            db.refresh(campaign)
        return campaign

    campaign.status = STATUS_RUNNING
    campaign.started_at = campaign.started_at or now
    campaign.updated_at = now
    db.flush()

    # Execute first batch
    execute_campaign_batch(db, campaign_id=campaign_id, batch_size=batch_size, commit=False)

    if commit:
        db.commit()
        db.refresh(campaign)

    return campaign


def execute_campaign_batch(
    db: Session,
    campaign_id: int,
    batch_size: int = 50,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    Execute a batch of pending recipients for a campaign.
    Isolates individual email errors, renders templates safely, and updates campaign statistics.
    """
    campaign = get_campaign(db, campaign_id)
    if not campaign or campaign.status != STATUS_RUNNING:
        return {"sent": 0, "failed": 0, "remaining": 0}

    template = get_template(db, campaign.template_id)
    if not template:
        logger.error("Campaign %d references missing template %d", campaign_id, campaign.template_id)
        campaign.status = STATUS_FAILED
        campaign.completed_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
        return {"sent": 0, "failed": 0, "remaining": 0}

    provider = get_email_provider()
    now_utc = datetime.now(timezone.utc)

    # Fetch pending recipients
    pending_recipients = (
        db.query(EmailCampaignRecipient)
        .filter(
            EmailCampaignRecipient.campaign_id == campaign_id,
            EmailCampaignRecipient.status == RECIPIENT_PENDING,
        )
        .order_by(EmailCampaignRecipient.id.asc())
        .limit(batch_size)
        .all()
    )

    batch_sent = 0
    batch_failed = 0

    for recip in pending_recipients:
        recip.status = RECIPIENT_PROCESSING
        recip.attempted_at = datetime.now(timezone.utc)
        recip.attempt_count += 1
        db.flush()

        biz = db.query(Business).filter(Business.id == recip.business_id).first()
        if not biz:
            recip.status = RECIPIENT_FAILED
            recip.error_message = "Associated business record not found."
            recip.updated_at = datetime.now(timezone.utc)
            campaign.failed_count += 1
            batch_failed += 1
            continue

        # Assemble template context
        biz_name = (biz.name or "").strip()
        raw_contact = (recip.recipient_name or "").strip()
        if raw_contact and raw_contact.lower() != biz_name.lower():
            contact_name = raw_contact
        else:
            contact_name = f"{biz_name} team" if biz_name else "team"

        context = {
            "business_name": biz_name,
            "contact_name": contact_name,
            "city": (biz.city or "").strip(),
            "email": recip.recipient_email or "",
            "phone": biz.phone or "",
            "website": biz.website or "",
            "lead_status": getattr(biz, "lead_status", "new") or "new",
            "lead_score": str(getattr(biz, "lead_score", 0) or 0),
            "follow_up_title": "",
            "follow_up_due_at": "",
        }

        rendered_subject = render_template(template.subject, context, escape_html=False)
        body_to_render = ensure_html_email(template.body)
        rendered_body = render_template(body_to_render, context, escape_html=True)
        plain_body = html_to_plain_text(rendered_body)

        try:
            send_result = provider.send_email(
                to_email=recip.recipient_email,
                subject=rendered_subject,
                html_content=rendered_body,
                text_content=plain_body,
                metadata={
                    "db": db,
                    "campaign_id": campaign.id,
                    "recipient_id": recip.id,
                    "business_id": recip.business_id,
                },
            )

        except Exception as exc:
            logger.exception("Campaign recipient dispatch exception for recip ID %d", recip.id)
            from providers.email_provider import EmailSendResult
            send_result = EmailSendResult(
                success=False,
                error=f"Unexpected dispatch error: {str(exc)[:200]}",
            )

        if send_result.success:
            recip.status = RECIPIENT_SENT
            recip.sent_at = datetime.now(timezone.utc)
            recip.provider_message_id = send_result.message_id
            recip.error_message = None
            recip.updated_at = datetime.now(timezone.utc)
            campaign.sent_count += 1
            batch_sent += 1

            # Log activity
            meta = {
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "recipient_id": recip.id,
                "recipient_email": recip.recipient_email,
                "provider_message_id": send_result.message_id,
                "subject": rendered_subject,
            }
            create_activity(
                db=db,
                business_id=recip.business_id,
                activity_type=ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_SENT,
                title=f"Campaign email sent: {campaign.name}",
                description=f"Delivered subject '{rendered_subject}' to {recip.recipient_email}",
                metadata=meta,
                commit=False,
            )
        else:
            if send_result.error and "Daily sending limit reached" in send_result.error:
                # Quota reached: restore recipient to pending and halt remaining batch
                recip.status = RECIPIENT_PENDING
                recip.attempt_count = max(0, recip.attempt_count - 1)
                recip.error_message = send_result.error
                logger.warning("Halting campaign %d execution: Daily sending limit reached.", campaign.id)
                break

            recip.status = RECIPIENT_FAILED
            recip.error_message = send_result.error
            recip.updated_at = datetime.now(timezone.utc)
            campaign.failed_count += 1
            batch_failed += 1


            # Log activity
            meta = {
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "recipient_id": recip.id,
                "recipient_email": recip.recipient_email,
                "error": send_result.error,
                "subject": rendered_subject,
            }
            create_activity(
                db=db,
                business_id=recip.business_id,
                activity_type=ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_FAILED,
                title=f"Campaign email failed: {campaign.name}",
                description=f"Failed sending to {recip.recipient_email}: {send_result.error}",
                metadata=meta,
                commit=False,
            )

    # Check if more recipients are pending
    remaining_pending = (
        db.query(EmailCampaignRecipient)
        .filter(
            EmailCampaignRecipient.campaign_id == campaign_id,
            EmailCampaignRecipient.status == RECIPIENT_PENDING,
        )
        .count()
    )

    if remaining_pending == 0:
        # All recipients processed!
        campaign.status = STATUS_COMPLETED if campaign.sent_count > 0 or campaign.recipient_count == 0 else STATUS_FAILED
        campaign.completed_at = datetime.now(timezone.utc)
        campaign.updated_at = datetime.now(timezone.utc)

    if commit:
        db.commit()
        db.refresh(campaign)

    return {
        "sent": batch_sent,
        "failed": batch_failed,
        "remaining": remaining_pending,
    }


def process_due_campaigns(
    db: Session,
    limit: int = 10,
    batch_size: int = 50,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    Background worker entry point to process scheduled campaigns and active running campaigns.
    """
    now_utc = datetime.now(timezone.utc)

    # 1. Start due scheduled campaigns
    due_campaigns = (
        db.query(EmailCampaign)
        .filter(
            EmailCampaign.status == STATUS_SCHEDULED,
            EmailCampaign.scheduled_at <= now_utc,
        )
        .order_by(EmailCampaign.scheduled_at.asc())
        .limit(limit)
        .all()
    )

    campaigns_processed = 0
    total_sent = 0
    total_failed = 0
    campaigns_completed = 0

    for camp in due_campaigns:
        campaigns_processed += 1
        # Snapshot and transition to running
        if not camp.snapshot_at or camp.recipient_count == 0:
            materialize_recipients(db, camp.id, commit=False)

        if camp.recipient_count == 0:
            camp.status = STATUS_COMPLETED
            camp.completed_at = now_utc
            camp.updated_at = now_utc
            campaigns_completed += 1
        else:
            camp.status = STATUS_RUNNING
            camp.started_at = camp.started_at or now_utc
            camp.updated_at = now_utc
            db.flush()
            res = execute_campaign_batch(db, campaign_id=camp.id, batch_size=batch_size, commit=False)
            total_sent += res.get("sent", 0)
            total_failed += res.get("failed", 0)
            if camp.status == STATUS_COMPLETED:
                campaigns_completed += 1

    # 2. Advance running campaigns
    running_campaigns = (
        db.query(EmailCampaign)
        .filter(EmailCampaign.status == STATUS_RUNNING)
        .filter(~EmailCampaign.id.in_([c.id for c in due_campaigns]) if due_campaigns else True)
        .order_by(EmailCampaign.started_at.asc())
        .limit(limit)
        .all()
    )

    for camp in running_campaigns:
        res = execute_campaign_batch(db, campaign_id=camp.id, batch_size=batch_size, commit=False)
        total_sent += res.get("sent", 0)
        total_failed += res.get("failed", 0)
        if camp.status == STATUS_COMPLETED:
            campaigns_completed += 1

    if commit:
        db.commit()

    return {
        "campaigns_processed": campaigns_processed,
        "recipients_sent": total_sent,
        "recipients_failed": total_failed,
        "campaigns_completed": campaigns_completed,
    }


def list_campaign_recipients(
    db: Session,
    campaign_id: int,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[EmailCampaignRecipient]:
    """List execution records for recipients of a campaign."""
    query = db.query(EmailCampaignRecipient).filter(EmailCampaignRecipient.campaign_id == campaign_id)

    if status:
        query = query.filter(EmailCampaignRecipient.status == status.lower())

    offset = max(0, (page - 1) * page_size)
    return (
        query.order_by(EmailCampaignRecipient.id.asc())
        .offset(offset)
        .limit(page_size)
        .all()
    )


def count_campaign_recipients(
    db: Session,
    campaign_id: int,
    status: Optional[str] = None,
) -> int:
    """Count recipient records matching status filter."""
    query = db.query(EmailCampaignRecipient).filter(EmailCampaignRecipient.campaign_id == campaign_id)

    if status:
        query = query.filter(EmailCampaignRecipient.status == status.lower())

    return query.count()
