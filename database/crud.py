from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from database.models import Business, ScanJob


# ======================================================
# BUSINESS CRUD
# ======================================================

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Columns the free-text search runs across.
SEARCHABLE_COLUMNS = (
    Business.name,
    Business.phone,
    Business.email,
    Business.website,
)

# Whitelist of sortable columns. Anything outside this map falls back to the
# default, so an arbitrary query string can never reach the ORDER BY clause.
SORTABLE_COLUMNS: Dict[str, Any] = {
    "id": Business.id,
    "name": Business.name,
    "city": Business.city,
    "category": Business.category,
    "status": Business.status,
}

SORT_ORDERS = ("asc", "desc")

DEFAULT_SORT_BY = "id"
DEFAULT_SORT_ORDER = "desc"


def _clean(value: Optional[str]) -> Optional[str]:
    """Trim a filter value and treat blank strings as "no filter"."""
    if value is None:
        return None

    cleaned = value.strip()

    return cleaned or None


def _escape_like(term: str) -> str:
    """
    Escape LIKE wildcards so a literal % or _ in a search term is matched
    literally instead of behaving as a pattern.
    """
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _apply_business_filters(
    query: Query,
    search: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> Query:
    """Apply the optional search and filter clauses to a Business query."""

    search = _clean(search)
    city = _clean(city)
    category = _clean(category)
    status = _clean(status)

    if search:
        pattern = f"%{_escape_like(search)}%"

        query = query.filter(
            or_(
                *[
                    column.ilike(pattern, escape="\\")
                    for column in SEARCHABLE_COLUMNS
                ]
            )
        )

    if city:
        query = query.filter(Business.city == city)

    if category:
        query = query.filter(Business.category == category)

    if status:
        query = query.filter(Business.status == status)

    return query


def _resolve_ordering(
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> List[Any]:
    """
    Translate the requested sort into ORDER BY clauses.

    Unknown columns fall back to `id` and unknown directions to `desc`, so a
    bad value degrades to the default instead of raising.
    """

    key = (_clean(sort_by) or "").lower()
    column = SORTABLE_COLUMNS.get(key, SORTABLE_COLUMNS[DEFAULT_SORT_BY])

    direction = (_clean(sort_order) or "").lower()

    if direction not in SORT_ORDERS:
        direction = DEFAULT_SORT_ORDER

    primary = column.asc() if direction == "asc" else column.desc()

    if column is SORTABLE_COLUMNS[DEFAULT_SORT_BY]:
        return [primary]

    # name/city/category/status are not unique, so rows sharing a value have no
    # defined order of their own. Without a tiebreaker a record can repeat on
    # one page and be missing from the next.
    return [primary, Business.id.desc()]


def save_business(
    db: Session,
    name: Optional[str],
    phone: Optional[str],
    email: Optional[str],
    website: Optional[str],
    city: Optional[str],
    category: Optional[str],
    address: Optional[str],
    status: Optional[str],
    place_id: Optional[str],
) -> bool:
    # `WHERE place_id = NULL` never matches, so the lookup is only meaningful
    # when we actually have an id to match on.
    if place_id:

        existing = (
            db.query(Business)
            .filter(Business.place_id == place_id)
            .first()
        )

        if existing:
            return False

    business = Business(
        name=name,
        phone=phone,
        email=email,
        website=website,
        city=city,
        category=category,
        address=address,
        status=status,
        place_id=place_id,
    )

    db.add(business)
    db.commit()
    db.refresh(business)

    return True


def get_businesses(
    db: Session,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = DEFAULT_SORT_BY,
    sort_order: Optional[str] = DEFAULT_SORT_ORDER,
) -> Dict[str, Any]:
    """
    Return a page of businesses together with its pagination metadata.

    `search` matches name, phone, email or website. `city`, `category` and
    `status` are exact-match filters. `sort_by` accepts id, name, city,
    category or status; `sort_order` accepts asc or desc. All are optional and
    invalid values fall back to the defaults.
    """

    # Clamp the paging inputs so a bad query string cannot request a negative
    # offset or pull the whole table in one response.
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    query = _apply_business_filters(
        db.query(Business),
        search=search,
        city=city,
        category=category,
        status=status,
    )

    total_items = query.count()

    # Integer ceiling, floored at 1 so the client always has a page to render
    # even when the result set is empty.
    total_pages = max(
        1,
        (total_items + page_size - 1) // page_size,
    )

    items: List[Business] = (
        query
        # An explicit order is required for stable paging; without it the
        # database may return rows in a different order per page.
        .order_by(*_resolve_ordering(sort_by, sort_order))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "success": True,
        "data": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "totalItems": total_items,
            "totalPages": total_pages,
        },
    }


def get_business_by_id(db: Session, business_id: int) -> Optional[Business]:
    return (
        db.query(Business)
        .filter(Business.id == business_id)
        .first()
    )


def delete_business(db: Session, business_id: int) -> bool:

    business = get_business_by_id(db, business_id)

    if not business:
        return False

    db.delete(business)
    db.commit()

    return True


# ======================================================
# SCAN JOB CRUD
# ======================================================

def create_scan_job(db: Session, city: str, category: str):

    job = ScanJob(
        city=city,
        category=category,
        status="Running",
        progress=0,
        total_businesses=0,
        new_businesses=0,
    )

    db.add(job)
    db.commit()
    db.refresh(job)

    return job.id


def update_scan_job(
    db: Session,
    job_id: int,
    progress: int = None,
    total_businesses: int = None,
    new_businesses: int = None,
    status: str = None,
):

    job = (
        db.query(ScanJob)
        .filter(ScanJob.id == job_id)
        .first()
    )

    if not job:
        return None

    if progress is not None:
        job.progress = progress

    if total_businesses is not None:
        job.total_businesses = total_businesses

    if new_businesses is not None:
        job.new_businesses = new_businesses

    if status is not None:
        job.status = status

    db.commit()
    db.refresh(job)

    return job


def get_scan_jobs(db: Session):

    return (
        db.query(ScanJob)
        .order_by(ScanJob.id.desc())
        .all()
    )


def get_scan_job(db: Session, job_id: int):

    return (
        db.query(ScanJob)
        .filter(ScanJob.id == job_id)
        .first()
    )


def delete_scan_job(db: Session, job_id: int):

    job = get_scan_job(db, job_id)

    if not job:
        return False

    db.delete(job)
    db.commit()

    return True


# ======================================================
# DASHBOARD STATS
# ======================================================

def get_dashboard_stats(db: Session):

    total_businesses = db.query(Business).count()

    with_website = (
        db.query(Business)
        .filter(Business.website.isnot(None))
        .count()
    )

    without_website = (
        db.query(Business)
        .filter(Business.website.is_(None))
        .count()
    )

    emails_found = (
        db.query(Business)
        .filter(Business.email.isnot(None))
        .count()
    )

    running_scans = (
        db.query(ScanJob)
        .filter(ScanJob.status == "Running")
        .count()
    )

    completed_scans = (
        db.query(ScanJob)
        .filter(ScanJob.status == "Completed")
        .count()
    )

    return {
        "totalBusinesses": total_businesses,
        "withWebsite": with_website,
        "withoutWebsite": without_website,
        "emailsFound": emails_found,
        "runningScans": running_scans,
        "completedScans": completed_scans,
    }

def get_latest_scan_job(db: Session):
    return (
        db.query(ScanJob)
        .order_by(ScanJob.id.desc())
        .first()
    )
