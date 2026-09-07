"""
API endpoints for Business Activities / History timeline.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import Business
from errors import AppError, ErrorCode
from schemas.activity import BusinessActivityListResponse, BusinessActivityOut
from schemas.common import VALIDATION_ERROR_RESPONSE
from services.activity_service import (
    VALID_ACTIVITY_TYPES,
    get_business_activities,
    get_business_activity_count,
)

router = APIRouter(
    prefix="/businesses",
    tags=["Activities"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)


@router.get(
    "/{business_id}/activities",
    response_model=BusinessActivityListResponse,
    summary="List business activities",
    description="Returns chronological audit and activity history records for a business, ordered newest first.",
)
def list_business_activities(
    business_id: int,
    page: int = Query(1, ge=1, description="Page number starting at 1"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    activity_type: Optional[str] = Query(None, description="Optional activity type filter"),
    db: Session = Depends(get_db),
):
    if activity_type is not None and activity_type not in VALID_ACTIVITY_TYPES:
        raise AppError(
            message=f"Invalid activity_type '{activity_type}'",
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )

    # Verify business existence
    business = db.query(Business.id).filter(Business.id == business_id).first()
    if not business:
        raise AppError(
            message="Business not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    activities = get_business_activities(
        db,
        business_id=business_id,
        page=page,
        page_size=page_size,
        activity_type=activity_type,
    )
    total = get_business_activity_count(
        db,
        business_id=business_id,
        activity_type=activity_type,
    )

    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    items = [BusinessActivityOut.from_orm_model(a) for a in activities]

    return {
        "success": True,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
