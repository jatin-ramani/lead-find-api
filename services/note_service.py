"""
Business Notes Service for Lead Finder.

Provides centralized operations for internal CRM notes attached to businesses.
Notes are private internal records and must never affect lead scoring, tags,
favorites, or other business metadata.
"""

from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session

from database.models import Business, BusinessNote


from services.activity_service import (
    ACTIVITY_NOTE_CREATED,
    ACTIVITY_NOTE_DELETED,
    ACTIVITY_NOTE_UPDATED,
    create_activity,
)


def get_business_by_id(db: Session, business_id: int) -> Optional[Business]:
    """Fetch a business by ID."""
    return db.query(Business).filter(Business.id == business_id).first()


def get_note_by_id(db: Session, note_id: int) -> Optional[BusinessNote]:
    """Fetch a single note by ID."""
    return db.query(BusinessNote).filter(BusinessNote.id == note_id).first()


def get_notes_for_business(
    db: Session,
    business_id: int,
) -> List[BusinessNote]:
    """
    Get all internal notes for a business, ordered newest first.
    Returns empty list if business has no notes.
    """
    return (
        db.query(BusinessNote)
        .filter(BusinessNote.business_id == business_id)
        .order_by(BusinessNote.created_at.desc(), BusinessNote.id.desc())
        .all()
    )


def get_business_notes_count(db: Session, business_id: int) -> int:
    """Return the total count of notes for a business."""
    return (
        db.query(BusinessNote)
        .filter(BusinessNote.business_id == business_id)
        .count()
    )


def create_business_note(
    db: Session,
    business_id: int,
    content: str,
) -> Optional[BusinessNote]:
    """
    Create a new note attached to a business.
    Returns None if the business does not exist.
    """
    business = get_business_by_id(db, business_id)
    if not business:
        return None

    cleaned_content = content.strip()
    now = datetime.now(timezone.utc)
    note = BusinessNote(
        business_id=business_id,
        content=cleaned_content,
        created_at=now,
        updated_at=now,
    )
    db.add(note)
    db.flush()

    # Record activity
    create_activity(
        db=db,
        business_id=business_id,
        activity_type=ACTIVITY_NOTE_CREATED,
        title="Note created",
        description="Added internal CRM note",
        metadata={"note_id": note.id},
        commit=False,
    )

    db.commit()
    db.refresh(note)
    return note


def update_business_note(
    db: Session,
    note_id: int,
    content: str,
) -> Optional[BusinessNote]:
    """
    Update the content of an existing note.
    Returns None if the note does not exist.
    """
    note = get_note_by_id(db, note_id)
    if not note:
        return None

    cleaned_content = content.strip()
    if note.content != cleaned_content:
        note.content = cleaned_content
        note.updated_at = datetime.now(timezone.utc)
        # Record activity
        create_activity(
            db=db,
            business_id=note.business_id,
            activity_type=ACTIVITY_NOTE_UPDATED,
            title="Note updated",
            description="Updated internal CRM note",
            metadata={"note_id": note.id},
            commit=False,
        )
        db.commit()
        db.refresh(note)

    return note


def delete_business_note(
    db: Session,
    note_id: int,
) -> bool:
    """
    Delete a business note by ID.
    Returns True if deleted, False if note did not exist.
    """
    note = get_note_by_id(db, note_id)
    if not note:
        return False

    business_id = note.business_id
    db.delete(note)
    # Record activity
    create_activity(
        db=db,
        business_id=business_id,
        activity_type=ACTIVITY_NOTE_DELETED,
        title="Note deleted",
        description="Deleted internal CRM note",
        metadata={"note_id": note_id},
        commit=False,
    )
    db.commit()
    return True
