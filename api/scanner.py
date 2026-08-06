from fastapi import APIRouter
from schemas.scanner import ScanRequest
from services.geoapify import search_business

router = APIRouter()


@router.post("/scan")
def scan_city(request: ScanRequest):
    search_business(
        city=request.city,
        category=request.category,
    )

    return {
        "success": True,
        "message": "Scan completed successfully."
    }