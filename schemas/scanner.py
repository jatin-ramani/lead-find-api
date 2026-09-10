from typing import Optional
from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    """
    Body for POST /scan.

    `category` can be a category family (e.g., 'healthcare', 'catering', 'commercial')
    or any specific Geoapify Places category.
    `radius_km` defines the concentric search radius around the city centroid (default: 25 km).
    """

    city: str
    category: str
    radius_km: Optional[float] = Field(default=25.0, ge=1.0, le=100.0, description="Scanning radius in km")

    model_config = {
        "json_schema_extra": {
            "example": {"city": "Ahmedabad", "category": "healthcare", "radius_km": 25.0}
        }
    }


class ClearDataRequest(BaseModel):
    """Body for POST /scan/clear-data."""

    confirm: bool = Field(..., description="Must be true to safely clear all scanned businesses")

