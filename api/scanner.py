import logging

from fastapi import APIRouter

from errors import AppError, ErrorCode
from schemas.common import (
    MessageResponse,
    VALIDATION_ERROR_RESPONSE,
    error_response,
)
from schemas.scanner import ScanRequest
from services.scanner import ScanFailed, scan_city

logger = logging.getLogger(__name__)

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
        "Runs to completion before answering, so the response reflects the "
        "actual outcome. Progress is recorded against a **scan job**, which "
        "`GET /scan/jobs` reports; the id of a failed job is returned in "
        "`details.job_id`.\n\n"
        "`category` must be a category Geoapify recognises — for example "
        "`commercial`, `catering` or `healthcare`. An unsupported value is "
        "rejected by Geoapify and the scan fails with **502**."
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
        502: error_response(
            "Geoapify could not be reached or rejected the request. The scan "
            "job is marked \"Failed\"; the scan can be retried.",
            "The scan could not be completed because Geoapify is unavailable.",
            error=ErrorCode.UPSTREAM_ERROR,
        ),
        500: error_response(
            "The scan failed for an unexpected reason. The scan job is marked "
            "\"Failed\".",
            "The scan failed unexpectedly.",
            error=ErrorCode.INTERNAL_ERROR,
        ),
    },
)
def scan(request: ScanRequest):

    try:
        scan_city(
            request.city,
            request.category,
        )

    except ScanFailed as exc:
        # The job row already says "Failed"; the response has to agree with it.
        # `exc.message` is not forwarded — it can carry the provider's raw
        # response body, which is not ours to hand to a client.
        if exc.upstream:
            raise AppError(
                "The scan could not be completed because Geoapify is "
                "unavailable.",
                status_code=502,
                error=ErrorCode.UPSTREAM_ERROR,
                details={"job_id": exc.job_id},
            ) from exc

        raise AppError(
            "The scan failed unexpectedly.",
            status_code=500,
            error=ErrorCode.INTERNAL_ERROR,
            details={"job_id": exc.job_id},
        ) from exc

    return {
        "success": True,
        "message": "Scan completed."
    }
