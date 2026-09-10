"""
Durable Background Email Queue Worker & Rate Limiter for City Email Automations.

Provides:
1. Durable database-backed queue processing.
2. Configurable rate limiting throttle (default: 20 sends/min ~ 3.0s smooth spacing).
3. Concurrency-safe atomic recipient claiming.
4. Error classification & retry with exponential backoff for temporary rate limits (429/transient).
5. Daily quota protection (pauses campaign safely when 400/day limit is reached).
6. Stale processing recovery on worker restart.
7. Background execution independent of HTTP requests and browser sessions.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import random
import threading
import time
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from config import settings
from database.db import get_db, get_session
from database.models import (
    Business,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailTemplate,
)
from providers.email_provider import get_email_provider
from services.activity_service import (
    ACTIVITY_EMAIL_CAMPAIGN_COMPLETED,
    ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_FAILED,
    ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_SENT,
    create_activity,
)
from services.template_engine import ensure_html_email, html_to_plain_text, render_template

logger = logging.getLogger(__name__)


_ACTIVE_CAMPAIGNS_LOCK = threading.Lock()
_ACTIVE_CAMPAIGNS: set = set()

# Execution statuses
STATUS_DRAFT = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

RECIPIENT_PENDING = "pending"
RECIPIENT_PROCESSING = "processing"
RECIPIENT_SENT = "sent"
RECIPIENT_FAILED = "failed"
RECIPIENT_CANCELLED = "cancelled"
RECIPIENT_SKIPPED = "skipped"

DEFAULT_MAX_RETRIES = 3
STALE_PROCESSING_TIMEOUT_MINUTES = 5


def _is_transient_error(error_message: Optional[str]) -> bool:
    """Determine whether an error is transient / retryable (e.g. 429, rate limit, timeout)."""
    if not error_message:
        return False
    msg = error_message.lower()
    transient_indicators = [
        "429",
        "rate limit",
        "rate_limit",
        "user_rate_limit",
        "userratelimitexceeded",
        "quota exceeded for quota metric",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "503",
        "service unavailable",
        "try again later",
    ]
    return any(indicator in msg for indicator in transient_indicators)


def _is_auth_error(error_message: Optional[str]) -> bool:
    """Determine whether an error is a non-retryable authentication/credentials error."""
    if not error_message:
        return False
    msg = error_message.lower()
    auth_indicators = [
        "401",
        "invalid_grant",
        "token has been expired or revoked",
        "no active gmail oauth",
        "gmail oauth credentials not configured",
        "unauthorized",
        "insufficient permissions",
    ]
    return any(indicator in msg for indicator in auth_indicators)


def calculate_backoff_seconds(attempt_count: int, base_seconds: float = 4.0, max_seconds: float = 300.0) -> float:
    """Calculate exponential backoff delay with jitter."""
    exp_delay = min(max_seconds, base_seconds * (2 ** max(0, attempt_count - 1)))
    jitter = random.uniform(0.5, 1.5)
    return round(exp_delay + jitter, 2)


def get_throttle_interval_seconds() -> float:
    """Return the spacing in seconds between sends based on EMAIL_SENDS_PER_MINUTE."""
    sends_per_min = max(1, settings.EMAIL_SENDS_PER_MINUTE)
    return 60.0 / float(sends_per_min)


def recover_stale_processing_recipients(db: Session, timeout_minutes: int = STALE_PROCESSING_TIMEOUT_MINUTES) -> int:
    """
    Recover recipient rows stuck in 'processing' state from crashed or restarted workers.
    Reverts eligible items to 'pending' and marks items exceeding max retries as 'failed'.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    stale_recipients = (
        db.query(EmailCampaignRecipient)
        .filter(
            EmailCampaignRecipient.status == RECIPIENT_PROCESSING,
            or_(
                EmailCampaignRecipient.attempted_at < cutoff,
                EmailCampaignRecipient.attempted_at.is_(None),
            ),
        )
        .all()
    )

    recovered_count = 0
    now = datetime.now(timezone.utc)
    for rec in stale_recipients:
        if rec.attempt_count < DEFAULT_MAX_RETRIES:
            rec.status = RECIPIENT_PENDING
            rec.next_attempt_at = None
            rec.error_message = f"Recovered from interrupted processing at {now.isoformat()}"
        else:
            rec.status = RECIPIENT_FAILED
            rec.error_message = f"Failed after worker crash and max retries ({DEFAULT_MAX_RETRIES}) reached"
        rec.updated_at = now
        recovered_count += 1

    if recovered_count > 0:
        db.commit()
        logger.info("Recovered %d stale processing campaign recipients.", recovered_count)

    return recovered_count


def claim_next_recipient(db: Session, campaign_id: int) -> Optional[EmailCampaignRecipient]:
    """
    Atomically claim the next eligible pending recipient for processing.
    Uses PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED if available, or transactional row locking.
    """
    now = datetime.now(timezone.utc)
    query = (
        db.query(EmailCampaignRecipient)
        .filter(
            EmailCampaignRecipient.campaign_id == campaign_id,
            EmailCampaignRecipient.status == RECIPIENT_PENDING,
            or_(
                EmailCampaignRecipient.next_attempt_at.is_(None),
                EmailCampaignRecipient.next_attempt_at <= now,
            ),
        )
        .order_by(
            EmailCampaignRecipient.next_attempt_at.asc().nullsfirst(),
            EmailCampaignRecipient.id.asc(),
        )
    )

    # Apply SKIP LOCKED if backend supports it (PostgreSQL)
    bind = db.get_bind()
    if bind and bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)

    recipient = query.first()
    if not recipient:
        return None

    recipient.status = RECIPIENT_PROCESSING
    recipient.attempted_at = now
    recipient.updated_at = now
    db.commit()
    db.refresh(recipient)
    return recipient


def process_campaign_queue(campaign_id: int, sleep_fn=time.sleep) -> Dict[str, Any]:
    """
    Process the queued recipients for a campaign sequentially with rate limiting throttle,
    exponential backoff retry, and daily quota awareness.
    Guaranteed single-worker-per-campaign in memory.
    """
    with _ACTIVE_CAMPAIGNS_LOCK:
        if campaign_id in _ACTIVE_CAMPAIGNS:
            logger.info("Campaign %d is already active in another worker task. Skipping duplicate.", campaign_id)
            return {"success": True, "already_running": True}
        _ACTIVE_CAMPAIGNS.add(campaign_id)

    try:
        return _execute_campaign_queue(campaign_id=campaign_id, sleep_fn=sleep_fn)
    finally:
        with _ACTIVE_CAMPAIGNS_LOCK:
            _ACTIVE_CAMPAIGNS.discard(campaign_id)


def _execute_campaign_queue(campaign_id: int, sleep_fn=time.sleep) -> Dict[str, Any]:
    with get_session() as db:
        # 1. Recover any stale items first
        recover_stale_processing_recipients(db)

        campaign = db.query(EmailCampaign).filter(EmailCampaign.id == campaign_id).first()
        if not campaign:
            logger.error("Campaign %d not found for queue processing.", campaign_id)
            return {"success": False, "error": "Campaign not found"}

        if campaign.status in {STATUS_COMPLETED, STATUS_COMPLETED_WITH_ERRORS, STATUS_CANCELLED}:
            logger.info("Campaign %d is in terminal status '%s'. Skipping.", campaign_id, campaign.status)
            return {"success": True, "status": campaign.status}

        # Mark campaign running
        now = datetime.now(timezone.utc)
        if campaign.status != STATUS_RUNNING:
            campaign.status = STATUS_RUNNING
            campaign.started_at = campaign.started_at or now
            campaign.paused_reason = None
            campaign.updated_at = now
            db.commit()

        # Parse grade template mappings
        filters = {}
        try:
            filters = json.loads(campaign.filter_criteria_json or "{}")
        except Exception:
            pass
        grade_template_ids = filters.get("grade_templates", {})

        grade_templates: Dict[str, EmailTemplate] = {}
        for grade in ["A", "B", "C", "D"]:
            tid = grade_template_ids.get(grade)
            if tid:
                tpl = db.query(EmailTemplate).filter(EmailTemplate.id == tid).first()
                if tpl:
                    grade_templates[grade] = tpl

        fallback_template = campaign.template
        email_provider = get_email_provider()
        throttle_interval = get_throttle_interval_seconds()

    # Main processing loop
    while True:
        with get_session() as db:
            # Check campaign status for external cancellation or pause
            campaign = db.query(EmailCampaign).filter(EmailCampaign.id == campaign_id).first()
            if not campaign or campaign.status in {STATUS_CANCELLED, STATUS_PAUSED}:
                logger.info("Campaign %d stopped due to status: %s", campaign_id, campaign.status if campaign else "None")
                break

            # Claim next recipient atomically
            recipient = claim_next_recipient(db, campaign_id)
            if not recipient:
                # Check if there are any pending items waiting for retry in the future
                future_pending = (
                    db.query(EmailCampaignRecipient)
                    .filter(
                        EmailCampaignRecipient.campaign_id == campaign_id,
                        EmailCampaignRecipient.status == RECIPIENT_PENDING,
                        EmailCampaignRecipient.next_attempt_at > datetime.now(timezone.utc),
                    )
                    .order_by(EmailCampaignRecipient.next_attempt_at.asc())
                    .first()
                )

                if future_pending and future_pending.next_attempt_at:
                    wait_seconds = min(60.0, max(1.0, (future_pending.next_attempt_at - datetime.now(timezone.utc)).total_seconds()))
                    logger.info("Campaign %d: pending retries waiting. Sleeping for %.1fs.", campaign_id, wait_seconds)
                    sleep_fn(wait_seconds)
                    continue

                # Check if any items are still in processing by other concurrent workers
                processing_count = (
                    db.query(EmailCampaignRecipient)
                    .filter(
                        EmailCampaignRecipient.campaign_id == campaign_id,
                        EmailCampaignRecipient.status == RECIPIENT_PROCESSING,
                    )
                    .count()
                )
                if processing_count > 0:
                    sleep_fn(1.0)
                    continue

                # No more pending or processing items — finalize campaign
                sent_count = (
                    db.query(EmailCampaignRecipient)
                    .filter(
                        EmailCampaignRecipient.campaign_id == campaign_id,
                        EmailCampaignRecipient.status == RECIPIENT_SENT,
                    )
                    .count()
                )
                failed_count = (
                    db.query(EmailCampaignRecipient)
                    .filter(
                        EmailCampaignRecipient.campaign_id == campaign_id,
                        EmailCampaignRecipient.status == RECIPIENT_FAILED,
                    )
                    .count()
                )

                final_status = STATUS_COMPLETED_WITH_ERRORS if failed_count > 0 else STATUS_COMPLETED
                campaign.status = final_status
                campaign.sent_count = sent_count
                campaign.failed_count = failed_count
                campaign.completed_at = datetime.now(timezone.utc)
                campaign.updated_at = datetime.now(timezone.utc)
                db.commit()

                logger.info(
                    "Campaign %d completed with status '%s' (sent: %d, failed: %d).",
                    campaign_id,
                    final_status,
                    sent_count,
                    failed_count,
                )
                break

            # Process the claimed recipient
            biz = recipient.business
            if not biz or not recipient.recipient_email:
                recipient.status = RECIPIENT_FAILED
                recipient.error_message = "Recipient business or email missing"
                recipient.updated_at = datetime.now(timezone.utc)
                campaign.failed_count = (campaign.failed_count or 0) + 1
                db.commit()
                continue

            grade = (biz.lead_grade or "D").upper().strip()
            # Primary: campaign.template (universal master template). Fallback: legacy grade_templates
            tpl = campaign.template or grade_templates.get(grade, fallback_template)
            if not tpl:
                recipient.status = RECIPIENT_FAILED
                recipient.error_message = f"No email template assigned for campaign or Grade {grade}"
                recipient.updated_at = datetime.now(timezone.utc)
                campaign.failed_count = (campaign.failed_count or 0) + 1
                db.commit()
                continue

            business_name = (biz.name or "").strip()
            raw_contact = (recipient.recipient_name or "").strip()
            if raw_contact and raw_contact.lower() != business_name.lower():
                contact_name = raw_contact
            else:
                contact_name = f"{business_name} team" if business_name else "team"

            context = {
                "business_name": business_name,
                "contact_name": contact_name,
                "email": recipient.recipient_email or "",
                "phone": biz.phone or "",
                "website": biz.website or "",
                "lead_status": getattr(biz, "lead_status", "new") or "new",
                "lead_score": str(getattr(biz, "lead_score", 0) or 0),
                "follow_up_title": "",
                "follow_up_due_at": "",
            }

            try:
                rendered_subject = render_template(tpl.subject, context, escape_html=False)
                body_to_render = ensure_html_email(tpl.body)
                rendered_body = render_template(body_to_render, context, escape_html=True)
                plain_body = html_to_plain_text(rendered_body)
            except Exception as e:
                recipient.status = RECIPIENT_FAILED
                recipient.error_message = f"Template rendering error: {str(e)}"
                recipient.updated_at = datetime.now(timezone.utc)
                campaign.failed_count = (campaign.failed_count or 0) + 1
                db.commit()
                continue

            # Attempt send via provider
            send_start_time = time.monotonic()
            result = email_provider.send_email(
                to_email=recipient.recipient_email,
                subject=rendered_subject,
                html_content=rendered_body,
                text_content=plain_body,
                metadata={
                    "db": db,
                    "campaign_id": campaign.id,
                    "business_id": biz.id,
                    "lead_grade": grade,
                    "template_id": tpl.id,
                },
            )


            now_utc = datetime.now(timezone.utc)
            if result.success:
                recipient.status = RECIPIENT_SENT
                recipient.sent_at = now_utc
                recipient.provider_message_id = result.message_id
                recipient.error_message = None
                recipient.updated_at = now_utc
                campaign.sent_count = (campaign.sent_count or 0) + 1
                db.commit()

                # Activity log
                create_activity(
                    db=db,
                    business_id=biz.id,
                    activity_type=ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_SENT,
                    title="Email Automation Dispatched",
                    description=f"Email sent via Automation: '{rendered_subject}' (Grade {grade})",
                    metadata={
                        "campaign_id": campaign.id,
                        "campaign_name": campaign.name,
                        "template_id": tpl.id,
                        "message_id": result.message_id,
                        "grade": grade,
                    },
                )
            else:
                error_str = result.error or "Email delivery failed"

                # 1. Check daily quota limit exhaustion
                if "Daily sending limit reached" in error_str:
                    logger.warning("Daily quota reached during campaign %d. Pausing automation.", campaign_id)
                    recipient.status = RECIPIENT_PENDING
                    recipient.next_attempt_at = None
                    recipient.error_message = "Paused: Daily sending limit reached (400/day)"
                    recipient.updated_at = now_utc

                    campaign.status = STATUS_PAUSED
                    campaign.paused_reason = "Daily sending limit reached (400/day). Automation paused."
                    campaign.updated_at = now_utc
                    db.commit()
                    break

                # 2. Check non-retryable authentication error
                if _is_auth_error(error_str):
                    logger.error("Auth error during campaign %d: %s. Pausing automation.", campaign_id, error_str)
                    recipient.status = RECIPIENT_FAILED
                    recipient.error_message = error_str
                    recipient.updated_at = now_utc
                    campaign.failed_count = (campaign.failed_count or 0) + 1

                    campaign.status = STATUS_PAUSED
                    campaign.paused_reason = f"Gmail authentication error: {error_str}. Please reconnect Gmail and resume."
                    campaign.updated_at = now_utc
                    db.commit()
                    break

                # 3. Check transient / retryable error (e.g. 429 rate limit)
                if _is_transient_error(error_str):
                    recipient.attempt_count += 1
                    if recipient.attempt_count < DEFAULT_MAX_RETRIES:
                        backoff = calculate_backoff_seconds(recipient.attempt_count)
                        recipient.status = RECIPIENT_PENDING
                        recipient.next_attempt_at = now_utc + timedelta(seconds=backoff)
                        recipient.error_message = (
                            f"Temporary rate limit (attempt {recipient.attempt_count}/{DEFAULT_MAX_RETRIES}): "
                            f"{error_str}. Retrying in {backoff:.1f}s."
                        )
                        recipient.updated_at = now_utc
                        db.commit()
                        logger.warning(
                            "Recipient %d rate-limited. Retry scheduled in %.1fs (attempt %d).",
                            recipient.id,
                            backoff,
                            recipient.attempt_count,
                        )
                    else:
                        recipient.status = RECIPIENT_FAILED
                        recipient.error_message = f"Max retries ({DEFAULT_MAX_RETRIES}) exceeded: {error_str}"
                        recipient.updated_at = now_utc
                        campaign.failed_count = (campaign.failed_count or 0) + 1
                        db.commit()
                        create_activity(
                            db=db,
                            business_id=biz.id,
                            activity_type=ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_FAILED,
                            title="Email Automation Failed (Rate Limit)",
                            description=f"Email failed after max retries: {recipient.error_message}",
                            metadata={"campaign_id": campaign.id, "grade": grade},
                        )
                else:
                    # Permanent failure
                    recipient.status = RECIPIENT_FAILED
                    recipient.error_message = error_str
                    recipient.updated_at = now_utc
                    campaign.failed_count = (campaign.failed_count or 0) + 1
                    db.commit()
                    create_activity(
                        db=db,
                        business_id=biz.id,
                        activity_type=ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_FAILED,
                        title="Email Automation Failed",
                        description=f"Email failed via Automation: {recipient.error_message}",
                        metadata={"campaign_id": campaign.id, "grade": grade},
                    )

        # Smooth rate limiter throttle: sleep the remainder of the interval
        elapsed = time.monotonic() - send_start_time
        remaining_throttle = max(0.0, throttle_interval - elapsed)
        if remaining_throttle > 0:
            sleep_fn(remaining_throttle)

    return {"success": True, "campaign_id": campaign_id}


def resume_active_campaigns(db: Session) -> int:
    """
    Find campaigns in RUNNING or SCHEDULED state with pending/processing recipients on server startup
    and resume background worker tasks for them.
    """
    running_campaigns = (
        db.query(EmailCampaign)
        .filter(EmailCampaign.status.in_([STATUS_RUNNING, STATUS_SCHEDULED]))
        .all()
    )
    resumed_count = 0
    for camp in running_campaigns:
        # Check if campaign has unfinished recipients
        has_pending = (
            db.query(EmailCampaignRecipient)
            .filter(
                EmailCampaignRecipient.campaign_id == camp.id,
                EmailCampaignRecipient.status.in_([RECIPIENT_PENDING, RECIPIENT_PROCESSING]),
            )
            .first()
        )
        if has_pending:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(asyncio.to_thread(process_campaign_queue, camp.id))
                resumed_count += 1
            except RuntimeError:
                t = threading.Thread(target=process_campaign_queue, args=(camp.id,), daemon=True)
                t.start()
                resumed_count += 1

    if resumed_count > 0:
        logger.info("Resumed %d active email automations on server startup.", resumed_count)
    return resumed_count
