from pydantic import BaseModel
from typing import Optional


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