import json
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, field_validator


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
        "json_schema_extra": {
            "example": {"business_ids": [5, 11, 18, 42]}
        }
    }


class ScrapeSelectedRequest(BulkBusinessRequest):
    """Body for POST /scrape/selected — additionally skips websiteless rows."""

    model_config = {
        "json_schema_extra": {
            "example": {"business_ids": [5, 11, 18, 42]}
        }
    }
