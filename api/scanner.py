from fastapi import APIRouter

from schemas.common import MessageResponse, VALIDATION_ERROR_RESPONSE
from schemas.scanner import ScanRequest
from services.scanner import scan_city

router = APIRouter(
    prefix="/scan",
    tags=["Business Scanning"],
)


@router.post(
    "",
    response_model=MessageResponse,
    summary="Scan a city for businesses",
    description=(
        "Queries the Geoapify Places API for the given city and category and "
        "stores anything not already known, de-duplicated by `place_id`.\n\n"
        "Progress is recorded against a **scan job**, which "
        "`GET /scan/jobs` reports.\n\n"
        "`category` must be a category Geoapify recognises — for example "
        "`commercial`, `catering` or `healthcare`. An unsupported value "
        "returns no results and the job finishes with zero businesses."
    ),
    response_description="The scan finished and any new businesses were saved.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "Scan completed.",
                    }
                }
            }
        },
        422: VALIDATION_ERROR_RESPONSE,
    },
)
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
