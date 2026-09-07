"""
Lead Follow-Up / CRM Task Service for Lead Finder.

Provides centralized management for CRM follow-ups attached to businesses,
including lifecycle transitions (pending, completed, cancelled), priorities,
overdue evaluation, and automatic activity history logging.
"""

from datetime import datetime, timezone
import logging
from typing import List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Business, BusinessFollowUp
from services.activity_service import (
    ACTIVITY_FOLLOW_UP_CANCELLED,
    ACTIVITY_FOLLOW_UP_COMPLETED,
    ACTIVITY_FOLLOW_UP_CREATED,
    ACTIVITY_FOLLOW_UP_DELETED,
    ACTIVITY_FOLLOW_UP_UPDATED,
    create_activity,
)
from services.email_automation_service import (
    TRIGGER_FOLLOW_UP_DUE,
    TRIGGER_FOLLOW_UP_OVERDUE,
    evaluate_automations_for_event,
)

logger = logging.getLogger(__name__)

# Controlled Status Vocabulary
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

VALID_FOLLOW_UP_STATUSES = {
    STATUS_PENDING,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
}

# Controlled Priority Vocabulary
PRIORITY_LOW = "low"
PRIORITY_MEDIUM = "medium"
PRIORITY_HIGH = "high"

VALID_FOLLOW_UP_PRIORITIES = {
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    PRIORITY_HIGH,
}


def compute_is_overdue(due_at: Optional[datetime], status: str) -> bool:
    """
    Returns True if the follow-up is pending and its due date is in the past.
    """
    if status != STATUS_PENDING or due_at is None:
        return False

    now_utc = datetime.now(timezone.utc)
    # Normalize naive datetimes to UTC if needed
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)

    return due_at < now_utc


def get_follow_up(db: Session, follow_up_id: int) -> Optional[BusinessFollowUp]:
    """Fetch a single follow-up by ID."""
    return db.query(BusinessFollowUp).filter(BusinessFollowUp.id == follow_up_id).first()


def create_follow_up(
    db: Session,
    business_id: int,
    title: str,
    description: Optional[str] = None,
    due_at: Optional[datetime] = None,
    priority: str = PRIORITY_MEDIUM,
    commit: bool = True,
) -> Optional[BusinessFollowUp]:
    """
    Create a new follow-up associated with a business.
    Returns None if the business does not exist.
    """
    business = db.query(Business.id).filter(Business.id == business_id).first()
    if not business:
        return None

    cleaned_title = title.strip()
    cleaned_desc = description.strip() if description else None
    cleaned_priority = priority.lower() if priority else PRIORITY_MEDIUM
    if cleaned_priority not in VALID_FOLLOW_UP_PRIORITIES:
        cleaned_priority = PRIORITY_MEDIUM

    now = datetime.now(timezone.utc)
    follow_up = BusinessFollowUp(
        business_id=business_id,
        title=cleaned_title,
        description=cleaned_desc,
        due_at=due_at,
        completed_at=None,
        status=STATUS_PENDING,
        priority=cleaned_priority,
        created_at=now,
        updated_at=now,
    )
    db.add(follow_up)
    db.flush()

    # Record Activity
    meta = {
        "follow_up_id": follow_up.id,
        "title": follow_up.title,
        "priority": follow_up.priority,
        "due_at": follow_up.due_at.isoformat() if follow_up.due_at else None,
    }
    create_activity(
        db=db,
        business_id=business_id,
        activity_type=ACTIVITY_FOLLOW_UP_CREATED,
        title=f"Follow-up scheduled: {cleaned_title}",
        description=f"Priority: {cleaned_priority.capitalize()}" + (f" | Due: {due_at.strftime('%Y-%m-%d %H:%M UTC')}" if due_at else ""),
        metadata=meta,
        commit=False,
    )

    evaluate_automations_for_event(
        db=db,
        trigger_type=TRIGGER_FOLLOW_UP_DUE,
        business_id=business_id,
        follow_up_id=follow_up.id,
        commit=False,
    )

    if commit:
        db.commit()
        db.refresh(follow_up)

    return follow_up


def list_follow_ups(
    db: Session,
    business_id: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    overdue: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[BusinessFollowUp]:
    """
    Query follow-ups with optional filters, ordered by pending first, due_at asc, id desc.
    """
    query = db.query(BusinessFollowUp)

    if business_id is not None:
        query = query.filter(BusinessFollowUp.business_id == business_id)

    if status:
        query = query.filter(BusinessFollowUp.status == status)

    if priority:
        query = query.filter(BusinessFollowUp.priority == priority)

    now_utc = datetime.now(timezone.utc)
    if overdue is True:
        query = query.filter(
            BusinessFollowUp.status == STATUS_PENDING,
            BusinessFollowUp.due_at.isnot(None),
            BusinessFollowUp.due_at < now_utc,
        )
    elif overdue is False:
        query = query.filter(
            (BusinessFollowUp.status != STATUS_PENDING)
            | (BusinessFollowUp.due_at.is_(None))
            | (BusinessFollowUp.due_at >= now_utc)
        )

    # Ordering: pending follow-ups first, then by due_at asc (nulls last), id desc
    query = query.order_by(
        BusinessFollowUp.status == STATUS_COMPLETED,
        BusinessFollowUp.status == STATUS_CANCELLED,
        BusinessFollowUp.due_at.is_(None),
        BusinessFollowUp.due_at.asc(),
        BusinessFollowUp.id.desc(),
    )

    offset = max(0, (page - 1) * page_size)
    return query.offset(offset).limit(page_size).all()


def count_follow_ups(
    db: Session,
    business_id: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    overdue: Optional[bool] = None,
) -> int:
    """Count follow-ups matching filter criteria."""
    query = db.query(BusinessFollowUp)

    if business_id is not None:
        query = query.filter(BusinessFollowUp.business_id == business_id)

    if status:
        query = query.filter(BusinessFollowUp.status == status)

    if priority:
        query = query.filter(BusinessFollowUp.priority == priority)

    now_utc = datetime.now(timezone.utc)
    if overdue is True:
        query = query.filter(
            BusinessFollowUp.status == STATUS_PENDING,
            BusinessFollowUp.due_at.isnot(None),
            BusinessFollowUp.due_at < now_utc,
        )
    elif overdue is False:
        query = query.filter(
            (BusinessFollowUp.status != STATUS_PENDING)
            | (BusinessFollowUp.due_at.is_(None))
            | (BusinessFollowUp.due_at >= now_utc)
        )

    return query.count()


def update_follow_up(
    db: Session,
    follow_up_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    due_at: Optional[datetime] = None,
    priority: Optional[str] = None,
    commit: bool = True,
) -> Optional[BusinessFollowUp]:
    """
    Update follow-up fields idempotently.
    Logs follow_up_updated only if actual attributes change.
    """
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        return None

    changed_fields = []

    if title is not None:
        cleaned_title = title.strip()
        if cleaned_title and follow_up.title != cleaned_title:
            follow_up.title = cleaned_title
            changed_fields.append("title")

    if description is not None:
        cleaned_desc = description.strip() if description else None
        if follow_up.description != cleaned_desc:
            follow_up.description = cleaned_desc
            changed_fields.append("description")

    if due_at is not None and follow_up.due_at != due_at:
        follow_up.due_at = due_at
        changed_fields.append("due_at")

    if priority is not None:
        cleaned_priority = priority.lower()
        if cleaned_priority in VALID_FOLLOW_UP_PRIORITIES and follow_up.priority != cleaned_priority:
            follow_up.priority = cleaned_priority
            changed_fields.append("priority")

    if changed_fields:
        follow_up.updated_at = datetime.now(timezone.utc)
        meta = {
            "follow_up_id": follow_up.id,
            "title": follow_up.title,
            "priority": follow_up.priority,
            "status": follow_up.status,
            "due_at": follow_up.due_at.isoformat() if follow_up.due_at else None,
            "changes": changed_fields,
        }
        create_activity(
            db=db,
            business_id=follow_up.business_id,
            activity_type=ACTIVITY_FOLLOW_UP_UPDATED,
            title=f"Follow-up updated: {follow_up.title}",
            description=f"Status: {follow_up.status.capitalize()} | Priority: {follow_up.priority.capitalize()}",
            metadata=meta,
            commit=False,
        )
        if commit:
            db.commit()
            db.refresh(follow_up)

    return follow_up


def complete_follow_up(
    db: Session,
    follow_up_id: int,
    commit: bool = True,
) -> Optional[BusinessFollowUp]:
    """
    Mark a follow-up as completed.
    """
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        return None

    if follow_up.status != STATUS_COMPLETED:
        now = datetime.now(timezone.utc)
        follow_up.status = STATUS_COMPLETED
        follow_up.completed_at = now
        follow_up.updated_at = now

        meta = {
            "follow_up_id": follow_up.id,
            "title": follow_up.title,
            "completed_at": now.isoformat(),
        }
        create_activity(
            db=db,
            business_id=follow_up.business_id,
            activity_type=ACTIVITY_FOLLOW_UP_COMPLETED,
            title=f"Follow-up completed: {follow_up.title}",
            description="Marked as completed",
            metadata=meta,
            commit=False,
        )

        if commit:
            db.commit()
            db.refresh(follow_up)

    return follow_up


def cancel_follow_up(
    db: Session,
    follow_up_id: int,
    commit: bool = True,
) -> Optional[BusinessFollowUp]:
    """
    Mark a follow-up as cancelled.
    """
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        return None

    if follow_up.status != STATUS_CANCELLED:
        now = datetime.now(timezone.utc)
        follow_up.status = STATUS_CANCELLED
        follow_up.updated_at = now

        meta = {
            "follow_up_id": follow_up.id,
            "title": follow_up.title,
            "cancelled_at": now.isoformat(),
        }
        create_activity(
            db=db,
            business_id=follow_up.business_id,
            activity_type=ACTIVITY_FOLLOW_UP_CANCELLED,
            title=f"Follow-up cancelled: {follow_up.title}",
            description="Marked as cancelled",
            metadata=meta,
            commit=False,
        )

        if commit:
            db.commit()
            db.refresh(follow_up)

    return follow_up


def delete_follow_up(
    db: Session,
    follow_up_id: int,
    commit: bool = True,
) -> bool:
    """
    Delete a follow-up and record the deletion activity.
    """
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        return False

    business_id = follow_up.business_id
    follow_up_title = follow_up.title

    db.delete(follow_up)

    meta = {
        "follow_up_id": follow_up_id,
        "title": follow_up_title,
    }
    create_activity(
        db=db,
        business_id=business_id,
        activity_type=ACTIVITY_FOLLOW_UP_DELETED,
        title=f"Follow-up deleted: {follow_up_title}",
        description="Removed follow-up task",
        metadata=meta,
        commit=False,
    )

    if commit:
        db.commit()

    return True
