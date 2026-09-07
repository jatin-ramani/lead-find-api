"""
Lead Status / CRM Pipeline Service for Lead Finder.

Provides single and bulk operations to transition businesses across CRM pipeline stages
(new, contacted, interested, follow_up, converted, lost) without altering lead scoring,
lead grades, tags, favorites, or internal notes.
"""

from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Business

VALID_LEAD_STATUSES = {
    "new",
    "contacted",
    "interested",
    "follow_up",
    "converted",
    "lost",
}


from services.activity_service import (
    ACTIVITY_STATUS_CHANGED,
    bulk_create_activities,
    create_activity,
)
from services.email_automation_service import (
    TRIGGER_LEAD_STATUS_CHANGED,
    evaluate_automations_for_event,
)


def get_business_by_id(db: Session, business_id: int) -> Optional[Business]:
    """Fetch a business by ID."""
    return db.query(Business).filter(Business.id == business_id).first()


def update_business_lead_status(
    db: Session,
    business_id: int,
    status: str,
) -> Optional[Business]:
    """
    Update the lead status of a single business.
    Returns None if the business does not exist or status is invalid.
    """
    if status not in VALID_LEAD_STATUSES:
        return None

    business = get_business_by_id(db, business_id)
    if not business:
        return None

    if business.lead_status != status:
        old_status = business.lead_status or "new"
        business.lead_status = status
        # Record activity
        old_label = old_status.replace("_", " ").title()
        new_label = status.replace("_", " ").title()
        create_activity(
            db=db,
            business_id=business_id,
            activity_type=ACTIVITY_STATUS_CHANGED,
            title="Lead status changed",
            description=f"{old_label} → {new_label}",
            metadata={"old_status": old_status, "new_status": status},
            commit=False,
        )
        evaluate_automations_for_event(
            db=db,
            trigger_type=TRIGGER_LEAD_STATUS_CHANGED,
            business_id=business_id,
            event_discriminator=status,
            commit=False,
        )
        db.commit()
        db.refresh(business)

    return business


def bulk_update_lead_status(
    db: Session,
    business_ids: Sequence[int],
    status: str,
) -> Dict[str, Any]:
    """
    Bulk update lead status for a sequence of business IDs in a single transaction.
    Non-existent IDs and duplicates are handled cleanly without error.
    """
    if status not in VALID_LEAD_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    unique_ids = list({int(b_id) for b_id in business_ids if b_id})
    if not unique_ids:
        return {
            "updated_count": 0,
            "total_requested": 0,
            "status": status,
        }

    # Query existing statuses to record activities only for actual changes
    businesses_to_update = (
        db.query(Business.id, Business.lead_status)
        .filter(
            Business.id.in_(unique_ids),
            Business.lead_status != status,
        )
        .all()
    )

    if not businesses_to_update:
        return {
            "updated_count": 0,
            "total_requested": len(unique_ids),
            "status": status,
        }

    target_ids = [b_id for b_id, _ in businesses_to_update]

    # Execute update in a single atomic SQL statement
    updated_count = (
        db.query(Business)
        .filter(Business.id.in_(target_ids))
        .update(
            {"lead_status": status},
            synchronize_session=False,
        )
    )

    # Bulk create activities
    new_label = status.replace("_", " ").title()
    activities_data = [
        {
            "business_id": b_id,
            "activity_type": ACTIVITY_STATUS_CHANGED,
            "title": "Lead status changed",
            "description": f"{(old_st or 'new').replace('_', ' ').title()} → {new_label}",
            "metadata": {"old_status": old_st or "new", "new_status": status},
        }
        for b_id, old_st in businesses_to_update
    ]
    bulk_create_activities(db, activities_data, commit=False)

    for b_id, _ in businesses_to_update:
        evaluate_automations_for_event(
            db=db,
            trigger_type=TRIGGER_LEAD_STATUS_CHANGED,
            business_id=b_id,
            event_discriminator=status,
            commit=False,
        )

    db.commit()

    return {
        "updated_count": updated_count,
        "total_requested": len(unique_ids),
        "status": status,
    }


def get_lead_status_counts(db: Session, city: Optional[str] = None) -> Dict[str, int]:
    """
    Return counts grouped by lead_status using efficient SQL aggregation.
    """
    query = db.query(Business.lead_status, func.count(Business.id))
    if city:
        query = query.filter(Business.city == city)
    rows = query.group_by(Business.lead_status).all()

    counts = {status: 0 for status in VALID_LEAD_STATUSES}
    for status, count in rows:
        if status in counts:
            counts[status] = count
    return counts
