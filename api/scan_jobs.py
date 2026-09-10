from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import (
    get_scan_jobs,
    get_latest_scan_job,
)
from schemas.common import error_response
from services.scan_worker import get_scan_status_detail

router = APIRouter(
    prefix="/scan/jobs",
    tags=["Scan Jobs"],
)


@router.get(
    "",
    summary="List scan jobs",
    description="Every continuous business scan ever started, newest first.",
)
def list_scan_jobs(
    db: Session = Depends(get_db),
):
    jobs = get_scan_jobs(db)
    return [
        {
            "id": j.id,
            "city": j.city,
            "category": j.category,
            "categoryFamily": j.category_family or j.category,
            "status": j.status,
            "progress": j.progress,
            "coverageProgress": j.coverage_progress or j.progress,
            "processedProgress": j.processed_progress or j.progress,
            "coverage_progress": j.coverage_progress or j.progress,
            "processed_progress": j.processed_progress or j.progress,
            "scanRadiusKm": j.scan_radius_km or 25,
            "totalCells": j.total_cells or 0,
            "completedCells": j.completed_cells or 0,
            "currentCell": j.current_cell or "",
            "totalSearchUnits": j.total_search_units or 0,
            "completedSearchUnits": j.completed_search_units or 0,
            "failedSearchUnits": j.failed_search_units or 0,
            "failed_search_units": j.failed_search_units or 0,
            "businessesFound": j.businesses_found or 0,
            "businessesStored": j.businesses_stored or 0,
            "businessesSkippedNoContact": j.businesses_skipped_no_contact or 0,
            "businessesDuplicates": j.businesses_duplicates or 0,
            "totalBusinesses": j.total_businesses or 0,
            "newBusinesses": j.new_businesses or 0,
            "startedAt": j.started_at.isoformat() if j.started_at else None,
            "completedAt": j.completed_at.isoformat() if j.completed_at else None,
            "pausedAt": j.paused_at.isoformat() if j.paused_at else None,
            "errorMessage": j.error_message,
            "createdAt": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]


@router.get(
    "/latest",
    summary="Most recent scan job",
    description="The newest scan job, for polling progress or displaying active scan status.",
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

    detail = get_scan_status_detail(db, job.id)
    return detail or {
        "id": job.id,
        "city": job.city,
        "category": job.category,
        "category_family": job.category_family,
        "status": job.status,
        "progress": job.progress,
        "coverage_progress": job.coverage_progress or job.progress,
        "processed_progress": job.processed_progress or job.progress,
        "scan_radius_km": job.scan_radius_km or 25,
        "total_cells": job.total_cells,
        "completed_cells": job.completed_cells,
        "current_cell": job.current_cell,
        "total_search_units": job.total_search_units,
        "completed_search_units": job.completed_search_units,
        "failed_search_units": job.failed_search_units or 0,
        "businesses_found": job.businesses_found,
        "businesses_stored": job.businesses_stored,
        "businesses_skipped_no_contact": job.businesses_skipped_no_contact,
        "businesses_duplicates": job.businesses_duplicates,
        "total_businesses": job.total_businesses,
        "new_businesses": job.new_businesses,
        "recent_leads": [],
    }


@router.get(
    "/{job_id}",
    summary="Get scan job status details",
    description="Get detailed progress metrics, cell counters, and recent live leads for a specific scan job.",
)
def get_scan_job_detail(
    job_id: int,
    db: Session = Depends(get_db),
):
    detail = get_scan_status_detail(db, job_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan job {job_id} not found.",
        )
    return detail

