"""
Pydantic Schemas for Follow-Up endpoints.
"""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.follow_up_service import (
    PRIORITY_MEDIUM,
    VALID_FOLLOW_UP_PRIORITIES,
    VALID_FOLLOW_UP_STATUSES,
    compute_is_overdue,
)


class FollowUpCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, description="Follow-up title or subject")
    description: Optional[str] = Field(None, max_length=5000, description="Optional details or context")
    due_at: Optional[datetime] = Field(None, description="ISO datetime when follow-up is due")
    priority: Optional[str] = Field(PRIORITY_MEDIUM, description="low | medium | high")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Title cannot be empty or whitespace only")
        return cleaned

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: Optional[str]) -> str:
        if v is None:
            return PRIORITY_MEDIUM
        cleaned = v.strip().lower()
        if cleaned not in VALID_FOLLOW_UP_PRIORITIES:
            raise ValueError(f"Invalid priority '{v}'. Allowed: {sorted(VALID_FOLLOW_UP_PRIORITIES)}")
        return cleaned


class FollowUpUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    due_at: Optional[datetime] = None
    priority: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("Title cannot be empty or whitespace only")
            return cleaned
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip().lower()
            if cleaned not in VALID_FOLLOW_UP_PRIORITIES:
                raise ValueError(f"Invalid priority '{v}'. Allowed: {sorted(VALID_FOLLOW_UP_PRIORITIES)}")
            return cleaned
        return v


class FollowUpOut(BaseModel):
    id: int
    business_id: int
    title: str
    description: Optional[str] = None
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str
    priority: str
    is_overdue: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_model(cls, follow_up) -> "FollowUpOut":
        is_overdue = compute_is_overdue(follow_up.due_at, follow_up.status)
        return cls(
            id=follow_up.id,
            business_id=follow_up.business_id,
            title=follow_up.title,
            description=follow_up.description,
            due_at=follow_up.due_at,
            completed_at=follow_up.completed_at,
            status=follow_up.status,
            priority=follow_up.priority,
            is_overdue=is_overdue,
            created_at=follow_up.created_at,
            updated_at=follow_up.updated_at,
        )


class FollowUpSingleResponse(BaseModel):
    success: bool = True
    data: FollowUpOut
    message: Optional[str] = None


class FollowUpListResponse(BaseModel):
    success: bool = True
    items: List[FollowUpOut]
    total: int
    page: int
    page_size: int
    total_pages: int
