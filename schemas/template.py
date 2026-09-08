"""Pydantic schemas for Email Templates."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    subject: str = Field(..., min_length=1, max_length=500)
    body: str = Field(..., min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Name cannot be empty or whitespace only")
        return s

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Subject cannot be empty or whitespace only")
        return s

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Body cannot be empty or whitespace only")
        return s


class TemplateCreate(TemplateBase):
    pass


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    subject: Optional[str] = Field(None, min_length=1, max_length=500)
    body: Optional[str] = Field(None, min_length=1)
    is_archived: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Name cannot be empty or whitespace only")
            return s
        return v

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Subject cannot be empty or whitespace only")
            return s
        return v

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Body cannot be empty or whitespace only")
            return s
        return v


class TemplateArchiveToggleRequest(BaseModel):
    is_archived: bool


class TemplateOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    subject: str
    body: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TemplateSingleResponse(BaseModel):
    success: bool = True
    data: TemplateOut
    message: Optional[str] = None


class TemplateListResponse(BaseModel):
    success: bool = True
    items: List[TemplateOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class TemplatePreviewRequest(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    template_id: Optional[int] = None
    business_id: Optional[int] = None
    custom_context: Optional[Dict[str, Any]] = None


class TemplatePreviewResponse(BaseModel):
    success: bool = True
    rendered_subject: str
    rendered_body: str
    context_used: Dict[str, Any]
