from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import (
    get_scan_jobs,
    get_latest_scan_job,
)
from schemas.common import error_response

router = APIRouter(
    prefix="/scan/jobs",
    tags=["Scan Jobs"],
)

_SCAN_JOB_EXAMPLE = {
    "id": 5,
    "city": "Ahmedabad",
    "category": "commercial",
    "status": "Completed",
    "progress": 100,
    "total_businesses": 20,
    "new_businesses": 6,
}


@router.get(
    "",
    summary="List scan jobs",
    description=(
        "Every business scan ever started, newest first. Each entry carries "
        "its progress percentage and how many businesses were found versus "
        "newly added."
    ),
    response_description="All scan jobs, newest first.",
    responses={
        200: {
            "content": {
                "application/json": {"example": [_SCAN_JOB_EXAMPLE]}
            }
        }
    },
)
def list_scan_jobs(
    db: Session = Depends(get_db),
):
    return get_scan_jobs(db)


@router.get(
    "/latest",
    summary="Most recent scan job",
    description=(
        "The newest scan job, for polling progress after `POST /scan`.\n\n"
        "Returns **404** when no scan has ever been run."
    ),
    response_description="The most recently created scan job.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "id": 5,
                        "city": "Ahmedabad",
                        "category": "commercial",
                        "status": "Completed",
                        "progress": 100,
                        "totalBusinesses": 20,
                        "newBusinesses": 6,
                    }
                }
            }
        },
        404: error_response(
            "No scan job exists yet.",
            "No scan jobs found.",
        ),
    },
)
def latest_scan_job(
    db: Session = Depends(get_db),
):
    job = get_latest_scan_job(db)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No scan jobs found.",
        )

    return {
        "id": job.id,
        "city": job.city,
        "category": job.category,
        "status": job.status,
        "progress": job.progress,
        "totalBusinesses": job.total_businesses,
        "newBusinesses": job.new_businesses,
    }
