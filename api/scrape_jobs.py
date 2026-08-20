from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import (
    get_scrape_jobs,
    get_scrape_job,
    get_scrape_job_results,
    delete_scrape_job,
)
from database.models import ScrapeJob
from schemas.common import (
    PATH_VALIDATION_ERROR_RESPONSE,
    MessageResponse,
    error_response,
)

router = APIRouter(
    prefix="/scrape/jobs",
    tags=["Scrape Jobs"],
)

_SCRAPE_JOB_EXAMPLE = {
    "id": 12,
    "status": "Completed",
    "progress": 100,
    "total_websites": 96,
    "completed": 96,
    "success": 87,
    "failed": 9,
    "current_business_id": None,
    "started_at": "2026-08-07T09:02:11.004512+00:00",
    "completed_at": "2026-08-07T09:07:48.771903+00:00",
}

_NOT_FOUND = error_response(
    "No scrape job with that id.",
    "Scrape job not found",
)

_BAD_ID = PATH_VALIDATION_ERROR_RESPONSE


def _serialise(job: ScrapeJob) -> Dict[str, Any]:
    """Shape a ScrapeJob for the API. Datetimes are encoded by FastAPI."""

    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "total_websites": job.total_websites,
        "completed": job.completed,
        "success": job.success,
        "failed": job.failed,
        "current_business_id": job.current_business_id,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


@router.get(
    "",
    summary="List scrape jobs",
    description=(
        "Every website-scrape job ever started, newest first.\n\n"
        "`status` moves `Pending` → `Running` → `Completed`, or `Failed` if "
        "the run aborted. `current_business_id` is only set while a job is in "
        "flight and is cleared once it finishes."
    ),
    response_description="All scrape jobs, newest first.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "data": [_SCRAPE_JOB_EXAMPLE],
                    }
                }
            }
        }
    },
)
def list_scrape_jobs(
    db: Session = Depends(get_db),
):
    jobs = get_scrape_jobs(db)

    return {
        "success": True,
        "data": [_serialise(job) for job in jobs],
    }


@router.get(
    "/{job_id}",
    summary="Get one scrape job",
    description=(
        "A single scrape job by id — the endpoint to poll for live progress "
        "after starting a bulk scrape."
    ),
    response_description="The requested scrape job.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {"success": True, "data": _SCRAPE_JOB_EXAMPLE}
                }
            }
        },
        404: _NOT_FOUND,
        422: _BAD_ID,
    },
)
def read_scrape_job(
    job_id: int,
    db: Session = Depends(get_db),
):

    job = get_scrape_job(db, job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scrape job not found",
        )

    return {
        "success": True,
        "data": _serialise(job),
    }


@router.get(
    "/{job_id}/results",
    summary="Get scrape job results",
    description="Returns paginated detailed results of scraped websites for a specific scrape job, with status/city filtering.",
    response_description="Detailed scrape results and summary.",
    responses={
        404: _NOT_FOUND,
        422: _BAD_ID,
    },
)
def read_scrape_job_results(
    job_id: int,
    page: int = Query(1, ge=1, description="1-based page number."),
    pageSize: int = Query(20, ge=1, le=100, description="Rows per page."),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (Completed, Failed)."),
    city: Optional[str] = Query(None, description="Filter by city name."),
    search: Optional[str] = Query(None, description="Search business name, website or title."),
    db: Session = Depends(get_db),
):
    results = get_scrape_job_results(
        db=db,
        job_id=job_id,
        page=page,
        page_size=pageSize,
        status=status_filter,
        city=city,
        search=search,
    )

    if results is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scrape job not found",
        )

    return results


@router.delete(
    "/{job_id}",
    response_model=MessageResponse,
    summary="Delete a scrape job",
    description=(
        "Removes a scrape job record. This only deletes the job's history — "
        "the scraped `website_data` it produced is left untouched."
    ),
    response_description="The scrape job was deleted.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "Scrape job deleted successfully.",
                    }
                }
            }
        },
        404: _NOT_FOUND,
        422: _BAD_ID,
    },
)
def remove_scrape_job(
    job_id: int,
    db: Session = Depends(get_db),
):

    deleted = delete_scrape_job(db, job_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scrape job not found",
        )

    return {
        "success": True,
        "message": "Scrape job deleted successfully.",
    }
