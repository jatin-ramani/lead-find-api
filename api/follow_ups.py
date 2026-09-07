"""
API endpoints for CRM Follow-ups.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import Business
from errors import AppError, ErrorCode
from schemas.common import VALIDATION_ERROR_RESPONSE
from schemas.follow_up import (
    FollowUpCreate,
    FollowUpListResponse,
    FollowUpOut,
    FollowUpSingleResponse,
    FollowUpUpdate,
)
from services.follow_up_service import (
    VALID_FOLLOW_UP_PRIORITIES,
    VALID_FOLLOW_UP_STATUSES,
    cancel_follow_up,
    complete_follow_up,
    count_follow_ups,
    create_follow_up,
    delete_follow_up,
    get_follow_up,
    list_follow_ups,
    update_follow_up,
)

router = APIRouter(
    tags=["Follow-ups"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)


# ============================================================================
# Business-scoped Endpoints
# ============================================================================

@router.post(
    "/businesses/{business_id}/follow-ups",
    response_model=FollowUpSingleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a follow-up for a business",
    description="Create a new scheduled follow-up or reminder for a specific business.",
)
def create_business_follow_up(
    business_id: int,
    payload: FollowUpCreate,
    db: Session = Depends(get_db),
):
    business = db.query(Business.id).filter(Business.id == business_id).first()
    if not business:
        raise AppError(
            message="Business not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    follow_up = create_follow_up(
        db=db,
        business_id=business_id,
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
        priority=payload.priority or "medium",
        commit=True,
    )
    if not follow_up:
        raise AppError(
            message="Failed to create follow-up",
            error=ErrorCode.INTERNAL_ERROR,
            status_code=500,
        )

    return {
        "success": True,
        "data": FollowUpOut.from_orm_model(follow_up),
        "message": "Follow-up created successfully",
    }


@router.get(
    "/businesses/{business_id}/follow-ups",
    response_model=FollowUpListResponse,
    summary="List follow-ups for a business",
    description="Retrieve paginated follow-ups for a specific business with optional status and priority filtering.",
)
def list_business_follow_ups_endpoint(
    business_id: int,
    status_filter: Optional[str] = Query(None, alias="status", description="pending | completed | cancelled"),
    priority_filter: Optional[str] = Query(None, alias="priority", description="low | medium | high"),
    overdue: Optional[bool] = Query(None, description="Filter by overdue status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    business = db.query(Business.id).filter(Business.id == business_id).first()
    if not business:
        raise AppError(
            message="Business not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    if status_filter and status_filter not in VALID_FOLLOW_UP_STATUSES:
        raise AppError(
            message=f"Invalid status '{status_filter}'",
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )

    if priority_filter and priority_filter not in VALID_FOLLOW_UP_PRIORITIES:
        raise AppError(
            message=f"Invalid priority '{priority_filter}'",
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )

    items = list_follow_ups(
        db=db,
        business_id=business_id,
        status=status_filter,
        priority=priority_filter,
        overdue=overdue,
        page=page,
        page_size=page_size,
    )
    total = count_follow_ups(
        db=db,
        business_id=business_id,
        status=status_filter,
        priority=priority_filter,
        overdue=overdue,
    )
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1

    return {
        "success": True,
        "items": [FollowUpOut.from_orm_model(f) for f in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# ============================================================================
# Global / Direct Follow-Up Endpoints
# ============================================================================

@router.get(
    "/follow-ups",
    response_model=FollowUpListResponse,
    summary="List all follow-ups across CRM",
    description="Retrieve paginated follow-ups across all businesses with multi-criteria filtering.",
)
def list_all_follow_ups_endpoint(
    business_id: Optional[int] = Query(None, description="Filter by business ID"),
    status_filter: Optional[str] = Query(None, alias="status"),
    priority_filter: Optional[str] = Query(None, alias="priority"),
    overdue: Optional[bool] = Query(None, description="Filter by overdue status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if status_filter and status_filter not in VALID_FOLLOW_UP_STATUSES:
        raise AppError(
            message=f"Invalid status '{status_filter}'",
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )

    if priority_filter and priority_filter not in VALID_FOLLOW_UP_PRIORITIES:
        raise AppError(
            message=f"Invalid priority '{priority_filter}'",
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )

    items = list_follow_ups(
        db=db,
        business_id=business_id,
        status=status_filter,
        priority=priority_filter,
        overdue=overdue,
        page=page,
        page_size=page_size,
    )
    total = count_follow_ups(
        db=db,
        business_id=business_id,
        status=status_filter,
        priority=priority_filter,
        overdue=overdue,
    )
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1

    return {
        "success": True,
        "items": [FollowUpOut.from_orm_model(f) for f in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get(
    "/follow-ups/{follow_up_id}",
    response_model=FollowUpSingleResponse,
    summary="Get single follow-up details",
    description="Fetch a single CRM follow-up record by its primary ID.",
)
def get_single_follow_up_endpoint(
    follow_up_id: int,
    db: Session = Depends(get_db),
):
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        raise AppError(
            message="Follow-up not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    return {
        "success": True,
        "data": FollowUpOut.from_orm_model(follow_up),
    }


@router.patch(
    "/follow-ups/{follow_up_id}",
    response_model=FollowUpSingleResponse,
    summary="Update follow-up attributes",
    description="Update title, description, due date, or priority for a follow-up.",
)
def update_follow_up_endpoint(
    follow_up_id: int,
    payload: FollowUpUpdate,
    db: Session = Depends(get_db),
):
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        raise AppError(
            message="Follow-up not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    updated = update_follow_up(
        db=db,
        follow_up_id=follow_up_id,
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
        priority=payload.priority,
        commit=True,
    )
    return {
        "success": True,
        "data": FollowUpOut.from_orm_model(updated),
        "message": "Follow-up updated successfully",
    }


@router.post(
    "/follow-ups/{follow_up_id}/complete",
    response_model=FollowUpSingleResponse,
    summary="Mark follow-up as completed",
    description="Mark an existing follow-up as completed and stamp completion timestamp.",
)
def complete_follow_up_endpoint(
    follow_up_id: int,
    db: Session = Depends(get_db),
):
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        raise AppError(
            message="Follow-up not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    completed = complete_follow_up(db, follow_up_id, commit=True)
    return {
        "success": True,
        "data": FollowUpOut.from_orm_model(completed),
        "message": "Follow-up marked as completed",
    }


@router.post(
    "/follow-ups/{follow_up_id}/cancel",
    response_model=FollowUpSingleResponse,
    summary="Mark follow-up as cancelled",
    description="Mark an existing follow-up as cancelled.",
)
def cancel_follow_up_endpoint(
    follow_up_id: int,
    db: Session = Depends(get_db),
):
    follow_up = get_follow_up(db, follow_up_id)
    if not follow_up:
        raise AppError(
            message="Follow-up not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    cancelled = cancel_follow_up(db, follow_up_id, commit=True)
    return {
        "success": True,
        "data": FollowUpOut.from_orm_model(cancelled),
        "message": "Follow-up marked as cancelled",
    }


@router.delete(
    "/follow-ups/{follow_up_id}",
    summary="Delete a follow-up",
    description="Permanently remove a CRM follow-up record.",
)
def delete_follow_up_endpoint(
    follow_up_id: int,
    db: Session = Depends(get_db),
):
    success = delete_follow_up(db, follow_up_id, commit=True)
    if not success:
        raise AppError(
            message="Follow-up not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )

    return {
        "success": True,
        "message": "Follow-up deleted successfully",
    }
