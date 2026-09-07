"""Pydantic schemas for Email Automations."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AutomationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    trigger_type: str = Field(..., min_length=1, max_length=50)
    subject_template: str = Field(..., min_length=1, max_length=500)
    body_template: str = Field(..., min_length=1)
    enabled: bool = Field(True, description="Whether automation is active")
    delay_minutes: int = Field(0, ge=0, le=10080)  # Max 7 days in minutes
    max_retries: int = Field(3, ge=0, le=10)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Name cannot be empty or whitespace only")
        return s

    @field_validator("subject_template")
    @classmethod
    def validate_subject(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Subject template cannot be empty or whitespace only")
        return s

    @field_validator("body_template")
    @classmethod
    def validate_body(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Body template cannot be empty or whitespace only")
        return s

    @field_validator("trigger_type")
    @classmethod
    def validate_trigger(cls, v: str) -> str:
        allowed = {"lead_created", "lead_status_changed", "follow_up_due", "follow_up_overdue"}
        if v not in allowed:
            raise ValueError(f"trigger_type must be one of {allowed}")
        return v


class AutomationCreate(AutomationBase):
    pass


class AutomationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    trigger_type: Optional[str] = Field(None, min_length=1, max_length=50)
    subject_template: Optional[str] = Field(None, min_length=1, max_length=500)
    body_template: Optional[str] = Field(None, min_length=1)
    enabled: Optional[bool] = None
    delay_minutes: Optional[int] = Field(None, ge=0, le=10080)
    max_retries: Optional[int] = Field(None, ge=0, le=10)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Name cannot be empty or whitespace only")
            return s
        return v

    @field_validator("subject_template")
    @classmethod
    def validate_subject(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Subject template cannot be empty or whitespace only")
            return s
        return v

    @field_validator("body_template")
    @classmethod
    def validate_body(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Body template cannot be empty or whitespace only")
            return s
        return v

    @field_validator("trigger_type")
    @classmethod
    def validate_trigger(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            allowed = {"lead_created", "lead_status_changed", "follow_up_due", "follow_up_overdue"}
            if v not in allowed:
                raise ValueError(f"trigger_type must be one of {allowed}")
        return v


class AutomationToggleRequest(BaseModel):
    enabled: bool


class AutomationOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    trigger_type: str
    subject_template: str
    body_template: str
    enabled: bool
    delay_minutes: int
    max_retries: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AutomationSingleResponse(BaseModel):
    success: bool = True
    data: AutomationOut
    message: Optional[str] = None


class AutomationListResponse(BaseModel):
    success: bool = True
    items: List[AutomationOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class AutomationExecutionOut(BaseModel):
    id: int
    automation_id: int
    business_id: int
    follow_up_id: Optional[int] = None
    status: str
    trigger_event: str
    trigger_key: str
    recipient_email: str
    rendered_subject: Optional[str] = None
    rendered_body: Optional[str] = None
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int
    next_retry_at: Optional[datetime] = None
    scheduled_at: datetime
    sent_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExecutionListResponse(BaseModel):
    success: bool = True
    items: List[AutomationExecutionOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class SupportedVariableResponse(BaseModel):
    key: str
    label: str
    description: str
    example: Optional[str] = None


class ProcessDueResponse(BaseModel):
    success: bool = True
    processed: int = 0
    sent: int = 0
    failed: int = 0
    retried: int = 0
    cancelled: int = 0
