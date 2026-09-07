"""
Pydantic Schemas for Business Notes System.
"""
from datetime import datetime
from typing import List
from pydantic import BaseModel, ConfigDict, Field, field_validator


class NoteCreateRequest(BaseModel):
    content: str = Field(
        ...,
        description="Note text content (1-5000 characters).",
    )

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Note content must be a string.")
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Note content cannot be empty or only whitespace.")
        if len(trimmed) > 5000:
            raise ValueError("Note content cannot exceed 5000 characters.")
        return trimmed


class NoteUpdateRequest(BaseModel):
    content: str = Field(
        ...,
        description="Updated note text content (1-5000 characters).",
    )

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Note content must be a string.")
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Note content cannot be empty or only whitespace.")
        if len(trimmed) > 5000:
            raise ValueError("Note content cannot exceed 5000 characters.")
        return trimmed


class NoteResponse(BaseModel):
    id: int
    business_id: int
    content: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NoteListResponse(BaseModel):
    success: bool = True
    data: List[NoteResponse] = Field(default_factory=list)
    total: int = 0
