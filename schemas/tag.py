from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class TagResponse(BaseModel):
    id: int
    name: str
    slug: str
    business_count: Optional[int] = 0
    created_at: Optional[datetime] = None

    model_config = {
        "from_attributes": True
    }


class TagListResponse(BaseModel):
    success: bool = True
    data: List[TagResponse]


class TagCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="Tag name")


class TagUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="New tag name")


class BusinessTagAssignRequest(BaseModel):
    tag_id: Optional[int] = None
    name: Optional[str] = Field(None, max_length=50, description="Tag name (auto-creates if absent)")


class BulkBusinessTagRequest(BaseModel):
    business_ids: List[int] = Field(..., min_length=1, description="List of business IDs")
    tag_id: Optional[int] = None
    tag_name: Optional[str] = Field(None, max_length=50, description="Tag name (auto-creates if absent)")


class BulkBusinessTagRemoveRequest(BaseModel):
    business_ids: List[int] = Field(..., min_length=1, description="List of business IDs")
    tag_id: int = Field(..., description="Tag ID to remove from businesses")


class BulkTagActionResponse(BaseModel):
    success: bool = True
    message: str
    tag: Optional[TagResponse] = None
    updated_count: int = 0
    total_requested: int = 0
