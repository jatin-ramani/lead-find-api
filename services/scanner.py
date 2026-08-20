"""
Business scanning.

Asks the provider for businesses in a city using pagination, stores the ones
not already known, and records the outcome against a scan job. The job is the
durable record; the exceptions raised here are how the *caller* learns the outcome,
which is what lets `POST /scan` answer truthfully.
"""

import logging
from typing import Optional

from config import settings
from database.crud import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    RUNNING_STATUS,
    create_scan_job,
    save_business,
    update_scan_job,
)
from database.db import SessionLocal

from providers.geoapify import (
    GeoapifyError,
    fetch_places_page,
    search_businesses,
)
from services.geocoder import geocode_city
from services.taxonomy import normalize_category

logger = logging.getLogger(__name__)


class ScanFailed(Exception):
    """
    A scan that did not finish.

    Carries the id of the job that was marked "Failed", so the caller can point
    the user at it, and `upstream` to say who was at fault: the provider (worth
    retrying) or this service (a bug worth fixing). The two deserve different
    HTTP status codes.
    """

    def __init__(self, message: str, *, job_id: Optional[int], upstream: bool):
        super().__init__(message)

        self.message = message
        self.job_id = job_id
        self.upstream = upstream


def _mark_failed(db, job_id: int, progress: int, total: int, added: int) -> None:
    """
    Record the failure, keeping whatever the scan had already achieved.
    """

    try:
        db.rollback()

        update_scan_job(
            db=db,
            job_id=job_id,
            progress=progress,
            total_businesses=total,
            new_businesses=added,
            status=FAILED_STATUS,
        )

    except Exception:
        logger.exception(
            "Could not mark scan job %s as failed; it may be left Running",
            job_id,
        )


def scan_city(city: str, category: str) -> int:
    """
    Run a paginated scan to completion and return its job id.

    Normalizes the category alias/term to a verified Geoapify category key.
    Raises `ScanFailed` if the scan did not complete. The job is marked
    "Failed" before the exception leaves, so the two records — the HTTP
    response and the job row — always agree.
    """

    db = SessionLocal()

    normalized_cat = normalize_category(category)

    try:
        job_id = create_scan_job(db=db, city=city, category=normalized_cat)

        total = 0
        added = 0
        progress = 0

        try:
            limit = settings.GEOAPIFY_SEARCH_LIMIT
            max_pages = settings.GEOAPIFY_MAX_SCAN_PAGES

            lat, lon, place_id = None, None, None

            for page in range(max_pages):
                offset = page * limit

                logger.info(
                    "Scan job %s: fetching page %s (offset=%s, limit=%s) for %r/%r (normalized: %r)",
                    job_id, page + 1, offset, limit, city, category, normalized_cat,
                )

                if page == 0:
                    page_features = search_businesses(city, normalized_cat)
                else:
                    if not place_id and not (lat and lon):
                        geo_res = geocode_city(city)
                        if geo_res:
                            lat, lon, place_id = geo_res

                    page_features = fetch_places_page(
                        category=normalized_cat,
                        place_id=place_id,
                        latitude=lat,
                        longitude=lon,
                        limit=limit,
                        offset=offset,
                    )

                if not page_features:
                    logger.info("Scan job %s: page %s returned 0 features; ending scan", job_id, page + 1)
                    progress = 100
                    update_scan_job(
                        db=db,
                        job_id=job_id,
                        progress=100,
                        total_businesses=total,
                        new_businesses=added,
                        status=COMPLETED_STATUS,
                    )
                    break

                page_count = len(page_features)
                total += page_count

                for business in page_features:
                    properties = business.get("properties", {})
                    contact = properties.get("contact", {})

                    name = properties.get("name") or properties.get("formatted") or f"Unnamed {normalized_cat.capitalize()}"
                    website = properties.get("website")
                    status = "Has Website" if website else "No Website"

                    saved = save_business(
                        db=db,
                        name=name,
                        phone=contact.get("phone"),
                        email=contact.get("email"),
                        website=website,
                        city=city,
                        category=normalized_cat,
                        address=properties.get("formatted"),
                        status=status,
                        place_id=properties.get("place_id"),
                    )

                    if saved:
                        added += 1

                is_last_page = page_count < limit

                if is_last_page:
                    progress = 100
                    update_scan_job(
                        db=db,
                        job_id=job_id,
                        progress=100,
                        total_businesses=total,
                        new_businesses=added,
                        status=COMPLETED_STATUS,
                    )
                    logger.info(
                        "Scan job %s completed on page %s: %s new of %s found",
                        job_id, page + 1, added, total
                    )
                    break
                else:
                    progress = min(99, int(((page + 1) / max_pages) * 100))
                    update_scan_job(
                        db=db,
                        job_id=job_id,
                        progress=progress,
                        total_businesses=total,
                        new_businesses=added,
                        status=RUNNING_STATUS,
                    )

            else:
                update_scan_job(
                    db=db,
                    job_id=job_id,
                    progress=100,
                    total_businesses=total,
                    new_businesses=added,
                    status=COMPLETED_STATUS,
                )
                logger.info(
                    "Scan job %s reached MAX_SCAN_PAGES cap (%s pages): %s new of %s found",
                    job_id, max_pages, added, total
                )

        except GeoapifyError as exc:
            logger.warning("Scan job %s failed upstream: %s", job_id, exc)
            _mark_failed(db, job_id, progress, total, added)
            raise ScanFailed(str(exc), job_id=job_id, upstream=True) from exc

        except Exception as exc:
            logger.exception("Scan job %s failed unexpectedly", job_id)
            _mark_failed(db, job_id, progress, total, added)
            raise ScanFailed(str(exc), job_id=job_id, upstream=False) from exc

    finally:
        db.close()

    return job_id
