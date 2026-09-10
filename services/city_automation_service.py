"""
City-First Grade-Based Email Automation Service for Lead Finder CRM.

Encapsulates the streamlined workflow:
CITY -> LEADS -> GRADE -> AI-GENERATED TEMPLATES -> START -> SEND -> REPORT

Reuses existing EmailCampaign, EmailCampaignRecipient, EmailTemplate,
MockEmailProvider / ResendEmailProvider, and TemplateEngine architecture.
"""

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from database.crud import HAS_EMAIL, NO_EMAIL
from database.models import (
    Business,
    BusinessActivity,
    EmailAutomationExecution,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailTemplate,
)
from providers.ai_provider import get_ai_provider
from providers.email_provider import get_email_provider
from services.activity_service import (
    ACTIVITY_EMAIL_CAMPAIGN_CANCELLED,
    ACTIVITY_EMAIL_CAMPAIGN_COMPLETED,
    ACTIVITY_EMAIL_CAMPAIGN_CREATED,
    ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_FAILED,
    ACTIVITY_EMAIL_CAMPAIGN_RECIPIENT_SENT,
    ACTIVITY_EMAIL_CAMPAIGN_STARTED,
    create_activity,
)
from services.email_template_service import create_template
from services.template_engine import render_template
from sqlalchemy import exists, not_

logger = logging.getLogger(__name__)

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

# Canonical server-side queries for successfully emailed leads
from sqlalchemy import select

campaign_sent_subquery = select(1).where(
    EmailCampaignRecipient.business_id == Business.id,
    EmailCampaignRecipient.status == RECIPIENT_SENT,
).correlate(Business).exists()

automation_sent_subquery = select(1).where(
    EmailAutomationExecution.business_id == Business.id,
    EmailAutomationExecution.status == "sent",
).correlate(Business).exists()

IS_ALREADY_SENT = or_(campaign_sent_subquery, automation_sent_subquery)
IS_ELIGIBLE = and_(HAS_EMAIL, not_(IS_ALREADY_SENT))


# ============================================================================
# 1. City & Lead Grade Analytics
# ============================================================================

def get_available_cities(db: Session) -> List[Dict[str, Any]]:
    """
    Return distinct list of cities from accessible business records
    with total lead counts, already-sent lead counts, and email-eligible lead counts.
    Strictly excludes previously successfully emailed businesses from eligible leads.
    """
    results = (
        db.query(
            Business.city,
            func.count(Business.id).label("total_leads"),
            func.count(case((IS_ALREADY_SENT, Business.id), else_=None)).label("already_sent_leads"),
            func.count(case((IS_ELIGIBLE, Business.id), else_=None)).label("eligible_leads"),
        )
        .filter(Business.city.isnot(None), func.trim(Business.city) != "")
        .group_by(Business.city)
        .order_by(Business.city.asc())
        .all()
    )

    return [
        {
            "city": row.city.strip(),
            "total_leads": int(row.total_leads),
            "already_sent_leads": int(row.already_sent_leads),
            "eligible_leads": int(row.eligible_leads),
            "ineligible_leads": max(0, int(row.total_leads) - int(row.eligible_leads) - int(row.already_sent_leads)),
        }
        for row in results
        if row.city and row.city.strip()
    ]


def get_city_lead_grade_stats(db: Session, city: str) -> Dict[str, Any]:
    """
    Return comprehensive lead counts, already-sent counts, and grade distribution (A, B, C, D) for a specific city.
    Identifies total leads vs already-sent leads vs email-eligible leads per grade.
    """
    clean_city = city.strip()
    if not clean_city:
        return {
            "city": "",
            "total_leads": 0,
            "already_sent_leads": 0,
            "email_eligible_leads": 0,
            "ineligible_leads": 0,
            "grades": {
                "A": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
                "B": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
                "C": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
                "D": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
            },
        }

    # Query leads in city grouped by grade (null/empty grade normalized to 'D')
    normalized_grade = func.coalesce(
        func.nullif(func.upper(func.trim(Business.lead_grade)), ""),
        "D",
    )

    grade_rows = (
        db.query(
            normalized_grade.label("grade"),
            func.count(Business.id).label("total"),
            func.count(case((IS_ALREADY_SENT, Business.id), else_=None)).label("already_sent"),
            func.count(case((IS_ELIGIBLE, Business.id), else_=None)).label("eligible"),
        )
        .filter(func.lower(func.trim(Business.city)) == clean_city.lower())
        .group_by(normalized_grade)
        .all()
    )

    grades_map: Dict[str, Dict[str, int]] = {
        "A": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
        "B": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
        "C": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
        "D": {"total": 0, "eligible": 0, "already_sent": 0, "ineligible": 0},
    }

    total_leads = 0
    total_already_sent = 0
    total_eligible = 0

    for row in grade_rows:
        g = str(row.grade).upper()
        if g not in grades_map:
            g = "D"
        tot = int(row.total)
        sent = int(row.already_sent)
        elig = int(row.eligible)
        grades_map[g]["total"] += tot
        grades_map[g]["already_sent"] += sent
        grades_map[g]["eligible"] += elig
        grades_map[g]["ineligible"] += max(0, tot - sent - elig)
        total_leads += tot
        total_already_sent += sent
        total_eligible += elig

    return {
        "city": clean_city,
        "total_leads": total_leads,
        "already_sent_leads": total_already_sent,
        "email_eligible_leads": total_eligible,
        "ineligible_leads": max(0, total_leads - total_already_sent - total_eligible),
        "grades": grades_map,
    }



# ============================================================================
# 2. Template Generation & Master Cold Email
# ============================================================================

def get_master_cold_email_template(city: Optional[str] = None, industry: Optional[str] = None) -> Dict[str, str]:
    """
    Return the universal master cold email template for website mockup outreach.
    """
    ai_provider = get_ai_provider()
    return ai_provider.generate_master_template(city=city, industry=industry)


def generate_city_grade_templates(
    city: str,
    industry: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """
    Generate email templates using the active AI provider abstraction.
    Returns the universal master template.
    """
    ai_provider = get_ai_provider()
    return ai_provider.generate_grade_templates(city=city, industry=industry)


def generate_single_city_template(
    grade: str,
    city: str,
    industry: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate or regenerate the universal email template.
    """
    ai_provider = get_ai_provider()
    return ai_provider.generate_single_grade_template(
        grade=grade,
        city=city,
        industry=industry,
    )


# ============================================================================
# 3. Universal Master Automation Start & Execution
# ============================================================================

def start_city_automation(
    db: Session,
    city: str,
    template: Optional[Dict[str, str]] = None,
    templates: Optional[Dict[str, Dict[str, str]]] = None,
    name: Optional[str] = None,
    scheduled_at: Optional[datetime] = None,
    execute_now: bool = True,
) -> Dict[str, Any]:
    """
    Create and dispatch a city-first email automation using ONE universal master cold email.
    - Persists ONE universal EmailTemplate for the campaign
    - Snapshots eligible leads in the city
    - Dispatches emails uniformly to all eligible leads regardless of lead grade
    - Logs CRM activity and records execution state
    """
    clean_city = city.strip()
    if not clean_city:
        raise ValueError("City name is required.")

    # Verify eligible leads exist
    stats = get_city_lead_grade_stats(db, clean_city)
    if stats["email_eligible_leads"] == 0:
        raise ValueError(
            f"No email-eligible leads found in {clean_city}. Make sure leads have valid email addresses."
        )

    now = datetime.now(timezone.utc)
    auto_name = name.strip() if name and name.strip() else f"Email Automation — {clean_city}"

    # Extract template data from either single template or legacy grade dict
    tpl_data: Dict[str, str] = {}
    if template and isinstance(template, dict):
        tpl_data = template
    elif templates and isinstance(templates, dict):
        # Fallback to master or first available
        tpl_data = templates.get("master") or templates.get("A") or templates.get("B") or next(iter(templates.values()), {})

    master_default = get_master_cold_email_template(clean_city)
    subject = (tpl_data.get("subject") or "").strip() or master_default["subject"]
    body = (tpl_data.get("body") or "").strip() or master_default["body"]
    tpl_name = (tpl_data.get("name") or "").strip() or f"{auto_name} — Master Template"

    # 1. Create / persist single universal EmailTemplate for this automation
    saved_template = EmailTemplate(
        name=tpl_name,
        description=f"Universal master cold email template for {clean_city}",
        subject=subject,
        body=body,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(saved_template)
    db.flush()

    # 2. Create EmailCampaign record (representing this automation run)
    filter_criteria = {
        "city": clean_city,
        "workflow": "city_universal_automation",
        "template_id": saved_template.id,
    }

    initial_status = STATUS_SCHEDULED if scheduled_at else STATUS_DRAFT
    campaign = EmailCampaign(
        name=auto_name,
        description=f"Universal cold email automation for {clean_city}",
        template_id=saved_template.id,
        status=initial_status,
        filter_criteria_json=json.dumps(filter_criteria, ensure_ascii=False),
        recipient_count=0,
        sent_count=0,
        failed_count=0,
        scheduled_at=scheduled_at,
        started_at=None,
        completed_at=None,
        snapshot_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(campaign)
    db.flush()

    # 3. Snapshot eligible businesses in the city (strictly excluding already successfully sent leads)
    eligible_businesses = (
        db.query(Business)
        .filter(
            func.lower(func.trim(Business.city)) == clean_city.lower(),
            IS_ELIGIBLE,
        )
        .order_by(Business.id.asc())
        .all()
    )


    recipients: List[EmailCampaignRecipient] = []
    for biz in eligible_businesses:
        recipient = EmailCampaignRecipient(
            campaign_id=campaign.id,
            business_id=biz.id,
            recipient_email=biz.email.strip(),
            recipient_name=biz.name.strip() if biz.name else None,
            status=RECIPIENT_PENDING,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(recipient)
        recipients.append(recipient)

    campaign.recipient_count = len(recipients)
    db.commit()
    db.refresh(campaign)

    # Activity log for creation
    if eligible_businesses:
        create_activity(
            db=db,
            business_id=eligible_businesses[0].id,
            activity_type=ACTIVITY_EMAIL_CAMPAIGN_CREATED,
            title=f"Email Automation Created — {clean_city}",
            description=f"Email Automation created for {clean_city} with {len(recipients)} eligible recipients.",
            metadata={
                "campaign_id": campaign.id,
                "city": clean_city,
                "recipient_count": len(recipients),
                "grades": stats["grades"],
            },
        )

    # 4. If immediate execution requested and not scheduled, mark running
    if execute_now and not scheduled_at:
        campaign.status = STATUS_RUNNING
        campaign.started_at = now
        db.commit()

    return get_city_automation_report(db, campaign.id)


def dispatch_city_automation(db: Session, campaign_id: int) -> Dict[str, Any]:
    """
    Execute batch dispatch for a city grade automation campaign via queue worker.
    """
    from services.email_queue_worker import process_campaign_queue
    process_campaign_queue(campaign_id, sleep_fn=lambda _: None)
    return get_city_automation_report(db, campaign_id)


# ============================================================================
# 4. Reporting & History
# ============================================================================

def get_city_automation_report(db: Session, campaign_id: int) -> Dict[str, Any]:
    """
    Generate comprehensive progress and grade-wise breakdown report for an automation run.
    """
    campaign = db.query(EmailCampaign).filter(EmailCampaign.id == campaign_id).first()
    if not campaign:
        raise ValueError(f"Automation run with ID {campaign_id} does not exist.")

    filters = {}
    try:
        filters = json.loads(campaign.filter_criteria_json or "{}")
    except Exception:
        pass
    city = filters.get("city", "")

    # Aggregate recipient counts by grade and status
    grade_col = func.coalesce(
        func.nullif(func.upper(func.trim(Business.lead_grade)), ""),
        "D",
    ).label("grade")

    breakdown_rows = (
        db.query(
            grade_col,
            EmailCampaignRecipient.status,
            func.count(EmailCampaignRecipient.id).label("count"),
        )
        .join(Business, EmailCampaignRecipient.business_id == Business.id)
        .filter(EmailCampaignRecipient.campaign_id == campaign.id)
        .group_by(grade_col, EmailCampaignRecipient.status)
        .all()
    )

    grade_breakdown: Dict[str, Dict[str, int]] = {
        "A": {"total": 0, "sent": 0, "failed": 0, "pending": 0, "processing": 0, "cancelled": 0, "skipped": 0},
        "B": {"total": 0, "sent": 0, "failed": 0, "pending": 0, "processing": 0, "cancelled": 0, "skipped": 0},
        "C": {"total": 0, "sent": 0, "failed": 0, "pending": 0, "processing": 0, "cancelled": 0, "skipped": 0},
        "D": {"total": 0, "sent": 0, "failed": 0, "pending": 0, "processing": 0, "cancelled": 0, "skipped": 0},
    }

    pending_count = 0
    processing_count = 0
    sent_count = 0
    failed_count = 0
    cancelled_count = 0
    skipped_count = 0

    for row in breakdown_rows:
        g = str(row.grade).upper()
        if g not in grade_breakdown:
            g = "D"
        st = str(row.status).lower()
        cnt = int(row.count)

        grade_breakdown[g]["total"] += cnt
        if st == RECIPIENT_SENT:
            grade_breakdown[g]["sent"] += cnt
            sent_count += cnt
        elif st == RECIPIENT_FAILED:
            grade_breakdown[g]["failed"] += cnt
            failed_count += cnt
        elif st == RECIPIENT_PROCESSING:
            grade_breakdown[g]["processing"] += cnt
            processing_count += cnt
        elif st == RECIPIENT_PENDING:
            grade_breakdown[g]["pending"] += cnt
            pending_count += cnt
        elif st == RECIPIENT_CANCELLED:
            grade_breakdown[g]["cancelled"] += cnt
            cancelled_count += cnt
        elif st == RECIPIENT_SKIPPED:
            grade_breakdown[g]["skipped"] += cnt
            skipped_count += cnt

    recipient_count = campaign.recipient_count or (sent_count + failed_count + pending_count + processing_count + cancelled_count + skipped_count)
    processed_count = sent_count + failed_count + cancelled_count + skipped_count
    remaining_count = pending_count + processing_count
    percentage = round((processed_count / recipient_count) * 100) if recipient_count > 0 else 100

    # Fetch remaining unsent recipients (pending or processing)
    remaining_rows = (
        db.query(
            EmailCampaignRecipient.id,
            EmailCampaignRecipient.business_id,
            EmailCampaignRecipient.recipient_email,
            EmailCampaignRecipient.recipient_name,
            EmailCampaignRecipient.status,
            EmailCampaignRecipient.error_message,
            EmailCampaignRecipient.sent_at,
            EmailCampaignRecipient.attempted_at,
            Business.name.label("business_name"),
            Business.lead_grade,
        )
        .join(Business, EmailCampaignRecipient.business_id == Business.id)
        .filter(
            EmailCampaignRecipient.campaign_id == campaign.id,
            EmailCampaignRecipient.status.in_([RECIPIENT_PENDING, RECIPIENT_PROCESSING]),
        )
        .order_by(
            EmailCampaignRecipient.next_attempt_at.asc().nullsfirst(),
            EmailCampaignRecipient.id.asc(),
        )
        .limit(200)
        .all()
    )

    remaining_list = [
        {
            "id": r.id,
            "business_id": r.business_id,
            "business_name": r.business_name or r.recipient_name or "Business",
            "recipient_email": r.recipient_email,
            "lead_grade": (r.lead_grade or "D").upper(),
            "status": r.status,
            "error_message": r.error_message,
            "sent_at": r.sent_at,
            "attempted_at": r.attempted_at,
        }
        for r in remaining_rows
    ]

    # Fetch recent recipient executions (all history / logs, including sent and failed)
    recipient_logs = (
        db.query(
            EmailCampaignRecipient.id,
            EmailCampaignRecipient.business_id,
            EmailCampaignRecipient.recipient_email,
            EmailCampaignRecipient.recipient_name,
            EmailCampaignRecipient.status,
            EmailCampaignRecipient.error_message,
            EmailCampaignRecipient.sent_at,
            EmailCampaignRecipient.attempted_at,
            Business.name.label("business_name"),
            Business.lead_grade,
        )
        .join(Business, EmailCampaignRecipient.business_id == Business.id)
        .filter(EmailCampaignRecipient.campaign_id == campaign.id)
        .order_by(EmailCampaignRecipient.id.desc())
        .limit(200)
        .all()
    )

    logs_list = [
        {
            "id": r.id,
            "business_id": r.business_id,
            "business_name": r.business_name or r.recipient_name or "Business",
            "recipient_email": r.recipient_email,
            "lead_grade": (r.lead_grade or "D").upper(),
            "status": r.status,
            "error_message": r.error_message,
            "sent_at": r.sent_at,
            "attempted_at": r.attempted_at,
        }
        for r in recipient_logs
    ]

    return {
        "id": campaign.id,
        "name": campaign.name,
        "city": city,
        "status": campaign.status,
        "recipient_count": recipient_count,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "pending_count": pending_count,
        "processing_count": processing_count,
        "remaining_count": remaining_count,
        "cancelled_count": cancelled_count,
        "skipped_count": skipped_count,
        "percentage": percentage,
        "paused_reason": getattr(campaign, "paused_reason", None),
        "scheduled_at": campaign.scheduled_at,
        "started_at": campaign.started_at,
        "completed_at": campaign.completed_at,
        "created_at": campaign.created_at,
        "grade_breakdown": grade_breakdown,
        "remaining_recipients": remaining_list,
        "recipient_logs": logs_list,
    }


def list_city_automations(
    db: Session,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    List past city email automation runs with high-level metrics.
    """
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size

    query = db.query(EmailCampaign).order_by(EmailCampaign.created_at.desc())
    total = query.count()
    items = query.offset(offset).limit(page_size).all()

    runs = []
    for c in items:
        city = ""
        try:
            f = json.loads(c.filter_criteria_json or "{}")
            city = f.get("city", "")
        except Exception:
            pass
        runs.append({
            "id": c.id,
            "name": c.name,
            "city": city,
            "status": c.status,
            "recipient_count": c.recipient_count,
            "sent_count": c.sent_count,
            "failed_count": c.failed_count,
            "created_at": c.created_at,
            "completed_at": c.completed_at,
        })

    return {
        "items": runs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size else 1,
    }


def resume_city_automation(db: Session, campaign_id: int) -> Dict[str, Any]:
    """
    Resume a paused city email automation run.
    """
    campaign = db.query(EmailCampaign).filter(EmailCampaign.id == campaign_id).first()
    if not campaign:
        raise ValueError(f"Automation run with ID {campaign_id} not found.")

    if campaign.status not in {STATUS_PAUSED, STATUS_RUNNING}:
        return get_city_automation_report(db, campaign.id)

    campaign.status = STATUS_RUNNING
    campaign.paused_reason = None
    campaign.updated_at = datetime.now(timezone.utc)
    db.commit()

    import asyncio
    try:
        from services.email_queue_worker import process_campaign_queue
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(process_campaign_queue, campaign.id))
    except RuntimeError:
        pass

    return get_city_automation_report(db, campaign.id)


def cancel_city_automation(db: Session, campaign_id: int) -> Dict[str, Any]:
    """
    Cancel an active or scheduled city automation run.
    """
    campaign = db.query(EmailCampaign).filter(EmailCampaign.id == campaign_id).first()
    if not campaign:
        raise ValueError(f"Automation run with ID {campaign_id} not found.")

    if campaign.status in {STATUS_COMPLETED, STATUS_COMPLETED_WITH_ERRORS, STATUS_CANCELLED}:
        return get_city_automation_report(db, campaign.id)

    # Cancel pending recipients
    db.query(EmailCampaignRecipient).filter(
        EmailCampaignRecipient.campaign_id == campaign.id,
        EmailCampaignRecipient.status.in_([RECIPIENT_PENDING, RECIPIENT_PROCESSING]),
    ).update({"status": RECIPIENT_CANCELLED, "updated_at": datetime.now(timezone.utc)})

    campaign.status = STATUS_CANCELLED
    campaign.completed_at = datetime.now(timezone.utc)
    campaign.updated_at = datetime.now(timezone.utc)
    db.commit()

    first_rec = (
        db.query(EmailCampaignRecipient)
        .filter(EmailCampaignRecipient.campaign_id == campaign.id)
        .first()
    )
    if first_rec and first_rec.business_id:
        create_activity(
            db=db,
            business_id=first_rec.business_id,
            activity_type=ACTIVITY_EMAIL_CAMPAIGN_CANCELLED,
            title="Email Automation Cancelled",
            description=f"Email Automation '{campaign.name}' was cancelled.",
            metadata={"campaign_id": campaign.id},
        )

    return get_city_automation_report(db, campaign.id)


# ============================================================================
# 5. Dedicated City Mobile Numbers XLSX Export for Manual WhatsApp Outreach
# ============================================================================

from services.xlsx_exporter import build_xlsx_bytes


def get_city_mobile_numbers_export_data(db: Session, city: str) -> Tuple[List[str], List[List[str]], int]:
    """
    Fetch, filter, and deduplicate mobile numbers for a selected city for manual WhatsApp outreach.

    Guarantees:
    - Exactly 3 columns: ["Business Type", "Business Name", "Mobile Number"]
    - Filters only businesses from the requested city (case-insensitive)
    - Filters only businesses with valid phone numbers (non-empty & contains digits)
    - Preserves the original phone formatting / country code (e.g. +91)
    - Deduplicates by normalized phone number (first occurrence kept)
    - Excludes leads already successfully contacted via WhatsApp workflow (BusinessActivity)
    - Does NOT exclude failed or pending WhatsApp attempts
    - No application-level 500-row cap (supports thousands of rows)
    """
    if not city or not city.strip():
        raise ValueError("City parameter is required.")

    city_clean = city.strip()

    # Subquery checking if business was already successfully contacted via WhatsApp
    whatsapp_contacted_subquery = select(1).where(
        BusinessActivity.business_id == Business.id,
        BusinessActivity.activity_type.in_(["whatsapp_contacted", "whatsapp_sent", "whatsapp_delivered"]),
    ).correlate(Business).exists()

    # Query all eligible businesses with no 500-limit cap
    businesses = (
        db.query(Business)
        .filter(
            func.lower(func.trim(Business.city)) == city_clean.lower(),
            Business.phone.isnot(None),
            func.trim(Business.phone) != "",
            not_(whatsapp_contacted_subquery),
        )
        .order_by(Business.id.asc())
        .all()
    )

    headers = ["Business Type", "Business Name", "Mobile Number"]
    rows: List[List[str]] = []
    seen_normalized_phones: set = set()

    for biz in businesses:
        raw_phone = (biz.phone or "").strip()
        if not raw_phone:
            continue

        # Must contain at least one digit
        if not re.search(r"\d", raw_phone):
            continue

        digits_only = re.sub(r"\D", "", raw_phone)
        if not digits_only:
            continue

        # Build candidate deduplication keys (full digits and last 10 digits if available)
        dedup_keys = [digits_only]
        if len(digits_only) >= 10:
            dedup_keys.append(digits_only[-10:])

        if any(k in seen_normalized_phones for k in dedup_keys):
            continue

        for k in dedup_keys:
            seen_normalized_phones.add(k)

        biz_type = (biz.category or "General Business").strip() or "General Business"
        biz_name = (biz.name or "").strip()

        rows.append([biz_type, biz_name, raw_phone])

    return headers, rows, len(businesses)


def export_city_mobile_numbers_xlsx(db: Session, city: str) -> bytes:
    """
    Generate XLSX binary bytes for WhatsApp mobile number outreach for a city.
    """
    headers, rows, _ = get_city_mobile_numbers_export_data(db, city)
    return build_xlsx_bytes(headers=headers, rows=rows, sheet_name=city.strip()[:31])

