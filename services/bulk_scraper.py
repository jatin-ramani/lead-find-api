import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Sequence, Tuple

from sqlalchemy.orm import Session

from database.db import SessionLocal
from database.crud import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    clear_scrape_job_current_business,
    get_failed_scrape_targets,
    get_scrape_job,
    get_scrape_targets,
    get_selected_scrape_targets,
    save_website_data,
    update_scrape_job,
)

from services.website_scraper import scrape_website

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _summary(job_id, total, completed, success, failed, status) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "status": status,
        "total_websites": total,
        "completed": completed,
        "success": success,
        "failed": failed,
    }


def _finalise(db, job_id, status, completed, success, failed) -> None:
    """Put the job in a terminal state and drop the stale progress pointer."""

    update_scrape_job(
        db=db,
        job_id=job_id,
        status=status,
        progress=100 if status == "Completed" else None,
        completed=completed,
        success=success,
        failed=failed,
        completed_at=_utcnow(),
    )

    # Must be a separate call: update_scrape_job ignores None arguments, so it
    # can set current_business_id but never clear it.
    clear_scrape_job_current_business(db, job_id)


# A collector takes the session and returns the (business_id, website) pairs
# to scrape. It is the only thing that varies between the entry points below.
TargetCollector = Callable[[Session], List[Tuple[int, str]]]


def _run_scrape_job(
    job_id: int,
    collect_targets: TargetCollector,
    label: str = "",
) -> Dict[str, Any]:
    """
    Shared engine behind every scrape entry point.

    Only the target list differs between them, so they pass a collector and
    reuse this loop, its error handling and its job bookkeeping verbatim.
    """

    db = SessionLocal()

    total = 0
    completed = 0
    success = 0
    failed = 0

    try:
        if get_scrape_job(db, job_id) is None:
            logger.warning("Scrape job %s does not exist; nothing to do.", job_id)
            return _summary(job_id, 0, 0, 0, 0, "Missing")

        update_scrape_job(
            db=db,
            job_id=job_id,
            status="Running",
            progress=0,
            started_at=_utcnow(),
        )

        targets: List[Tuple[int, str]] = collect_targets(db)
        total = len(targets)

        logger.info(
            "Scrape job %s starting: %s website(s)%s.",
            job_id,
            total,
            f" ({label})" if label else "",
        )

        for business_id, website in targets:

            try:
                update_scrape_job(
                    db=db,
                    job_id=job_id,
                    current_business_id=business_id,
                )

                result = scrape_website(website)

                if result.get("success"):
                    save_website_data(
                        db=db,
                        business_id=business_id,
                        title=result.get("title"),
                        meta_description=result.get("meta_description"),
                        emails=result.get("emails"),
                        facebook=result.get("facebook"),
                        instagram=result.get("instagram"),
                        linkedin=result.get("linkedin"),
                        twitter=result.get("twitter"),
                        youtube=result.get("youtube"),
                        whatsapp=result.get("whatsapp"),
                        status=COMPLETED_STATUS,
                    )
                    success += 1
                else:
                    # Same upserted row, but only status and scraped_at are
                    # touched: a site being down today must not wipe the data
                    # an earlier successful run captured.
                    save_website_data(
                        db=db,
                        business_id=business_id,
                        status=FAILED_STATUS,
                        update_content=False,
                    )
                    failed += 1
                    logger.info(
                        "Scrape job %s: business %s failed — %s",
                        job_id,
                        business_id,
                        result.get("error"),
                    )

            except Exception:
                # A database error leaves the session unusable until it is
                # rolled back, which would cascade into every later business.
                db.rollback()
                failed += 1
                logger.exception(
                    "Scrape job %s: unexpected error on business %s.",
                    job_id,
                    business_id,
                )

            completed += 1

            try:
                update_scrape_job(
                    db=db,
                    job_id=job_id,
                    completed=completed,
                    success=success,
                    failed=failed,
                    progress=int(completed / total * 100) if total else 100,
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "Scrape job %s: could not record progress.", job_id
                )

        _finalise(db, job_id, "Completed", completed, success, failed)

        logger.info(
            "Scrape job %s finished: %s succeeded, %s failed.",
            job_id,
            success,
            failed,
        )

        return _summary(job_id, total, completed, success, failed, "Completed")

    except Exception:
        # Without this the job would sit on "Running" forever after a crash.
        logger.exception("Scrape job %s aborted.", job_id)

        try:
            db.rollback()
            _finalise(db, job_id, "Failed", completed, success, failed)
        except Exception:
            logger.exception(
                "Scrape job %s: could not record the failure.", job_id
            )

        return _summary(job_id, total, completed, success, failed, "Failed")

    finally:
        db.close()


def scrape_all_websites(job_id: int) -> Dict[str, Any]:
    """Scrape every business that has a website."""

    return _run_scrape_job(
        job_id,
        lambda db: get_scrape_targets(db),
    )


def scrape_missing_websites(job_id: int) -> Dict[str, Any]:
    """Scrape only businesses that have a website but no website_data yet."""

    return _run_scrape_job(
        job_id,
        lambda db: get_scrape_targets(db, only_missing=True),
        label="missing only",
    )


def scrape_failed_websites(job_id: int) -> Dict[str, Any]:
    """Retry only businesses whose most recent scrape ended in "Failed"."""

    return _run_scrape_job(
        job_id,
        get_failed_scrape_targets,
        label="retry failed",
    )


def scrape_selected_websites(
    job_id: int,
    business_ids: Sequence[int],
) -> Dict[str, Any]:
    """Scrape only the requested businesses."""

    # Snapshot the ids now: the task runs after the request has ended, so the
    # collector must not close over anything request-scoped.
    selected = list(business_ids)

    return _run_scrape_job(
        job_id,
        lambda db: get_selected_scrape_targets(db, selected),
        label="selected",
    )
