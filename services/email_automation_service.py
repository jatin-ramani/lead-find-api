"""
Email Automation Service for Lead Finder.

Manages automated email rule configuration, event-driven trigger evaluation,
idempotency keys, scheduled batch execution, retry backoff, and activity history logging.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import random
from typing import Any, Dict, List, Optional
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Business, BusinessActivity, BusinessFollowUp, EmailAutomation, EmailAutomationExecution
from providers.email_provider import get_email_provider
from services.activity_service import (
    ACTIVITY_EMAIL_AUTOMATION_TRIGGERED,
    ACTIVITY_EMAIL_FAILED,
    ACTIVITY_EMAIL_SCHEDULED,
    ACTIVITY_EMAIL_SENT,
    create_activity,
)
from services.template_engine import render_template

logger = logging.getLogger(__name__)

# Trigger Types Vocabulary
TRIGGER_LEAD_CREATED = "lead_created"
TRIGGER_LEAD_STATUS_CHANGED = "lead_status_changed"
TRIGGER_FOLLOW_UP_DUE = "follow_up_due"
TRIGGER_FOLLOW_UP_OVERDUE = "follow_up_overdue"

VALID_TRIGGER_TYPES = {
    TRIGGER_LEAD_CREATED,
    TRIGGER_LEAD_STATUS_CHANGED,
    TRIGGER_FOLLOW_UP_DUE,
    TRIGGER_FOLLOW_UP_OVERDUE,
}

# Execution Statuses Vocabulary
EXEC_STATUS_SCHEDULED = "scheduled"
EXEC_STATUS_PROCESSING = "processing"
EXEC_STATUS_SENT = "sent"
EXEC_STATUS_FAILED = "failed"
EXEC_STATUS_CANCELLED = "cancelled"

VALID_EXECUTION_STATUSES = {
    EXEC_STATUS_SCHEDULED,
    EXEC_STATUS_PROCESSING,
    EXEC_STATUS_SENT,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_CANCELLED,
}


# ============================================================================
# Automation CRUD
# ============================================================================

def create_automation(
    db: Session,
    name: str,
    trigger_type: str,
    subject_template: str,
    body_template: str,
    description: Optional[str] = None,
    enabled: bool = True,
    delay_minutes: int = 0,
    max_retries: int = 3,
    commit: bool = True,
) -> EmailAutomation:
    """Create a new email automation rule."""
    cleaned_name = name.strip()
    cleaned_trigger = trigger_type.strip().lower()
    cleaned_subject = subject_template.strip()
    cleaned_body = body_template.strip()
    cleaned_desc = description.strip() if description else None

    now = datetime.now(timezone.utc)
    automation = EmailAutomation(
        name=cleaned_name,
        description=cleaned_desc,
        enabled=enabled,
        trigger_type=cleaned_trigger,
        subject_template=cleaned_subject,
        body_template=cleaned_body,
        delay_minutes=max(0, delay_minutes),
        max_retries=max(0, min(10, max_retries)),
        created_at=now,
        updated_at=now,
    )
    db.add(automation)
    if commit:
        db.commit()
        db.refresh(automation)
    return automation


def get_automation(db: Session, automation_id: int) -> Optional[EmailAutomation]:
    """Fetch an automation rule by primary ID."""
    return db.query(EmailAutomation).filter(EmailAutomation.id == automation_id).first()


def list_automations(
    db: Session,
    trigger_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[EmailAutomation]:
    """List paginated automations with optional filtering."""
    query = db.query(EmailAutomation)
    if trigger_type:
        query = query.filter(EmailAutomation.trigger_type == trigger_type)
    if enabled is not None:
        query = query.filter(EmailAutomation.enabled == enabled)

    offset = max(0, (page - 1) * page_size)
    return query.order_by(EmailAutomation.created_at.desc(), EmailAutomation.id.desc()).offset(offset).limit(page_size).all()


def count_automations(
    db: Session,
    trigger_type: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> int:
    """Count total automations matching filters."""
    query = db.query(EmailAutomation)
    if trigger_type:
        query = query.filter(EmailAutomation.trigger_type == trigger_type)
    if enabled is not None:
        query = query.filter(EmailAutomation.enabled == enabled)
    return query.count()


def update_automation(
    db: Session,
    automation_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    enabled: Optional[bool] = None,
    trigger_type: Optional[str] = None,
    subject_template: Optional[str] = None,
    body_template: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    max_retries: Optional[int] = None,
    commit: bool = True,
) -> Optional[EmailAutomation]:
    """Update automation attributes."""
    automation = get_automation(db, automation_id)
    if not automation:
        return None

    changed = False
    if name is not None and automation.name != name.strip():
        automation.name = name.strip()
        changed = True
    if description is not None:
        cleaned_desc = description.strip() if description else None
        if automation.description != cleaned_desc:
            automation.description = cleaned_desc
            changed = True
    if enabled is not None and automation.enabled != enabled:
        automation.enabled = enabled
        changed = True
    if trigger_type is not None and trigger_type in VALID_TRIGGER_TYPES and automation.trigger_type != trigger_type:
        automation.trigger_type = trigger_type
        changed = True
    if subject_template is not None and automation.subject_template != subject_template.strip():
        automation.subject_template = subject_template.strip()
        changed = True
    if body_template is not None and automation.body_template != body_template.strip():
        automation.body_template = body_template.strip()
        changed = True
    if delay_minutes is not None and automation.delay_minutes != delay_minutes:
        automation.delay_minutes = max(0, delay_minutes)
        changed = True
    if max_retries is not None and automation.max_retries != max_retries:
        automation.max_retries = max(0, min(10, max_retries))
        changed = True

    if changed:
        automation.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(automation)

    return automation


def toggle_automation_enabled(
    db: Session,
    automation_id: int,
    enabled: Optional[bool] = None,
    commit: bool = True,
) -> Optional[EmailAutomation]:
    """Toggle or set the enabled flag of an automation."""
    automation = get_automation(db, automation_id)
    if not automation:
        return None

    target_enabled = not automation.enabled if enabled is None else enabled
    if automation.enabled != target_enabled:
        automation.enabled = target_enabled
        automation.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(automation)

    return automation


def delete_automation(
    db: Session,
    automation_id: int,
    commit: bool = True,
) -> bool:
    """Delete an automation rule and cascade its executions."""
    automation = get_automation(db, automation_id)
    if not automation:
        return False

    db.delete(automation)
    if commit:
        db.commit()
    return True


# ============================================================================
# Executions / Logs Queries
# ============================================================================

def list_executions(
    db: Session,
    automation_id: Optional[int] = None,
    business_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[EmailAutomationExecution]:
    """Query execution records with filtering and pagination."""
    query = db.query(EmailAutomationExecution)
    if automation_id is not None:
        query = query.filter(EmailAutomationExecution.automation_id == automation_id)
    if business_id is not None:
        query = query.filter(EmailAutomationExecution.business_id == business_id)
    if status:
        query = query.filter(EmailAutomationExecution.status == status)

    offset = max(0, (page - 1) * page_size)
    return query.order_by(
        EmailAutomationExecution.created_at.desc(),
        EmailAutomationExecution.id.desc(),
    ).offset(offset).limit(page_size).all()


def count_executions(
    db: Session,
    automation_id: Optional[int] = None,
    business_id: Optional[int] = None,
    status: Optional[str] = None,
) -> int:
    """Count executions matching filters."""
    query = db.query(EmailAutomationExecution)
    if automation_id is not None:
        query = query.filter(EmailAutomationExecution.automation_id == automation_id)
    if business_id is not None:
        query = query.filter(EmailAutomationExecution.business_id == business_id)
    if status:
        query = query.filter(EmailAutomationExecution.status == status)
    return query.count()


def get_execution(db: Session, execution_id: int) -> Optional[EmailAutomationExecution]:
    """Fetch single execution record by ID."""
    return db.query(EmailAutomationExecution).filter(EmailAutomationExecution.id == execution_id).first()


# ============================================================================
# Trigger Evaluation & Scheduling
# ============================================================================

def build_template_context(
    business: Business,
    follow_up: Optional[BusinessFollowUp] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble dictionary for safe template variable rendering."""
    ctx = {
        "business_name": business.name or "",
        "contact_name": "",
        "email": business.email or "",
        "phone": business.phone or "",
        "website": business.website or "",
        "lead_status": getattr(business, "lead_status", "new") or "new",
        "lead_score": getattr(business, "lead_score", 0) or 0,
        "follow_up_title": follow_up.title if follow_up else "",
        "follow_up_due_at": follow_up.due_at.strftime("%Y-%m-%d %H:%M UTC") if follow_up and follow_up.due_at else "",
    }
    if extra_context:
        ctx.update(extra_context)
    return ctx


def generate_trigger_key(
    automation_id: int,
    trigger_type: str,
    business_id: int,
    follow_up_id: Optional[int] = None,
    event_discriminator: Optional[str] = None,
) -> str:
    """
    Generate deterministic idempotency key for an automation trigger.
    Prevents duplicate executions for the same event and lead.
    """
    if trigger_type in (TRIGGER_FOLLOW_UP_DUE, TRIGGER_FOLLOW_UP_OVERDUE) and follow_up_id:
        raw_key = f"auto_{automation_id}_fu_{follow_up_id}_{trigger_type}"
    elif trigger_type == TRIGGER_LEAD_STATUS_CHANGED:
        disc = event_discriminator or "status"
        raw_key = f"auto_{automation_id}_biz_{business_id}_status_{disc}"
    else:
        raw_key = f"auto_{automation_id}_biz_{business_id}_{trigger_type}"

    # Return clean key under 255 chars
    if len(raw_key) > 200:
        hash_digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        return f"{raw_key[:180]}_{hash_digest}"
    return raw_key


def evaluate_automations_for_event(
    db: Session,
    trigger_type: str,
    business_id: int,
    follow_up_id: Optional[int] = None,
    event_discriminator: Optional[str] = None,
    extra_context: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> List[EmailAutomationExecution]:
    """
    Evaluate all active automations matching the trigger_type for a business.
    Schedules executions idempotently.
    """
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        return []

    # Business must have a contact email to send
    if not business.email or not business.email.strip():
        logger.info("Skipping email automation for business %d: No email address", business_id)
        return []

    follow_up = None
    if follow_up_id:
        follow_up = db.query(BusinessFollowUp).filter(BusinessFollowUp.id == follow_up_id).first()
        # If follow-up trigger, completed/cancelled tasks should not trigger
        if follow_up and follow_up.status != "pending":
            logger.info("Skipping follow-up automation for follow_up %d: status is %s", follow_up_id, follow_up.status)
            return []

    # Find active automations for trigger
    automations = db.query(EmailAutomation).filter(
        EmailAutomation.enabled.is_(True),
        EmailAutomation.trigger_type == trigger_type,
    ).all()

    if not automations:
        return []

    context = build_template_context(business, follow_up, extra_context)
    now_utc = datetime.now(timezone.utc)
    scheduled_executions: List[EmailAutomationExecution] = []

    for auto in automations:
        trigger_key = generate_trigger_key(
            automation_id=auto.id,
            trigger_type=trigger_type,
            business_id=business_id,
            follow_up_id=follow_up_id,
            event_discriminator=event_discriminator,
        )

        # Idempotency check: check if already generated
        existing = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.trigger_key == trigger_key
        ).first()

        if existing:
            logger.info("Automation %d already executed/scheduled for key %s", auto.id, trigger_key)
            continue

        rendered_subject = render_template(auto.subject_template, context, escape_html=False)
        rendered_body = render_template(auto.body_template, context, escape_html=True)
        scheduled_at = now_utc + timedelta(minutes=auto.delay_minutes)

        try:
            with db.begin_nested():
                execution = EmailAutomationExecution(
                    automation_id=auto.id,
                    business_id=business_id,
                    follow_up_id=follow_up_id,
                    trigger_event=trigger_type,
                    trigger_key=trigger_key,
                    status=EXEC_STATUS_SCHEDULED,
                    recipient_email=business.email.strip(),
                    subject=rendered_subject,
                    body_rendered=rendered_body,
                    scheduled_at=scheduled_at,
                    attempted_at=None,
                    sent_at=None,
                    retry_count=0,
                    error_message=None,
                    provider_message_id=None,
                    created_at=now_utc,
                    updated_at=now_utc,
                )
                db.add(execution)
                db.flush()

                # Record activity
                meta = {
                    "automation_id": auto.id,
                    "automation_name": auto.name,
                    "execution_id": execution.id,
                    "recipient_email": execution.recipient_email,
                    "scheduled_at": scheduled_at.isoformat(),
                    "trigger_type": trigger_type,
                }
                create_activity(
                    db=db,
                    business_id=business_id,
                    activity_type=ACTIVITY_EMAIL_SCHEDULED,
                    title=f"Email scheduled: {auto.name}",
                    description=f"Recipient: {execution.recipient_email} | Scheduled for: {scheduled_at.strftime('%Y-%m-%d %H:%M UTC')}",
                    metadata=meta,
                    commit=False,
                )
                scheduled_executions.append(execution)
        except IntegrityError:
            logger.info("Concurrent trigger duplicate ignored for key %s", trigger_key)
            continue

    if scheduled_executions and commit:
        db.commit()
        for e in scheduled_executions:
            db.refresh(e)

    return scheduled_executions


# ============================================================================
# Execution Batch Processing
# ============================================================================

def process_due_executions(
    db: Session,
    limit: int = 50,
    batch_size: Optional[int] = None,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    Query scheduled executions where scheduled_at <= now,
    and send emails via the configured email provider.
    Handles stale processing recovery, retries, transient backoff, and activity logging.
    """
    effective_limit = batch_size if batch_size is not None else limit
    now_utc = datetime.now(timezone.utc)
    provider = get_email_provider()

    # Recover any stale processing jobs (e.g. previous worker crashed mid-send > 10m ago)
    stale_cutoff = now_utc - timedelta(minutes=10)
    stale_jobs = (
        db.query(EmailAutomationExecution)
        .filter(
            EmailAutomationExecution.status == EXEC_STATUS_PROCESSING,
            EmailAutomationExecution.attempted_at <= stale_cutoff,
        )
        .all()
    )
    for stale in stale_jobs:
        logger.warning("Recovering stale processing execution %d (attempted %s) -> resetting to scheduled", stale.id, stale.attempted_at)
        stale.status = EXEC_STATUS_SCHEDULED
        stale.updated_at = now_utc
    if stale_jobs:
        db.flush()

    due_executions = (
        db.query(EmailAutomationExecution)
        .filter(
            EmailAutomationExecution.status == EXEC_STATUS_SCHEDULED,
            EmailAutomationExecution.scheduled_at <= now_utc,
        )
        .order_by(EmailAutomationExecution.scheduled_at.asc())
        .limit(effective_limit)
        .all()
    )

    processed_count = 0
    sent_count = 0
    failed_count = 0
    retried_count = 0

    for exec_record in due_executions:
        processed_count += 1
        exec_record.status = EXEC_STATUS_PROCESSING
        exec_record.attempted_at = datetime.now(timezone.utc)
        db.flush()

        try:
            # Send via provider
            send_result = provider.send_email(
                to_email=exec_record.recipient_email,
                subject=exec_record.subject,
                html_content=exec_record.body_rendered,
                text_content=exec_record.body_rendered,
                metadata={
                    "automation_id": exec_record.automation_id,
                    "business_id": exec_record.business_id,
                    "execution_id": exec_record.id,
                },
            )
        except Exception as exc:
            logger.exception("Unexpected exception in provider send_email for execution %d", exec_record.id)
            from providers.email_provider import EmailSendResult
            send_result = EmailSendResult(
                success=False,
                error=f"Unexpected error: {str(exc)[:200]}",
                is_transient=True,
            )

        automation = db.query(EmailAutomation).filter(EmailAutomation.id == exec_record.automation_id).first()
        max_retries = automation.max_retries if automation else 3

        if send_result.success:
            sent_count += 1
            exec_record.status = EXEC_STATUS_SENT
            exec_record.sent_at = datetime.now(timezone.utc)
            exec_record.provider_message_id = send_result.message_id
            exec_record.error_message = None
            exec_record.updated_at = datetime.now(timezone.utc)

            # Record email_sent activity
            meta = {
                "automation_id": exec_record.automation_id,
                "execution_id": exec_record.id,
                "recipient_email": exec_record.recipient_email,
                "provider_message_id": send_result.message_id,
                "subject": exec_record.subject,
            }
            create_activity(
                db=db,
                business_id=exec_record.business_id,
                activity_type=ACTIVITY_EMAIL_SENT,
                title=f"Email sent: {exec_record.subject}",
                description=f"Delivered to {exec_record.recipient_email}",
                metadata=meta,
                commit=False,
            )
        else:
            # Check if retryable
            if send_result.is_transient and exec_record.retry_count < max_retries:
                retried_count += 1
                exec_record.retry_count += 1
                exec_record.status = EXEC_STATUS_SCHEDULED
                # Exponential backoff with jitter (max 24 hours = 1440 mins)
                jitter = random.randint(1, 3)
                backoff_delay = min(1440, (2 ** exec_record.retry_count) * 2 + jitter)
                exec_record.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=backoff_delay)
                exec_record.error_message = send_result.error
                exec_record.updated_at = datetime.now(timezone.utc)
                logger.warning(
                    "Execution %d failed (transient), scheduled retry %d in %d mins: %s",
                    exec_record.id,
                    exec_record.retry_count,
                    backoff_delay,
                    send_result.error,
                )
            else:
                failed_count += 1
                exec_record.status = EXEC_STATUS_FAILED
                exec_record.error_message = send_result.error
                exec_record.updated_at = datetime.now(timezone.utc)

                # Record email_failed activity
                meta = {
                    "automation_id": exec_record.automation_id,
                    "execution_id": exec_record.id,
                    "recipient_email": exec_record.recipient_email,
                    "error": send_result.error,
                    "subject": exec_record.subject,
                }
                create_activity(
                    db=db,
                    business_id=exec_record.business_id,
                    activity_type=ACTIVITY_EMAIL_FAILED,
                    title=f"Email failed: {exec_record.subject}",
                    description=f"Failed sending to {exec_record.recipient_email}: {send_result.error}",
                    metadata=meta,
                    commit=False,
                )

    if commit:
        db.commit()

    return {
        "processed": processed_count,
        "sent": sent_count,
        "failed": failed_count,
        "retried": retried_count,
    }
