"""
Lead Tags Service for Lead Finder.

Manages reusable custom tags, business-tag associations, bulk assignments,
and tag counts without N+1 overhead.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Sequence, Union

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database.models import Business, BusinessTag, Tag


MAX_TAG_NAME_LENGTH = 50


def validate_tag_name(name: Any) -> str:
    """
    Validate and clean tag name.
    
    Ensures name is non-empty, stripped of surrounding whitespace and control
    characters, within maximum length, and preserves all valid Unicode scripts
    (English, Gujarati, Hindi, Emoji, etc.).
    """
    if not name or not isinstance(name, str):
        raise ValueError("Tag name cannot be empty")

    # Replace newlines, tabs, and unprintable control characters with spaces
    cleaned = re.sub(r'[\r\n\t\x00-\x1f\x7f-\x9f]+', ' ', name).strip()
    # Collapse multiple consecutive spaces
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)

    if not cleaned:
        raise ValueError("Tag name cannot be empty")

    if len(cleaned) > MAX_TAG_NAME_LENGTH:
        raise ValueError(f"Tag name cannot exceed {MAX_TAG_NAME_LENGTH} characters")

    return cleaned


def slugify_tag(name: str) -> str:
    """
    Generate clean, URL-safe slug preserving Unicode characters.
    
    Example:
    'Hot Lead' -> 'hot-lead'
    '🔥 Priority' -> '🔥-priority'
    'ગુજરાતી' -> 'ગુજરાતી'
    """
    cleaned = re.sub(r'[\s_/\\]+', '-', name.strip()).lower()
    cleaned = re.sub(r'^-+|-+$', '', cleaned)
    return cleaned or "tag"


def get_tags(db: Session) -> List[Dict[str, Any]]:
    """
    Retrieve all tags with accurate business usage count in a single query.
    """
    rows = (
        db.query(
            Tag,
            func.count(BusinessTag.business_id).label("business_count"),
        )
        .outerjoin(BusinessTag, Tag.id == BusinessTag.tag_id)
        .group_by(Tag.id)
        .order_by(Tag.name.asc())
        .all()
    )

    return [
        {
            "id": tag.id,
            "name": tag.name,
            "slug": tag.slug,
            "created_at": tag.created_at,
            "updated_at": tag.updated_at,
            "business_count": int(count or 0),
        }
        for tag, count in rows
    ]


def get_tag_by_id(db: Session, tag_id: int) -> Optional[Tag]:
    """Look up tag by primary key ID."""
    return db.query(Tag).filter(Tag.id == tag_id).first()


def get_tag_by_name_or_slug(db: Session, identifier: str) -> Optional[Tag]:
    """Look up tag case-insensitively by name or slug."""
    clean = str(identifier).strip()
    slug = slugify_tag(clean)
    return (
        db.query(Tag)
        .filter(
            or_(
                func.lower(Tag.name) == clean.lower(),
                Tag.slug == slug,
            )
        )
        .first()
    )


def create_tag(db: Session, name: str) -> Tag:
    """
    Create a new custom tag. Raises ValueError if tag with same name/slug exists.
    """
    clean_name = validate_tag_name(name)
    slug = slugify_tag(clean_name)

    existing = (
        db.query(Tag)
        .filter(
            or_(
                func.lower(Tag.name) == clean_name.lower(),
                Tag.slug == slug,
            )
        )
        .first()
    )
    if existing:
        raise ValueError(f"Tag '{existing.name}' already exists")

    now = datetime.now(timezone.utc)
    tag = Tag(
        name=clean_name,
        slug=slug,
        created_at=now,
        updated_at=now,
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def rename_tag(db: Session, tag_id: int, new_name: str) -> Tag:
    """
    Rename an existing tag.
    """
    tag = get_tag_by_id(db, tag_id)
    if not tag:
        raise ValueError("Tag not found")

    clean_name = validate_tag_name(new_name)
    slug = slugify_tag(clean_name)

    # Check collision with other tags
    existing = (
        db.query(Tag)
        .filter(
            Tag.id != tag_id,
            or_(
                func.lower(Tag.name) == clean_name.lower(),
                Tag.slug == slug,
            ),
        )
        .first()
    )
    if existing:
        raise ValueError(f"Tag '{existing.name}' already exists")

    tag.name = clean_name
    tag.slug = slug
    tag.updated_at = datetime.now(timezone.utc)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, tag_id: int) -> bool:
    """
    Delete a tag and its associations. Does not delete any businesses.
    """
    tag = get_tag_by_id(db, tag_id)
    if not tag:
        return False

    db.delete(tag)
    db.commit()
    return True


def get_or_create_tag(db: Session, name: str) -> Tag:
    """
    Get existing tag by name/slug or create it if absent.
    """
    clean_name = validate_tag_name(name)
    existing = get_tag_by_name_or_slug(db, clean_name)
    if existing:
        return existing
    return create_tag(db, clean_name)


from services.activity_service import (
    ACTIVITY_TAG_ADDED,
    ACTIVITY_TAG_REMOVED,
    bulk_create_activities,
    create_activity,
)


def attach_tag_to_business(
    db: Session,
    business_id: int,
    tag_id: Optional[int] = None,
    name: Optional[str] = None,
) -> Tag:
    """
    Attach a tag to a single business.
    """
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        raise ValueError("Business not found")

    if tag_id is not None:
        tag = get_tag_by_id(db, tag_id)
        if not tag:
            raise ValueError("Tag not found")
    elif name is not None:
        tag = get_or_create_tag(db, name)
    else:
        raise ValueError("Either tag_id or tag name must be provided")

    # Check if already attached
    assoc = (
        db.query(BusinessTag)
        .filter(
            BusinessTag.business_id == business_id,
            BusinessTag.tag_id == tag.id,
        )
        .first()
    )
    if not assoc:
        assoc = BusinessTag(
            business_id=business_id,
            tag_id=tag.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(assoc)
        # Record activity
        create_activity(
            db=db,
            business_id=business_id,
            activity_type=ACTIVITY_TAG_ADDED,
            title="Tag added",
            description=f"Attached tag '{tag.name}'",
            metadata={"tag_id": tag.id, "tag_name": tag.name, "tag_slug": tag.slug},
            commit=False,
        )
        db.commit()

    db.refresh(business)
    return tag


def remove_tag_from_business(db: Session, business_id: int, tag_id: int) -> bool:
    """
    Remove a tag from a single business.
    """
    assoc = (
        db.query(BusinessTag)
        .filter(
            BusinessTag.business_id == business_id,
            BusinessTag.tag_id == tag_id,
        )
        .first()
    )
    if not assoc:
        return False

    tag = get_tag_by_id(db, tag_id)
    tag_name = tag.name if tag else str(tag_id)
    tag_slug = tag.slug if tag else ""

    db.delete(assoc)
    create_activity(
        db=db,
        business_id=business_id,
        activity_type=ACTIVITY_TAG_REMOVED,
        title="Tag removed",
        description=f"Removed tag '{tag_name}'",
        metadata={"tag_id": tag_id, "tag_name": tag_name, "tag_slug": tag_slug},
        commit=False,
    )
    db.commit()
    return True


def bulk_attach_tag(
    db: Session,
    business_ids: Sequence[int],
    tag_id: Optional[int] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Bulk attach a tag to multiple businesses in a single transaction.
    """
    unique_ids = list({int(b_id) for b_id in business_ids if b_id})
    if not unique_ids:
        return {"updated_count": 0, "total_requested": 0, "tag": None}

    if tag_id is not None:
        tag = get_tag_by_id(db, tag_id)
        if not tag:
            raise ValueError("Tag not found")
    elif name is not None:
        tag = get_or_create_tag(db, name)
    else:
        raise ValueError("Either tag_id or tag name must be provided")

    # Verify which businesses exist
    existing_business_ids = {
        row[0]
        for row in db.query(Business.id).filter(Business.id.in_(unique_ids)).all()
    }

    # Find which already have this tag
    already_tagged_ids = {
        row[0]
        for row in db.query(BusinessTag.business_id)
        .filter(
            BusinessTag.tag_id == tag.id,
            BusinessTag.business_id.in_(existing_business_ids),
        )
        .all()
    }

    to_add_ids = existing_business_ids - already_tagged_ids
    now = datetime.now(timezone.utc)

    for b_id in to_add_ids:
        db.add(
            BusinessTag(
                business_id=b_id,
                tag_id=tag.id,
                created_at=now,
            )
        )

    # Bulk create activities for newly attached tags
    activities_data = [
        {
            "business_id": b_id,
            "activity_type": ACTIVITY_TAG_ADDED,
            "title": "Tag added",
            "description": f"Attached tag '{tag.name}'",
            "metadata": {"tag_id": tag.id, "tag_name": tag.name, "tag_slug": tag.slug},
        }
        for b_id in to_add_ids
    ]
    bulk_create_activities(db, activities_data, commit=False)

    db.commit()

    return {
        "tag": {
            "id": tag.id,
            "name": tag.name,
            "slug": tag.slug,
        },
        "updated_count": len(to_add_ids),
        "total_requested": len(unique_ids),
    }


def bulk_remove_tag(
    db: Session,
    business_ids: Sequence[int],
    tag_id: int,
) -> Dict[str, Any]:
    """
    Bulk remove a tag from multiple businesses in a single transaction.
    """
    unique_ids = list({int(b_id) for b_id in business_ids if b_id})
    if not unique_ids:
        return {"removed_count": 0, "total_requested": 0}

    tag = get_tag_by_id(db, tag_id)
    if not tag:
        raise ValueError("Tag not found")

    # Find businesses that actually have this tag
    businesses_with_tag = [
        row[0]
        for row in db.query(BusinessTag.business_id)
        .filter(
            BusinessTag.tag_id == tag_id,
            BusinessTag.business_id.in_(unique_ids),
        )
        .all()
    ]

    deleted_count = (
        db.query(BusinessTag)
        .filter(
            BusinessTag.tag_id == tag_id,
            BusinessTag.business_id.in_(unique_ids),
        )
        .delete(synchronize_session=False)
    )

    # Bulk create activities for businesses from which tag was removed
    activities_data = [
        {
            "business_id": b_id,
            "activity_type": ACTIVITY_TAG_REMOVED,
            "title": "Tag removed",
            "description": f"Removed tag '{tag.name}'",
            "metadata": {"tag_id": tag.id, "tag_name": tag.name, "tag_slug": tag.slug},
        }
        for b_id in businesses_with_tag
    ]
    bulk_create_activities(db, activities_data, commit=False)

    db.commit()

    return {
        "removed_count": deleted_count,
        "total_requested": len(unique_ids),
    }
