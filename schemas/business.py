from typing import List, Optional

from pydantic import BaseModel


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