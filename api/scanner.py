from fastapi import APIRouter, BackgroundTasks

from schemas.scanner import ScanRequest
from services.scanner import scan_city

router = APIRouter()


@router.post("/scan")
def scan(request: ScanRequest):

    print("=" * 50)
    print("City:", request.city)
    print("Category:", request.category)
    print("=" * 50)

    scan_city(
        request.city,
        request.category,
    )

    return {
        "success": True,
        "message": "Scan completed."
    }