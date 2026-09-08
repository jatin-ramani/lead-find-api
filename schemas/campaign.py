"""Pydantic schemas for Email Campaigns and Campaign Recipients."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CampaignFilterCriteria(BaseModel):
    search: Optional[str] = None
    city: Optional[str] = None
    category: Optional[str] = None
    has_website: Optional[bool] = None
    has_email: Optional[bool] = None
    has_phone: Optional[bool] = None
    lead_grade: Optional[str] = None
    min_lead_score: Optional[int] = None
    max_lead_score: Optional[int] = None
    tags: Optional[str] = None
    is_favorite: Optional[bool] = None
    lead_status: Optional[str] = None
    business_ids: Optional[List[int]] = None


class CampaignBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    template_id: int = Field(..., description="ID of the EmailTemplate to use")
    filter_criteria: Optional[CampaignFilterCriteria] = Field(default_factory=CampaignFilterCriteria)
    scheduled_at: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Campaign name cannot be empty or whitespace only")
        return s


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    template_id: Optional[int] = None
    filter_criteria: Optional[CampaignFilterCriteria] = None
    scheduled_at: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Campaign name cannot be empty or whitespace only")
            return s
        return v


class CampaignOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    template_id: int
    template_name: Optional[str] = None
    status: str
    filter_criteria: Dict[str, Any]
    recipient_count: int
    sent_count: int
    failed_count: int
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    snapshot_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CampaignSingleResponse(BaseModel):
    success: bool = True
    data: CampaignOut
    message: Optional[str] = None


class CampaignListResponse(BaseModel):
    success: bool = True
    items: List[CampaignOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class CampaignRecipientOut(BaseModel):
    id: int
    campaign_id: int
    business_id: int
    business_name: Optional[str] = None
    recipient_email: str
    recipient_name: Optional[str] = None
    status: str
    attempt_count: int
    error_message: Optional[str] = None
    provider_message_id: Optional[str] = None
    sent_at: Optional[datetime] = None
    attempted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CampaignRecipientListResponse(BaseModel):
    success: bool = True
    items: List[CampaignRecipientOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class RecipientPreviewResponse(BaseModel):
    success: bool = True
    total_eligible_leads: int
    sample_leads: List[Dict[str, Any]]


class ProcessDueCampaignsResponse(BaseModel):
    success: bool = True
    campaigns_processed: int = 0
    recipients_sent: int = 0
    recipients_failed: int = 0
    campaigns_completed: int = 0
