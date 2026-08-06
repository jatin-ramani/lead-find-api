from pydantic import BaseModel


class ScanRequest(BaseModel):
    city: str
    category: str