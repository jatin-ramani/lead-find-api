import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database.db import get_db
from errors import AppError, ErrorCode
from schemas.common import (
    MessageResponse,
    VALIDATION_ERROR_RESPONSE,
    error_response,
)
from schemas.scanner import ClearDataRequest, ScanRequest
from services.scan_worker import (
    cancel_scan_job,
    clear_all_scanned_leads,
    get_scan_status_detail,
    pause_scan_job,
    resume_scan_job,
    start_continuous_scan,
)
from services.taxonomy import CATEGORY_FAMILIES

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/scan",
    tags=["Business Scanning"],
)


@router.post(
    "",
    summary="Start continuous city scan",
    description=(
        "Initiates a durable multi-cell background continuous scan over the specified "
        "city and category family/key across concentric geographic ranges (0-25+ km).\n\n"
        "Stores leads only if valid email OR phone is present, deduplicating records "
        "and enriching existing leads."
    ),
    response_description="The scan job was accepted and queued for execution.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "Continuous scan initiated.",
                        "job_id": 1,
                        "city": "Ahmedabad",
                        "category": "Healthcare & Wellness",
                        "total_cells": 19,
                        "total_search_units": 152,
                    }
                }
            }
        },
        400: error_response("Invalid scan parameters or geocoding failed.", "Could not locate city."),
        422: VALIDATION_ERROR_RESPONSE,
        500: error_response("Unexpected scan initiation failure.", "The scan failed unexpectedly.", error=ErrorCode.INTERNAL_ERROR),
    },
)
def scan(
    request: ScanRequest,
    db: Session = Depends(get_db),
):
    try:
        job = start_continuous_scan(
            db=db,
            city=request.city,
            category_or_family=request.category,
            radius_km=request.radius_km or 25.0,
        )
        return {
            "success": True,
            "message": "Continuous scan initiated.",
            "job_id": job.id,
            "city": job.city,
            "category": job.category,
            "total_cells": job.total_cells,
            "total_search_units": job.total_search_units,
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Failed to start continuous scan: %s", exc)
        raise AppError(
            "The scan failed unexpectedly.",
            status_code=500,
            error=ErrorCode.INTERNAL_ERROR,
        ) from exc


@router.get(
    "/families",
    summary="Get category families taxonomy",
    description="Returns available category families and their mapped subcategories for continuous scanning.",
)
def list_category_families():
    return {
        "success": True,
        "families": [
            {
                "id": fid,
                "label": f["label"],
                "subcategories": [{"label": l, "key": k} for l, k in f["subcategories"]],
            }
            for fid, f in CATEGORY_FAMILIES.items()
        ],
    }


@router.post(
    "/{job_id}/pause",
    summary="Pause active scan job",
    description="Pauses the execution of an ongoing background city scan.",
)
def pause_scan(
    job_id: int,
    db: Session = Depends(get_db),
):
    job = pause_scan_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan job {job_id} not found.",
        )
    return {
        "success": True,
        "message": f"Scan job {job_id} paused.",
        "status": job.status,
    }


@router.post(
    "/{job_id}/resume",
    summary="Resume paused scan job",
    description="Resumes a previously paused background city scan from its next pending cell/search unit.",
)
def resume_scan(
    job_id: int,
    db: Session = Depends(get_db),
):
    job = resume_scan_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan job {job_id} not found.",
        )
    return {
        "success": True,
        "message": f"Scan job {job_id} resumed.",
        "status": job.status,
    }


@router.post(
    "/{job_id}/cancel",
    summary="Cancel scan job",
    description="Cancels an in-flight background city scan. All previously stored businesses are preserved.",
)
def cancel_scan(
    job_id: int,
    db: Session = Depends(get_db),
):
    job = cancel_scan_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan job {job_id} not found.",
        )
    return {
        "success": True,
        "message": f"Scan job {job_id} cancelled.",
        "status": job.status,
    }


@router.post(
    "/clear-data",
    summary="Clear scanned businesses data",
    description=(
        "Safely clear all scanned business leads and their activity/followup records. "
        "User sessions, email templates, campaigns, and OAuth credentials are preserved."
    ),
)
def clear_data(
    request: ClearDataRequest,
    db: Session = Depends(get_db),
):
    if not request.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation is required to clear scanned business data.",
        )

    deleted_count = clear_all_scanned_leads(db, request.confirm)
    return {
        "success": True,
        "message": f"Successfully cleared {deleted_count} scanned businesses.",
        "deleted_count": deleted_count,
    }

