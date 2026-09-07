"""Pydantic schemas for Business Activities."""

from datetime import datetime
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class BusinessActivityOut(BaseModel):
    id: int
    business_id: int
    activity_type: str
    title: str
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_model(cls, model: Any) -> "BusinessActivityOut":
        """Parse metadata JSON string into dict if needed."""
        meta: Dict[str, Any] = {}
        if hasattr(model, "metadata_json") and model.metadata_json:
            try:
                if isinstance(model.metadata_json, dict):
                    meta = model.metadata_json
                else:
                    meta = json.loads(model.metadata_json)
            except Exception:
                meta = {}
        elif hasattr(model, "metadata") and model.metadata:
            try:
                if isinstance(model.metadata, dict):
                    meta = model.metadata
                else:
                    meta = json.loads(model.metadata)
            except Exception:
                meta = {}

        return cls(
            id=model.id,
            business_id=model.business_id,
            activity_type=model.activity_type,
            title=model.title,
            description=model.description,
            metadata=meta,
            created_at=model.created_at,
        )


class BusinessActivityListResponse(BaseModel):
    success: bool = True
    items: List[BusinessActivityOut]
    total: int
    page: int
    page_size: int
    total_pages: int = 1
