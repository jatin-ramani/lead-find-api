from pydantic import BaseModel


class ScanRequest(BaseModel):
    """
    Body for POST /scan.

    `category` must be one the Geoapify Places API recognises — an unsupported
    value is rejected upstream and the scan finds nothing.
    """

    city: str
    category: str

    model_config = {
        "json_schema_extra": {
            "example": {"city": "Ahmedabad", "category": "commercial"}
        }
    }
