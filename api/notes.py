"""
API endpoints for Business Notes management.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from database.db import get_db
from errors import AppError, ErrorCode
from schemas.common import DeletedCountResponse, VALIDATION_ERROR_RESPONSE
from schemas.note import (
    NoteCreateRequest,
    NoteListResponse,
    NoteResponse,
    NoteUpdateRequest,
)
from services.note_service import (
    create_business_note,
    delete_business_note,
    get_notes_for_business,
    update_business_note,
)

router = APIRouter(
    prefix="/businesses",
    tags=["Notes"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)


@router.get(
    "/{business_id}/notes",
    response_model=NoteListResponse,
    summary="List business notes",
    description="Returns all internal CRM notes attached to a business, ordered newest first.",
)
def list_business_notes(
    business_id: int,
    db: Session = Depends(get_db),
):
    notes = get_notes_for_business(db, business_id)
    return {
        "success": True,
        "data": notes,
        "total": len(notes),
    }


@router.post(
    "/{business_id}/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create note for business",
    description="Creates a new internal CRM note attached to a business.",
)
def add_note_to_business(
    business_id: int,
    body: NoteCreateRequest,
    db: Session = Depends(get_db),
):
    note = create_business_note(
        db,
        business_id=business_id,
        content=body.content,
    )
    if not note:
        raise AppError(
            message="Business not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return note


@router.patch(
    "/notes/{note_id}",
    response_model=NoteResponse,
    summary="Update business note",
    description="Updates the content of an existing internal CRM note.",
)
@router.patch(
    "/{business_id}/notes/{note_id}",
    response_model=NoteResponse,
    include_in_schema=False,
)
def edit_business_note(
    note_id: int,
    body: NoteUpdateRequest,
    db: Session = Depends(get_db),
    business_id: int = None,
):
    note = update_business_note(
        db,
        note_id=note_id,
        content=body.content,
    )
    if not note:
        raise AppError(
            message="Note not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return note


@router.delete(
    "/notes/{note_id}",
    response_model=DeletedCountResponse,
    summary="Delete business note",
    description="Deletes an internal CRM note.",
)
@router.delete(
    "/{business_id}/notes/{note_id}",
    response_model=DeletedCountResponse,
    include_in_schema=False,
)
def remove_business_note(
    note_id: int,
    db: Session = Depends(get_db),
    business_id: int = None,
):
    deleted = delete_business_note(db, note_id)
    if not deleted:
        raise AppError(
            message="Note not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return {"success": True, "deleted": 1}
