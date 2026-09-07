"""
Lead Activity / History Service for Lead Finder.

Provides a centralized, normalized audit and history logging mechanism
for important CRM actions performed on businesses.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

from database.models import Business, BusinessActivity

logger = logging.getLogger(__name__)

# Supported Activity Types
ACTIVITY_BUSINESS_CREATED = "business_created"
ACTIVITY_BUSINESS_UPDATED = "business_updated"
ACTIVITY_LEAD_CREATED = "lead_created"
ACTIVITY_FAVORITE_ADDED = "favorite_added"
ACTIVITY_FAVORITE_REMOVED = "favorite_removed"
ACTIVITY_STATUS_CHANGED = "status_changed"
ACTIVITY_TAG_ADDED = "tag_added"
ACTIVITY_TAG_REMOVED = "tag_removed"
ACTIVITY_NOTE_CREATED = "note_created"
ACTIVITY_NOTE_UPDATED = "note_updated"
ACTIVITY_NOTE_DELETED = "note_deleted"
ACTIVITY_EMAIL_ADDED = "email_added"
ACTIVITY_PHONE_ADDED = "phone_added"
ACTIVITY_WEBSITE_ADDED = "website_added"
ACTIVITY_SCRAPE_COMPLETED = "scrape_completed"
ACTIVITY_LEAD_SCANNED = "lead_scanned"
ACTIVITY_LEAD_SCRAPED = "lead_scraped"
ACTIVITY_LEAD_ENRICHED = "lead_enriched"
ACTIVITY_LEAD_SCORE_CHANGED = "lead_score_changed"

# Follow-up activity types
ACTIVITY_FOLLOW_UP_CREATED = "follow_up_created"
ACTIVITY_FOLLOW_UP_UPDATED = "follow_up_updated"
ACTIVITY_FOLLOW_UP_COMPLETED = "follow_up_completed"
ACTIVITY_FOLLOW_UP_CANCELLED = "follow_up_cancelled"
ACTIVITY_FOLLOW_UP_DELETED = "follow_up_deleted"

# Email automation activity types
ACTIVITY_EMAIL_AUTOMATION_TRIGGERED = "email_automation_triggered"
ACTIVITY_EMAIL_SCHEDULED = "email_scheduled"
ACTIVITY_EMAIL_SENT = "email_sent"
ACTIVITY_EMAIL_FAILED = "email_failed"
ACTIVITY_EMAIL_OPENED = "email_opened"
ACTIVITY_EMAIL_CLICKED = "email_clicked"
ACTIVITY_CAMPAIGN_ADDED = "campaign_added"
ACTIVITY_CAMPAIGN_REMOVED = "campaign_removed"
ACTIVITY_ENRICHMENT_COMPLETED = "enrichment_completed"

VALID_ACTIVITY_TYPES = {
    ACTIVITY_BUSINESS_CREATED,
    ACTIVITY_BUSINESS_UPDATED,
    ACTIVITY_LEAD_CREATED,
    ACTIVITY_FAVORITE_ADDED,
    ACTIVITY_FAVORITE_REMOVED,
    ACTIVITY_STATUS_CHANGED,
    ACTIVITY_TAG_ADDED,
    ACTIVITY_TAG_REMOVED,
    ACTIVITY_NOTE_CREATED,
    ACTIVITY_NOTE_UPDATED,
    ACTIVITY_NOTE_DELETED,
    ACTIVITY_EMAIL_ADDED,
    ACTIVITY_PHONE_ADDED,
    ACTIVITY_WEBSITE_ADDED,
    ACTIVITY_SCRAPE_COMPLETED,
    ACTIVITY_LEAD_SCANNED,
    ACTIVITY_LEAD_SCRAPED,
    ACTIVITY_LEAD_ENRICHED,
    ACTIVITY_LEAD_SCORE_CHANGED,
    ACTIVITY_FOLLOW_UP_CREATED,
    ACTIVITY_FOLLOW_UP_UPDATED,
    ACTIVITY_FOLLOW_UP_COMPLETED,
    ACTIVITY_FOLLOW_UP_CANCELLED,
    ACTIVITY_FOLLOW_UP_DELETED,
    ACTIVITY_EMAIL_AUTOMATION_TRIGGERED,
    ACTIVITY_EMAIL_SCHEDULED,
    ACTIVITY_EMAIL_SENT,
    ACTIVITY_EMAIL_FAILED,
    ACTIVITY_EMAIL_OPENED,
    ACTIVITY_EMAIL_CLICKED,
    ACTIVITY_CAMPAIGN_ADDED,
    ACTIVITY_CAMPAIGN_REMOVED,
    ACTIVITY_ENRICHMENT_COMPLETED,
}


def create_activity(
    db: Session,
    business_id: int,
    activity_type: str,
    title: str,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> Optional[BusinessActivity]:
    """
    Create a single historical activity record for a business.
    Returns the created BusinessActivity or None if creation fails.
    """
    try:
        meta_str = "{}"
        if metadata:
            meta_str = json.dumps(metadata, ensure_ascii=False)

        now = datetime.now(timezone.utc)
        activity = BusinessActivity(
            business_id=business_id,
            activity_type=activity_type,
            title=title,
            description=description,
            metadata_json=meta_str,
            created_at=now,
        )
        db.add(activity)
        if commit:
            db.commit()
            db.refresh(activity)
        return activity
    except Exception:
        logger.exception("Failed to create activity record for business %s", business_id)
        if commit:
            db.rollback()
        return None


def bulk_create_activities(
    db: Session,
    activities_data: Sequence[Dict[str, Any]],
    commit: bool = True,
) -> List[BusinessActivity]:
    """
    Bulk insert multiple activity records in a single database transaction.
    """
    if not activities_data:
        return []

    created_objects: List[BusinessActivity] = []
    now = datetime.now(timezone.utc)

    for item in activities_data:
        b_id = item.get("business_id")
        act_type = item.get("activity_type")
        title = item.get("title")
        if not b_id or not act_type or not title:
            continue

        meta = item.get("metadata")
        meta_str = "{}"
        if meta:
            meta_str = json.dumps(meta, ensure_ascii=False)

        activity = BusinessActivity(
            business_id=b_id,
            activity_type=act_type,
            title=title,
            description=item.get("description"),
            metadata_json=meta_str,
            created_at=now,
        )
        db.add(activity)
        created_objects.append(activity)

    if commit and created_objects:
        db.commit()

    return created_objects


def get_business_activities(
    db: Session,
    business_id: int,
    page: int = 1,
    page_size: int = 20,
    activity_type: Optional[str] = None,
) -> List[BusinessActivity]:
    """
    Retrieve paginated activities for a business, ordered newest first (created_at DESC, id DESC).
    """
    query = db.query(BusinessActivity).filter(BusinessActivity.business_id == business_id)
    if activity_type:
        query = query.filter(BusinessActivity.activity_type == activity_type)

    # Deterministic chronological sort: newest first
    query = query.order_by(
        BusinessActivity.created_at.desc(),
        BusinessActivity.id.desc(),
    )

    offset = max(0, (page - 1) * page_size)
    return query.offset(offset).limit(page_size).all()


def get_business_activity_count(
    db: Session,
    business_id: int,
    activity_type: Optional[str] = None,
) -> int:
    """
    Return total count of activity records for a business.
    """
    query = db.query(BusinessActivity).filter(BusinessActivity.business_id == business_id)
    if activity_type:
        query = query.filter(BusinessActivity.activity_type == activity_type)
    return query.count()


def delete_business_activities(
    db: Session,
    business_id: int,
    commit: bool = True,
) -> int:
    """
    Delete all activity records for a business (administrative or cascade cleanup only).
    """
    deleted = (
        db.query(BusinessActivity)
        .filter(BusinessActivity.business_id == business_id)
        .delete(synchronize_session=False)
    )
    if commit and deleted:
        db.commit()
    return deleted
