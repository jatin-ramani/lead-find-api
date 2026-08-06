from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import (
    get_scan_jobs,
    get_latest_scan_job,
)

router = APIRouter(
    prefix="/scan/jobs",
    tags=["Scan Jobs"],
)


@router.get("")
def list_scan_jobs(
    db: Session = Depends(get_db),
):
    return get_scan_jobs(db)


@router.get("/latest")
def latest_scan_job(
    db: Session = Depends(get_db),
):
    job = get_latest_scan_job(db)

    if not job:
        raise HTTPException(
            status_code=404,
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