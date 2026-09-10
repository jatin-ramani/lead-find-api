"""
Durable Background Continuous City Scanning Worker for Lead Finder.

Provides:
1. Multi-cell Geographic Subdivision Scanning (0-25+ km).
2. Category Family Expansion into all relevant subcategories.
3. Strict Contact Filtering (saves lead only if valid email OR phone exists; skips no-contact places).
4. CRM Deduplication & Intelligent Enrichment.
5. Atomic Lock Management, Restart Recovery, Pause, Resume, and Cancellation.
6. Incremental Database Persistence with real progress tracking.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from config import settings
from database.db import SessionLocal
from database.models import (
    Business,
    BusinessActivity,
    BusinessFollowUp,
    BusinessNote,
    BusinessTag,
    EmailAutomationExecution,
    EmailCampaignRecipient,
    ScanJob,
    ScanSearchUnit,
    WebsiteData,
)
from providers.exceptions import GeoapifyError
from providers.geoapify import fetch_places_page
from services.activity_service import ACTIVITY_BUSINESS_CREATED, create_activity
from services.contact_validator import evaluate_lead_contacts
from services.email_automation_service import (
    TRIGGER_LEAD_CREATED,
    evaluate_automations_for_event,
)
from services.geocoder import geocode_city
from services.geo_grid import ScanCell, generate_city_scan_grid
from services.lead_scoring import calculate_lead_score
from services.taxonomy import expand_category_family, normalize_category

logger = logging.getLogger(__name__)

# Job lifecycle constants
STATUS_PENDING = "Pending"
STATUS_RUNNING = "Running"
STATUS_PAUSED = "Paused"
STATUS_COMPLETED = "Completed"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"

# Search unit lifecycle constants
UNIT_PENDING = "pending"
UNIT_RUNNING = "running"
UNIT_RETRY_WAIT = "retry_wait"
UNIT_COMPLETED = "completed"
UNIT_FAILED = "failed"
UNIT_SKIPPED = "skipped"


def classify_provider_error(exc: Exception) -> Tuple[bool, bool, str]:
    """
    Classify provider exceptions into (is_quota_exhausted, is_transient, message).

    - is_quota_exhausted: Daily API limit reached (HTTP 402, or 429 quota text). Scan should pause safely.
    - is_transient: Rate limit per second (HTTP 429), timeouts, network blips, 5xx. Eligible for retry with exponential backoff.
    - permanent: 400 bad request, 401/403 unauthorized, unconfigured key. Fails immediately without retry.
    """
    err_str = str(exc)
    err_str_lower = err_str.lower()

    if isinstance(exc, GeoapifyError):
        code = getattr(exc, "status_code", None)
        resp_text = (getattr(exc, "response_text", "") or "").lower()

        # Quota detection
        if code == 402 or (code == 429 and any(k in resp_text for k in ("quota", "daily limit", "requests limit", "exceeded your plan", "plan limit", "credit"))):
            return True, False, f"Daily quota exhausted ({err_str})"

        # Transient detection
        if code in (429, 500, 502, 503, 504):
            return False, True, err_str

        # Permanent config/client error
        if code in (400, 401, 403):
            return False, False, err_str

    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return False, True, f"Network/Connection error: {err_str}"

    if "quota" in err_str_lower and any(k in err_str_lower for k in ("limit", "exceeded", "daily")):
        return True, False, f"Daily quota exhausted ({err_str})"

    if any(k in err_str_lower for k in ("timeout", "connection reset", "temporarily unavailable", "rate limit", "too many requests")):
        return False, True, err_str

    if any(k in err_str_lower for k in ("401", "403", "unauthorized", "forbidden", "not configured")):
        return False, False, err_str

    return False, False, err_str


_ACTIVE_SCANS_LOCK = threading.Lock()
_ACTIVE_SCANS: set = set()


def _is_scan_active(job_id: int) -> bool:
    with _ACTIVE_SCANS_LOCK:
        return job_id in _ACTIVE_SCANS


def _register_active_scan(job_id: int) -> bool:
    with _ACTIVE_SCANS_LOCK:
        if job_id in _ACTIVE_SCANS:
            return False
        _ACTIVE_SCANS.add(job_id)
        return True


def _unregister_active_scan(job_id: int) -> None:
    with _ACTIVE_SCANS_LOCK:
        _ACTIVE_SCANS.discard(job_id)


def start_continuous_scan(
    db: Session,
    city: str,
    category_or_family: str,
    radius_km: float = 25.0,
) -> ScanJob:
    """
    Initialize and launch a continuous background city scan.

    1. Geocodes target city.
    2. Expands category family into all subcategories.
    3. Generates geographic cells covering 0 to radius_km.
    4. Persists ScanJob and ScanSearchUnit records.
    5. Dispatches asynchronous worker thread.
    """
    cleaned_city = city.strip()
    if not cleaned_city:
        raise ValueError("City name is required.")

    # Geocode city center
    geo_res = geocode_city(cleaned_city)
    if not geo_res:
        raise ValueError(f"Could not locate or geocode city: {cleaned_city}")

    lat, lon, place_id = geo_res

    # Expand category family
    family_label, subcategories = expand_category_family(category_or_family)
    subcategories_json = json.dumps([{"label": l, "key": k} for l, k in subcategories])

    # Generate spatial cells
    cells = generate_city_scan_grid(lat, lon, max_radius_km=radius_km, cell_radius_km=3.0)
    total_cells = len(cells)
    total_search_units = total_cells * len(subcategories)

    now = datetime.now(timezone.utc)

    # Create ScanJob
    job = ScanJob(
        city=cleaned_city,
        category=family_label,
        category_family=category_or_family.strip(),
        subcategories_json=subcategories_json,
        scan_radius_km=int(radius_km),
        center_latitude=lat,
        center_longitude=lon,
        status=STATUS_PENDING,
        progress=0,
        total_cells=total_cells,
        completed_cells=0,
        current_cell="Initializing...",
        total_search_units=total_search_units,
        completed_search_units=0,
        businesses_found=0,
        businesses_stored=0,
        businesses_skipped_no_contact=0,
        businesses_duplicates=0,
        total_businesses=0,
        new_businesses=0,
        started_at=None,
        completed_at=None,
        paused_at=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()

    # Bulk insert search units
    search_units = []
    for cell in cells:
        for subcat_label, subcat_key in subcategories:
            unit = ScanSearchUnit(
                scan_job_id=job.id,
                cell_index=cell.cell_index,
                cell_latitude=cell.latitude,
                cell_longitude=cell.longitude,
                cell_radius_meters=cell.radius_meters,
                cell_label=cell.label,
                category_key=subcat_key,
                status=UNIT_PENDING,
                results_count=0,
                stored_count=0,
                skipped_no_contact_count=0,
                duplicates_count=0,
                created_at=now,
            )
            search_units.append(unit)

    db.bulk_save_objects(search_units)
    db.commit()
    db.refresh(job)

    # Launch background execution
    _spawn_background_worker(job.id)

    return job


def _spawn_background_worker(job_id: int) -> None:
    """Launch worker thread for a given scan job."""
    thread = threading.Thread(
        target=_run_scan_job_worker,
        args=(job_id,),
        name=f"scanner-worker-{job_id}",
        daemon=True,
    )
    thread.start()


def _run_scan_job_worker(job_id: int) -> None:
    """Background thread runner for a scan job."""
    if not _register_active_scan(job_id):
        logger.warning("Scan job %d is already running in another worker thread.", job_id)
        return

    db = SessionLocal()
    try:
        job = db.query(ScanJob).filter(ScanJob.id == job_id).first()
        if not job:
            logger.error("Scan job %d not found.", job_id)
            return

        if job.status not in (STATUS_PENDING, STATUS_RUNNING):
            logger.info("Scan job %d is in status '%s', skipping run.", job_id, job.status)
            return

        job.status = STATUS_RUNNING
        if not job.started_at:
            job.started_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "Starting continuous scan job %d for %s (family: %s, units: %d, radius: %d km)",
            job.id,
            job.city,
            job.category,
            job.total_search_units,
            job.scan_radius_km or 25,
        )

        while True:
            # Check if job was paused or cancelled externally
            db.expire(job)
            refreshed_job = db.query(ScanJob).filter(ScanJob.id == job_id).first()
            if not refreshed_job or refreshed_job.status in (STATUS_PAUSED, STATUS_CANCELLED):
                logger.info(
                    "Scan job %d halted due to status: %s",
                    job_id,
                    refreshed_job.status if refreshed_job else "deleted",
                )
                break

            now = datetime.now(timezone.utc)

            # Claim next eligible search unit (pending or retry_wait whose backoff has passed)
            unit = (
                db.query(ScanSearchUnit)
                .filter(
                    ScanSearchUnit.scan_job_id == job_id,
                    or_(
                        ScanSearchUnit.status == UNIT_PENDING,
                        and_(
                            ScanSearchUnit.status == UNIT_RETRY_WAIT,
                            or_(
                                ScanSearchUnit.next_attempt_at.is_(None),
                                ScanSearchUnit.next_attempt_at <= now,
                            ),
                        ),
                    ),
                )
                .order_by(ScanSearchUnit.cell_index.asc(), ScanSearchUnit.id.asc())
                .first()
            )

            if not unit:
                # Check if there are units in retry_wait waiting for a future retry time
                waiting_unit = (
                    db.query(ScanSearchUnit)
                    .filter(
                        ScanSearchUnit.scan_job_id == job_id,
                        ScanSearchUnit.status == UNIT_RETRY_WAIT,
                    )
                    .order_by(ScanSearchUnit.next_attempt_at.asc())
                    .first()
                )
                if waiting_unit and waiting_unit.next_attempt_at:
                    target_dt = waiting_unit.next_attempt_at
                    if target_dt.tzinfo is not None:
                        now_dt = datetime.now(timezone.utc)
                    else:
                        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                    wait_sec = (target_dt - now_dt).total_seconds()
                    if wait_sec > 0:
                        sleep_time = min(5.0, max(0.5, wait_sec))
                        time.sleep(sleep_time)
                        continue

                # All search units processed (either completed, failed, or skipped)!
                refreshed_job.status = STATUS_COMPLETED
                _recalculate_job_progress(db, refreshed_job)
                refreshed_job.completed_at = datetime.now(timezone.utc)
                refreshed_job.updated_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(
                    "Scan job %d fully completed. Coverage: %d%%, Processed: %d%%, Stored: %d, Failed Units: %d",
                    job_id,
                    refreshed_job.coverage_progress,
                    refreshed_job.processed_progress,
                    refreshed_job.businesses_stored,
                    refreshed_job.failed_search_units,
                )
                break

            # Execute search unit
            _execute_search_unit(db, refreshed_job, unit)

            # If execute caused a quota pause, halt worker cleanly
            if refreshed_job.status == STATUS_PAUSED:
                logger.info("Scan job %d paused (quota or manual). Halting worker.", job_id)
                break

    except Exception as exc:
        logger.exception("Unexpected error in scan worker for job %d: %s", job_id, exc)
        try:
            db.rollback()
            failed_job = db.query(ScanJob).filter(ScanJob.id == job_id).first()
            if failed_job and failed_job.status not in (STATUS_CANCELLED, STATUS_COMPLETED):
                failed_job.status = STATUS_FAILED
                failed_job.error_message = str(exc)
                failed_job.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
    finally:
        _unregister_active_scan(job_id)
        db.close()


def _execute_search_unit(db: Session, job: ScanJob, unit: ScanSearchUnit) -> None:
    """Execute a single search unit for a geographic cell and category with controlled retry & quota protection."""
    unit.status = UNIT_RUNNING
    unit.attempted_at = datetime.now(timezone.utc)
    job.current_cell = unit.cell_label
    db.commit()

    features = None
    try:
        # Query Geoapify Places API for this specific cell & category
        features = fetch_places_page(
            category=unit.category_key,
            latitude=unit.cell_latitude,
            longitude=unit.cell_longitude,
            radius=unit.cell_radius_meters,
            limit=100,
        )
    except Exception as exc:
        is_quota, is_transient, err_msg = classify_provider_error(exc)
        logger.warning(
            "Query error for unit %d (cell %d, cat %s): quota=%s, transient=%s: %s",
            unit.id, unit.cell_index, unit.category_key, is_quota, is_transient, exc,
        )

        now = datetime.now(timezone.utc)
        if is_quota:
            # Quota exhausted: Pause job safely, preserve unit state, do not mark as failed or lose data
            job.status = STATUS_PAUSED
            job.paused_at = now
            job.error_message = f"Daily Geoapify API quota reached: {err_msg}. Scan paused safely. Resume when quota is refreshed."
            unit.status = UNIT_PENDING  # Remains pending so it can resume cleanly
            unit.error_message = err_msg
            _recalculate_job_progress(db, job)
            return

        if is_transient:
            unit.attempt_count += 1
            unit.error_message = err_msg
            if unit.attempt_count < 3:
                backoff_seconds = 30 * (2 ** (unit.attempt_count - 1))  # attempt 1 -> 30s, attempt 2 -> 60s
                unit.status = UNIT_RETRY_WAIT
                unit.next_attempt_at = now + timedelta(seconds=backoff_seconds)
                logger.info(
                    "Unit %d scheduled for retry #%d at %s (backoff %ds)",
                    unit.id, unit.attempt_count + 1, unit.next_attempt_at.isoformat(), backoff_seconds,
                )
            else:
                unit.status = UNIT_FAILED
                unit.completed_at = now
                logger.warning("Unit %d permanently failed after %d attempts: %s", unit.id, unit.attempt_count, err_msg)
            _recalculate_job_progress(db, job)
            return

        # Permanent configuration/auth error
        unit.status = UNIT_FAILED
        unit.error_message = err_msg
        unit.completed_at = now
        _recalculate_job_progress(db, job)
        return

    # Process features
    results_count = len(features)
    stored_count = 0
    skipped_no_contact = 0
    duplicate_count = 0

    for feat in features:
        props = feat.get("properties", {})
        contact = props.get("contact", {})

        raw_name = props.get("name") or props.get("formatted") or f"Local {unit.category_key.split('.')[-1].capitalize()}"
        raw_email = contact.get("email")
        raw_phone = contact.get("phone")
        website = props.get("website")
        address = props.get("formatted")
        place_id = props.get("place_id")

        # Strict Contact Rule: Store only if email OR phone is present
        is_storable, clean_email, clean_phone = evaluate_lead_contacts(raw_email, raw_phone)
        if not is_storable:
            skipped_no_contact += 1
            continue

        # Deduplication check: place_id -> phone+city -> name+city
        existing_business = None
        if place_id:
            existing_business = db.query(Business).filter(Business.place_id == place_id).first()

        if not existing_business and clean_phone:
            existing_business = (
                db.query(Business)
                .filter(Business.phone == clean_phone, Business.city == job.city)
                .first()
            )

        if not existing_business and raw_name and job.city:
            existing_business = (
                db.query(Business)
                .filter(
                    func.lower(Business.name) == raw_name.lower().strip(),
                    func.lower(Business.city) == job.city.lower().strip(),
                )
                .first()
            )

        if existing_business:
            duplicate_count += 1
            # Intelligent enrichment: safely populate missing fields without overwriting valid data
            updated = False
            if clean_email and not existing_business.email:
                existing_business.email = clean_email
                updated = True
            if clean_phone and not existing_business.phone:
                existing_business.phone = clean_phone
                updated = True
            if website and not existing_business.website:
                existing_business.website = website
                existing_business.status = "Has Website"
                updated = True
            if address and not existing_business.address:
                existing_business.address = address
                updated = True
            if place_id and not existing_business.place_id:
                existing_business.place_id = place_id
                updated = True
            if updated:
                existing_business.updated_at = datetime.now(timezone.utc)
                db.commit()
            continue

        # Create new Business record
        status = "Has Website" if website else "No Website"
        score_data = calculate_lead_score({
            "name": raw_name,
            "phone": clean_phone,
            "email": clean_email,
            "website": website,
            "city": job.city,
            "category": unit.category_key,
            "address": address,
        })

        new_biz = Business(
            name=raw_name,
            phone=clean_phone,
            email=clean_email,
            website=website,
            city=job.city,
            category=unit.category_key,
            address=address,
            status=status,
            place_id=place_id,
            lead_score=score_data.score,
            lead_grade=score_data.grade,
            lead_status="new",
            is_favorite=False,
        )
        db.add(new_biz)
        db.flush()

        # Record activity and evaluate automations
        create_activity(
            db=db,
            business_id=new_biz.id,
            activity_type=ACTIVITY_BUSINESS_CREATED,
            title="Business Discovered",
            description=f"Continuous scanner discovered lead in {unit.cell_label}",
            metadata={"city": job.city, "category": unit.category_key, "name": raw_name},
            commit=False,
        )
        evaluate_automations_for_event(
            db=db,
            trigger_type=TRIGGER_LEAD_CREATED,
            business_id=new_biz.id,
            commit=False,
        )

        stored_count += 1

    # Update search unit stats
    unit.results_count = results_count
    unit.stored_count = stored_count
    unit.skipped_no_contact_count = skipped_no_contact
    unit.duplicates_count = duplicate_count
    unit.status = UNIT_COMPLETED
    unit.completed_at = datetime.now(timezone.utc)

    # Increment job cumulative totals
    job.businesses_found += results_count
    job.businesses_stored += stored_count
    job.businesses_skipped_no_contact += skipped_no_contact
    job.businesses_duplicates += duplicate_count
    job.total_businesses = job.businesses_found
    job.new_businesses = job.businesses_stored

    _recalculate_job_progress(db, job)


def _recalculate_job_progress(db: Session, job: ScanJob) -> None:
    """Calculate and persist accurate job progress, geographic coverage, and cell metrics."""
    completed_units = (
        db.query(func.count(ScanSearchUnit.id))
        .filter(
            ScanSearchUnit.scan_job_id == job.id,
            ScanSearchUnit.status == UNIT_COMPLETED,
        )
        .scalar()
        or 0
    )

    failed_units = (
        db.query(func.count(ScanSearchUnit.id))
        .filter(
            ScanSearchUnit.scan_job_id == job.id,
            ScanSearchUnit.status == UNIT_FAILED,
        )
        .scalar()
        or 0
    )

    completed_cells = (
        db.query(func.count(func.distinct(ScanSearchUnit.cell_index)))
        .filter(
            ScanSearchUnit.scan_job_id == job.id,
            ScanSearchUnit.status == UNIT_COMPLETED,
        )
        .scalar()
        or 0
    )

    total_units = job.total_search_units or 1
    coverage_pct = min(100, int((completed_units / total_units) * 100))
    processed_pct = min(100, int(((completed_units + failed_units) / total_units) * 100))

    if job.status == STATUS_COMPLETED:
        if failed_units == 0 and total_units > 0:
            coverage_pct = 100
        processed_pct = 100

    job.completed_search_units = completed_units
    job.failed_search_units = failed_units
    job.completed_cells = completed_cells
    job.coverage_progress = coverage_pct
    job.processed_progress = processed_pct
    # Progress reflects true geographic coverage
    job.progress = coverage_pct
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


def pause_scan_job(db: Session, job_id: int) -> Optional[ScanJob]:
    """Pause an active or pending scan job."""
    job = db.query(ScanJob).filter(ScanJob.id == job_id).first()
    if not job:
        return None

    if job.status in (STATUS_RUNNING, STATUS_PENDING):
        job.status = STATUS_PAUSED
        job.paused_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
        logger.info("Scan job %d marked as PAUSED.", job_id)

    return job


def resume_scan_job(db: Session, job_id: int) -> Optional[ScanJob]:
    """Resume a paused scan job."""
    job = db.query(ScanJob).filter(ScanJob.id == job_id).first()
    if not job:
        return None

    if job.status == STATUS_PAUSED:
        job.status = STATUS_RUNNING
        job.paused_at = None
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
        logger.info("Scan job %d resumed.", job_id)
        _spawn_background_worker(job.id)

    return job


def cancel_scan_job(db: Session, job_id: int) -> Optional[ScanJob]:
    """Cancel a scan job, preserving all already-stored businesses."""
    job = db.query(ScanJob).filter(ScanJob.id == job_id).first()
    if not job:
        return None

    if job.status not in (STATUS_COMPLETED, STATUS_CANCELLED):
        job.status = STATUS_CANCELLED
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
        logger.info("Scan job %d CANCELLED. Stored leads are preserved.", job_id)

    return job


def get_scan_status_detail(db: Session, job_id: int) -> Optional[Dict[str, Any]]:
    """Return detailed progress metrics and live newly discovered lead feed for a scan job."""
    job = db.query(ScanJob).filter(ScanJob.id == job_id).first()
    if not job:
        return None

    # Retrieve 20 most recent businesses for this city / category
    recent_leads = (
        db.query(Business)
        .filter(Business.city == job.city)
        .order_by(Business.id.desc())
        .limit(20)
        .all()
    )

    lead_feed = [
        {
            "id": b.id,
            "name": b.name,
            "city": b.city,
            "category": b.category,
            "has_email": bool(b.email),
            "has_phone": bool(b.phone),
            "has_website": bool(b.website),
            "lead_grade": b.lead_grade,
            "lead_score": b.lead_score,
        }
        for b in recent_leads
    ]

    return {
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
        "current_cell": job.current_cell or "Initializing...",
        "total_search_units": job.total_search_units,
        "completed_search_units": job.completed_search_units,
        "failed_search_units": job.failed_search_units or 0,
        "businesses_found": job.businesses_found,
        "businesses_stored": job.businesses_stored,
        "businesses_skipped_no_contact": job.businesses_skipped_no_contact,
        "businesses_duplicates": job.businesses_duplicates,
        "total_businesses": job.total_businesses,
        "new_businesses": job.new_businesses,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "paused_at": job.paused_at.isoformat() if job.paused_at else None,
        "error_message": job.error_message,
        "recent_leads": lead_feed,
    }


def clear_all_scanned_leads(db: Session, confirmation: bool) -> int:
    """
    Safely delete scanner-generated business leads.

    Guarantees:
    1. Requires explicit confirmation: confirmation must be True.
    2. Deletes only Businesses and their cascaded relations (notes, tags, activities, follow-ups, executions).
    3. PRESERVES Admin sessions, users, email credentials, and email templates!
    """
    if not confirmation:
        raise ValueError("Explicit confirmation is required to clear scanned business leads.")

    # Halt any active scans first
    active_jobs = db.query(ScanJob).filter(ScanJob.status.in_([STATUS_RUNNING, STATUS_PENDING])).all()
    for job in active_jobs:
        job.status = STATUS_CANCELLED
        job.updated_at = datetime.now(timezone.utc)
    db.commit()

    # Count businesses to delete
    count = db.query(func.count(Business.id)).scalar() or 0

    # Delete businesses (cascade deletes notes, activities, follow_ups, executions, website_data)
    db.query(BusinessTag).delete(synchronize_session=False)
    db.query(BusinessActivity).delete(synchronize_session=False)
    db.query(BusinessNote).delete(synchronize_session=False)
    db.query(BusinessFollowUp).delete(synchronize_session=False)
    db.query(EmailAutomationExecution).delete(synchronize_session=False)
    db.query(EmailCampaignRecipient).delete(synchronize_session=False)
    db.query(WebsiteData).delete(synchronize_session=False)
    db.query(Business).delete(synchronize_session=False)

    db.commit()
    logger.info("Admin cleared %d scanned business records.", count)
    return count


def recover_stale_scans_on_startup(db: Session) -> int:
    """Recover scans left in 'Running' state across server restarts."""
    stale_jobs = db.query(ScanJob).filter(ScanJob.status == STATUS_RUNNING).all()
    recovered = 0
    for job in stale_jobs:
        job.status = STATUS_PAUSED
        job.paused_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        recovered += 1

    if recovered > 0:
        db.commit()
        logger.info("Recovered %d stale scanner jobs on server startup (set to Paused).", recovered)

    return recovered
