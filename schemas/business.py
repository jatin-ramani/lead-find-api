import json
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from schemas.tag import TagResponse


class BusinessResponse(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    city: Optional[str] = None
    category: Optional[str] = None
    address: Optional[str] = None
    status: Optional[str] = None
    lead_score: Optional[int] = 0
    lead_grade: Optional[str] = "D"
    lead_score_reasons: Optional[List[str]] = None
    lead_status: Optional[str] = "new"
    is_favorite: bool = False
    tags: List[TagResponse] = []

    model_config = {
        "from_attributes": True
    }


class PaginationResponse(BaseModel):
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


class BusinessListResponse(BaseModel):
    success: bool
    data: List[BusinessResponse]
    pagination: PaginationResponse


class WebsiteDataResponse(BaseModel):
    id: int
    business_id: int

    title: Optional[str] = None
    meta_description: Optional[str] = None

    emails: List[str] = []

    facebook: Optional[str] = None
    instagram: Optional[str] = None
    linkedin: Optional[str] = None
    twitter: Optional[str] = None
    youtube: Optional[str] = None
    whatsapp: Optional[str] = None

    scraped_at: Optional[datetime] = None
    status: Optional[str] = None

    model_config = {
        "from_attributes": True
    }

    @field_validator("emails", mode="before")
    @classmethod
    def _decode_emails(cls, value: Any) -> List[str]:
        """
        The column stores a JSON string; this hands the caller a real list.

        Anything unreadable degrades to an empty list rather than failing the
        whole response, since a bad value in one row should not 500 the route.
        """

        if value is None:
            return []

        if isinstance(value, list):
            return value

        if not isinstance(value, str):
            return []

        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return []

        if not isinstance(decoded, list):
            return []

        return [str(item) for item in decoded]


class WebsiteDataDetailResponse(BaseModel):
    success: bool
    data: WebsiteDataResponse


class BulkBusinessRequest(BaseModel):
    """
    Body for endpoints that act on a set of businesses.

    Duplicates and unknown ids are filtered out downstream rather than rejected
    here, so a partly-stale selection from the UI still acts on whatever
    remains valid.
    """

    business_ids: List[int]

    model_config = {
        "json_schema_extra": {"example": {"business_ids": [5, 11, 18, 42]}}
    }


class FavoriteToggleRequest(BaseModel):
    """Body for setting or toggling favorite state on a business."""

    is_favorite: bool = True


class BulkFavoriteRequest(BaseModel):
    """Body for bulk setting favorite status across multiple businesses."""

    business_ids: List[int]
    is_favorite: bool = True

    model_config = {
        "json_schema_extra": {
            "example": {"business_ids": [5, 11, 18, 42], "is_favorite": True}
        }
    }


class BulkFavoriteResponse(BaseModel):
    success: bool = True
    message: str = "Favorites updated"
    updated_count: int
    total_requested: int
    is_favorite: bool


class LeadStatusUpdateRequest(BaseModel):
    """Body for updating CRM lead status on a single business."""

    status: str = Field(
        ...,
        description="CRM lead status (new, contacted, interested, follow_up, converted, lost).",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Status must be a string.")
        if v not in ("new", "contacted", "interested", "follow_up", "converted", "lost"):
            raise ValueError(f"Invalid lead status '{v}'. Allowed statuses: new, contacted, interested, follow_up, converted, lost.")
        return v


class BulkLeadStatusRequest(BaseModel):
    """Body for updating CRM lead status across multiple businesses."""

    business_ids: List[int]
    status: str = Field(
        ...,
        description="Target lead status (new, contacted, interested, follow_up, converted, lost).",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Status must be a string.")
        if v not in ("new", "contacted", "interested", "follow_up", "converted", "lost"):
            raise ValueError(f"Invalid lead status '{v}'. Allowed statuses: new, contacted, interested, follow_up, converted, lost.")
        return v

    model_config = {
        "json_schema_extra": {
            "example": {"business_ids": [5, 11, 18, 42], "status": "contacted"}
        }
    }


class BulkLeadStatusResponse(BaseModel):
    success: bool = True
    message: str = "Lead status updated"
    updated_count: int
    total_requested: int
    status: str


class ContactQualification(BaseModel):
    """Additional positive contact requirements applied with AND semantics."""

    has_email: bool = False
    has_phone: bool = False


class SelectedExportRequest(BulkBusinessRequest, ContactQualification):
    """Selected ids plus explicit server-side contact qualification."""


VALID_FILTER_GRADES = {"A", "B", "C", "D"}
VALID_FILTER_STATUSES = {"new", "contacted", "interested", "follow_up", "converted", "lost"}


class BusinessFilterRequest(BaseModel):
    """The canonical filter contract used by list, count, preview, and filtered CSV endpoints."""

    search: Optional[str] = None
    city: Optional[str] = None
    category: Optional[str] = None
    has_website: Optional[bool] = None
    has_email: Optional[bool] = None
    has_phone: Optional[bool] = None
    lead_grade: Optional[str] = None
    min_lead_score: Optional[int] = Field(None, ge=0, le=100, description="Minimum lead score (0-100).")
    max_lead_score: Optional[int] = Field(None, ge=0, le=100, description="Maximum lead score (0-100).")
    tags: Optional[str] = None
    is_favorite: Optional[bool] = None
    lead_status: Optional[str] = None

    @field_validator("lead_grade")
    @classmethod
    def validate_lead_grade(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("Lead grade must be a string.")
        cleaned = v.strip().upper()
        if not cleaned:
            return None
        if cleaned not in VALID_FILTER_GRADES:
            raise ValueError(f"Invalid lead grade '{v}'. Allowed values: A, B, C, D.")
        return cleaned

    @field_validator("lead_status")
    @classmethod
    def validate_lead_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("Lead status must be a string.")
        cleaned = v.strip().lower()
        if not cleaned:
            return None
        if cleaned not in VALID_FILTER_STATUSES:
            raise ValueError(
                f"Invalid lead status '{v}'. Allowed values: new, contacted, interested, follow_up, converted, lost."
            )
        return cleaned

    @model_validator(mode="after")
    def validate_score_range(self) -> "BusinessFilterRequest":
        if self.min_lead_score is not None and self.max_lead_score is not None:
            if self.min_lead_score > self.max_lead_score:
                raise ValueError(
                    f"min_lead_score ({self.min_lead_score}) cannot be greater than max_lead_score ({self.max_lead_score})."
                )
        return self


class ExportPreviewRequest(BaseModel):
    """Authoritative count request for filtered or selected export scope."""

    scope: Literal["filtered", "selected"]
    business_ids: List[int] = []
    filters: BusinessFilterRequest = BusinessFilterRequest()
    qualification: ContactQualification = ContactQualification()


class ExportPreviewResponse(BaseModel):
    success: bool = True
    total_selected: int
    matching_qualification: int
    export_count: int


class ScrapeSelectedRequest(BulkBusinessRequest):
    """Body for POST /scrape/selected — additionally skips websiteless rows."""

    model_config = {
        "json_schema_extra": {
            "example": {"business_ids": [5, 11, 18, 42]}
        }
    }
