"""
API endpoints for Tag management and Business-Tag assignments.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database.db import get_db
from errors import AppError, ErrorCode
from schemas.common import DeletedCountResponse, MessageResponse, VALIDATION_ERROR_RESPONSE
from schemas.tag import (
    BulkBusinessTagRemoveRequest,
    BulkBusinessTagRequest,
    BulkTagActionResponse,
    BusinessTagAssignRequest,
    TagCreateRequest,
    TagListResponse,
    TagResponse,
    TagUpdateRequest,
)
from services.tag_service import (
    attach_tag_to_business,
    bulk_attach_tag,
    bulk_remove_tag,
    create_tag,
    delete_tag,
    get_tags,
    get_tag_by_id,
    remove_tag_from_business,
    rename_tag,
)

router = APIRouter(
    prefix="/tags",
    tags=["Tags"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)

business_tags_router = APIRouter(
    prefix="/businesses",
    tags=["Businesses"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)


@router.get(
    "",
    response_model=TagListResponse,
    summary="List all tags",
    description="Returns all custom lead tags with their respective business usage counts.",
)
def list_tags(db: Session = Depends(get_db)):
    tags = get_tags(db)
    return {
        "success": True,
        "data": tags,
    }


@router.post(
    "",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create tag",
    description="Creates a new custom lead tag.",
)
def create_new_tag(
    body: TagCreateRequest,
    db: Session = Depends(get_db),
):
    try:
        tag = create_tag(db, body.name)
        return tag
    except ValueError as e:
        is_conflict = "already exists" in str(e).lower()
        raise AppError(
            message=str(e),
            error=ErrorCode.CONFLICT if is_conflict else ErrorCode.VALIDATION_ERROR,
            status_code=409 if is_conflict else 400,
        )


@router.patch(
    "/{tag_id}",
    response_model=TagResponse,
    summary="Rename tag",
    description="Renames an existing tag.",
)
def update_tag(
    tag_id: int,
    body: TagUpdateRequest,
    db: Session = Depends(get_db),
):
    try:
        tag = rename_tag(db, tag_id, body.name)
        return tag
    except ValueError as e:
        if "not found" in str(e).lower():
            raise AppError(message="Tag not found", error=ErrorCode.NOT_FOUND, status_code=404)
        if "already exists" in str(e).lower():
            raise AppError(message=str(e), error=ErrorCode.CONFLICT, status_code=409)
        raise AppError(message=str(e), error=ErrorCode.VALIDATION_ERROR, status_code=400)


@router.delete(
    "/{tag_id}",
    response_model=DeletedCountResponse,
    summary="Delete tag",
    description="Deletes a tag and removes all its associations from businesses.",
)
def remove_tag(
    tag_id: int,
    db: Session = Depends(get_db),
):
    deleted = delete_tag(db, tag_id)
    if not deleted:
        raise AppError(message="Tag not found", error=ErrorCode.NOT_FOUND, status_code=404)
    return {"success": True, "deleted": 1}


@business_tags_router.post(
    "/{business_id}/tags",
    response_model=TagResponse,
    summary="Attach tag to business",
    description="Attaches an existing or newly named tag to a single business.",
)
def add_business_tag(
    business_id: int,
    body: BusinessTagAssignRequest,
    db: Session = Depends(get_db),
):
    try:
        tag = attach_tag_to_business(
            db,
            business_id=business_id,
            tag_id=body.tag_id,
            name=body.name,
        )
        return tag
    except ValueError as e:
        if "business not found" in str(e).lower():
            raise AppError(message="Business not found", error=ErrorCode.NOT_FOUND, status_code=404)
        if "tag not found" in str(e).lower():
            raise AppError(message="Tag not found", error=ErrorCode.NOT_FOUND, status_code=404)
        raise AppError(message=str(e), error=ErrorCode.VALIDATION_ERROR, status_code=400)


@business_tags_router.delete(
    "/{business_id}/tags/{tag_id}",
    response_model=DeletedCountResponse,
    summary="Remove tag from business",
    description="Detaches a tag from a single business.",
)
def delete_business_tag(
    business_id: int,
    tag_id: int,
    db: Session = Depends(get_db),
):
    removed = remove_tag_from_business(db, business_id=business_id, tag_id=tag_id)
    return {"success": True, "deleted": 1 if removed else 0}


@business_tags_router.post(
    "/tags/bulk",
    response_model=BulkTagActionResponse,
    summary="Bulk attach tag",
    description="Attaches a tag to multiple businesses in one transaction.",
)
def bulk_attach_business_tags(
    body: BulkBusinessTagRequest,
    db: Session = Depends(get_db),
):
    try:
        result = bulk_attach_tag(
            db,
            business_ids=body.business_ids,
            tag_id=body.tag_id,
            name=body.tag_name,
        )
        return {
            "success": True,
            "message": f"Tag applied to {result['updated_count']} businesses.",
            "tag": result["tag"],
            "updated_count": result["updated_count"],
            "total_requested": result["total_requested"],
        }
    except ValueError as e:
        raise AppError(message=str(e), error=ErrorCode.VALIDATION_ERROR, status_code=400)


@business_tags_router.post(
    "/tags/bulk-remove",
    response_model=BulkTagActionResponse,
    summary="Bulk remove tag",
    description="Removes a tag from multiple businesses in one transaction.",
)
def bulk_remove_business_tags(
    body: BulkBusinessTagRemoveRequest,
    db: Session = Depends(get_db),
):
    try:
        result = bulk_remove_tag(
            db,
            business_ids=body.business_ids,
            tag_id=body.tag_id,
        )
        return {
            "success": True,
            "message": f"Tag removed from {result['removed_count']} businesses.",
            "tag": None,
            "updated_count": result["removed_count"],
            "total_requested": result["total_requested"],
        }
    except ValueError as e:
        raise AppError(message=str(e), error=ErrorCode.VALIDATION_ERROR, status_code=400)
