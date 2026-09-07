"""
Lead Favorites Service for Lead Finder.

Provides idempotent single and bulk operations to favorite/unfavorite businesses
without altering lead scoring, lead grades, tags, or other business metadata.
"""

from typing import Any, Dict, Optional, Sequence
from sqlalchemy.orm import Session

from database.models import Business


from services.activity_service import (
    ACTIVITY_FAVORITE_ADDED,
    ACTIVITY_FAVORITE_REMOVED,
    bulk_create_activities,
    create_activity,
)


def get_business_by_id(db: Session, business_id: int) -> Optional[Business]:
    """Fetch a business by ID."""
    return db.query(Business).filter(Business.id == business_id).first()


def set_business_favorite(
    db: Session,
    business_id: int,
    is_favorite: bool,
) -> Optional[Business]:
    """
    Set the favorite status of a single business idempotently.
    Returns None if the business does not exist.
    """
    business = get_business_by_id(db, business_id)
    if not business:
        return None

    if bool(business.is_favorite) != bool(is_favorite):
        business.is_favorite = is_favorite
        act_type = ACTIVITY_FAVORITE_ADDED if is_favorite else ACTIVITY_FAVORITE_REMOVED
        title = "Favorite added" if is_favorite else "Favorite removed"
        desc = "Lead marked as favorite" if is_favorite else "Lead removed from favorites"
        create_activity(
            db=db,
            business_id=business_id,
            activity_type=act_type,
            title=title,
            description=desc,
            metadata={"is_favorite": is_favorite},
            commit=False,
        )
        db.commit()
        db.refresh(business)

    return business


def toggle_business_favorite(
    db: Session,
    business_id: int,
) -> Optional[Business]:
    """
    Toggle the favorite status of a single business.
    Returns None if the business does not exist.
    """
    business = get_business_by_id(db, business_id)
    if not business:
        return None

    new_fav = not bool(business.is_favorite)
    return set_business_favorite(db, business_id, new_fav)


def bulk_set_favorites(
    db: Session,
    business_ids: Sequence[int],
    is_favorite: bool,
) -> Dict[str, Any]:
    """
    Bulk update favorite status for a sequence of business IDs in a single transaction.
    Non-existent IDs and duplicates are handled cleanly without error.
    """
    unique_ids = list({int(b_id) for b_id in business_ids if b_id})
    if not unique_ids:
        return {
            "updated_count": 0,
            "total_requested": 0,
            "is_favorite": is_favorite,
        }

    # Query businesses that actually change
    businesses_to_update = (
        db.query(Business.id)
        .filter(
            Business.id.in_(unique_ids),
            Business.is_favorite != is_favorite,
        )
        .all()
    )

    if not businesses_to_update:
        return {
            "updated_count": 0,
            "total_requested": len(unique_ids),
            "is_favorite": is_favorite,
        }

    target_ids = [row[0] for row in businesses_to_update]

    # Execute update in a single atomic SQL statement
    updated_count = (
        db.query(Business)
        .filter(Business.id.in_(target_ids))
        .update(
            {"is_favorite": is_favorite},
            synchronize_session=False,
        )
    )

    # Bulk create activities
    act_type = ACTIVITY_FAVORITE_ADDED if is_favorite else ACTIVITY_FAVORITE_REMOVED
    title = "Favorite added" if is_favorite else "Favorite removed"
    desc = "Lead marked as favorite" if is_favorite else "Lead removed from favorites"
    activities_data = [
        {
            "business_id": b_id,
            "activity_type": act_type,
            "title": title,
            "description": desc,
            "metadata": {"is_favorite": is_favorite},
        }
        for b_id in target_ids
    ]
    bulk_create_activities(db, activities_data, commit=False)

    db.commit()

    return {
        "updated_count": updated_count,
        "total_requested": len(unique_ids),
        "is_favorite": is_favorite,
    }


def get_favorite_count(db: Session) -> int:
    """Return the total count of favorited businesses."""
    return db.query(Business).filter(Business.is_favorite.is_(True)).count()
