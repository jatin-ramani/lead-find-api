import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, case, func, or_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Query, Session

from config import settings
from database.models import Business, BusinessTag, ScanJob, ScrapeJob, Tag, WebsiteData
from services.lead_scoring import calculate_lead_score


# ======================================================
# BUSINESS CRUD
# ======================================================

DEFAULT_PAGE_SIZE = settings.DEFAULT_PAGE_SIZE
MAX_PAGE_SIZE = settings.MAX_PAGE_SIZE

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
    "lead_status": Business.lead_status,
    "lead_score": Business.lead_score,
    "lead_grade": Business.lead_grade,
    "is_favorite": Business.is_favorite,
}

SORT_ORDERS = ("asc", "desc")

DEFAULT_SORT_BY = "id"
DEFAULT_SORT_ORDER = "desc"

# website_data.status values written by the scrapers.
COMPLETED_STATUS = "Completed"
FAILED_STATUS = "Failed"

# Job lifecycle states, shared by scan_jobs and scrape_jobs.
PENDING_STATUS = "Pending"
RUNNING_STATUS = "Running"

# A website counts as present only when it is a non-blank string; the same
# rule the scrape target query uses, so the dashboard cannot disagree with it.
HAS_WEBSITE = and_(
    Business.website.isnot(None),
    func.trim(Business.website, " \t\r\n") != "",
)

NO_WEBSITE = or_(
    Business.website.is_(None),
    func.trim(Business.website, " \t\r\n") == "",
)

HAS_EMAIL = and_(
    Business.email.isnot(None),
    func.trim(Business.email, " \t\r\n") != "",
)

NO_EMAIL = or_(
    Business.email.is_(None),
    func.trim(Business.email, " \t\r\n") == "",
)

HAS_PHONE = and_(
    Business.phone.isnot(None),
    func.trim(Business.phone, " \t\r\n") != "",
)

NO_PHONE = or_(
    Business.phone.is_(None),
    func.trim(Business.phone, " \t\r\n") == "",
)


def _count_if(condition) -> Any:
    """SUM(CASE WHEN ... THEN 1 ELSE 0 END) — one column of a grouped count."""

    return func.sum(case((condition, 1), else_=0))


def _as_int(value: Any) -> int:
    """SUM() is NULL on an empty table; normalise that to 0."""

    return int(value or 0)


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
    has_website: Optional[bool] = None,
    has_email: Optional[bool] = None,
    has_phone: Optional[bool] = None,
    lead_grade: Optional[str] = None,
    min_lead_score: Optional[int] = None,
    max_lead_score: Optional[int] = None,
    tags: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    lead_status: Optional[str] = None,
    business_ids: Optional[Sequence[int]] = None,
) -> Query:
    """Apply the optional search, contact, lead score, tag, favorite, lead status and filter clauses to a Business query."""

    search = _clean(search)
    city = _clean(city)
    category = _clean(category)
    lead_grade = _clean(lead_grade)
    tags = _clean(tags)
    lead_status = _clean(lead_status)

    if business_ids is not None:
        unique_ids = {int(b_id) for b_id in business_ids}
        if not unique_ids:
            return query.filter(text("1 = 0"))
        query = query.filter(Business.id.in_(unique_ids))

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

    if has_website is True:
        query = query.filter(HAS_WEBSITE)
    elif has_website is False:
        query = query.filter(NO_WEBSITE)

    if has_email is True:
        query = query.filter(HAS_EMAIL)
    elif has_email is False:
        query = query.filter(NO_EMAIL)

    if has_phone is True:
        query = query.filter(HAS_PHONE)
    elif has_phone is False:
        query = query.filter(NO_PHONE)

    if is_favorite is True:
        query = query.filter(Business.is_favorite.is_(True))
    elif is_favorite is False:
        query = query.filter(Business.is_favorite.is_(False))

    if lead_status:
        query = query.filter(Business.lead_status == lead_status.lower())

    if lead_grade:
        query = query.filter(Business.lead_grade == lead_grade.upper())
    if min_lead_score is not None:
        query = query.filter(Business.lead_score >= min_lead_score)
    if max_lead_score is not None:
        query = query.filter(Business.lead_score <= max_lead_score)

    if tags:
        tokens = [t.strip() for t in tags.split(",") if t.strip()]
        for token in tokens:
            token_slug = token.lower().replace(" ", "-")
            query = query.filter(
                Business.tags.any(
                    or_(
                        Tag.slug == token_slug,
                        Tag.name.ilike(token),
                        Tag.id == int(token) if token.isdigit() else text("1=0"),
                    )
                )
            )

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

    initial_score = calculate_lead_score({
        "name": name,
        "phone": phone,
        "email": email,
        "website": website,
        "city": city,
        "category": category,
        "address": address,
    })

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
        lead_score=initial_score.score,
        lead_grade=initial_score.grade,
    )

    db.add(business)
    db.flush()

    from services.activity_service import ACTIVITY_BUSINESS_CREATED, create_activity
    create_activity(
        db=db,
        business_id=business.id,
        activity_type=ACTIVITY_BUSINESS_CREATED,
        title="Business created",
        description="Lead discovered and added to CRM",
        metadata={"city": city, "category": category, "name": name},
        commit=False,
    )

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
    has_website: Optional[bool] = None,
    has_email: Optional[bool] = None,
    has_phone: Optional[bool] = None,
    lead_grade: Optional[str] = None,
    min_lead_score: Optional[int] = None,
    max_lead_score: Optional[int] = None,
    tags: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    lead_status: Optional[str] = None,
    sort_by: Optional[str] = DEFAULT_SORT_BY,
    sort_order: Optional[str] = DEFAULT_SORT_ORDER,
    business_ids: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """
    Return a page of businesses together with its pagination metadata.

    `search` matches name, phone, email or website. `city`, `category`,
    `status`, `lead_status` and `contact` are exact-match filters. `sort_by` accepts id, name,
    city, category, status, lead_status, lead_score, or is_favorite; `sort_order` accepts asc or desc. All are optional
    and invalid values fall back to the defaults.
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
        has_website=has_website,
        has_email=has_email,
        has_phone=has_phone,
        lead_grade=lead_grade,
        min_lead_score=min_lead_score,
        max_lead_score=max_lead_score,
        tags=tags,
        is_favorite=is_favorite,
        lead_status=lead_status,
        business_ids=business_ids,
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


def _scrape_target_query(db: Session, only_missing: bool = False) -> Query:
    """
    Businesses the scrapers should visit: those with a non-empty website.

    With `only_missing`, narrows to businesses that have no website_data row
    yet — an outer join filtered on a NULL right-hand side.
    """

    query = (
        db.query(Business)
        .filter(
            Business.website.isnot(None),
            func.trim(Business.website, " \t\r\n") != "",
        )
    )

    if only_missing:
        query = (
            query
            .outerjoin(WebsiteData, WebsiteData.business_id == Business.id)
            .filter(WebsiteData.id.is_(None))
        )

    return query


def count_scrape_targets(db: Session, only_missing: bool = False) -> int:

    return _scrape_target_query(db, only_missing).count()


def get_businesses_by_ids(
    db: Session,
    business_ids: Sequence[int],
    has_email: bool = False,
    has_phone: bool = False,
) -> List[Business]:
    """
    Fetch the requested businesses, ordered by id.

    Duplicates collapse via the set, unknown ids simply match nothing, and
    an optional contact requirement filters down to valid contacts.
    """

    unique_ids = {int(business_id) for business_id in business_ids}

    if not unique_ids:
        return []

    query = _apply_business_filters(
        db.query(Business).filter(Business.id.in_(unique_ids)),
        has_email=True if has_email else None,
        has_phone=True if has_phone else None,
    )

    return (
        query
        .order_by(Business.id.asc())
        .all()
    )


def count_businesses(
    db: Session,
    *,
    search: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    has_website: Optional[bool] = None,
    has_email: Optional[bool] = None,
    has_phone: Optional[bool] = None,
    lead_grade: Optional[str] = None,
    min_lead_score: Optional[int] = None,
    max_lead_score: Optional[int] = None,
    tags: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    lead_status: Optional[str] = None,
    business_ids: Optional[Sequence[int]] = None,
) -> int:
    """Count rows through the canonical business filter path."""
    return _apply_business_filters(
        db.query(Business), search=search, city=city, category=category,
        has_website=has_website, has_email=has_email, has_phone=has_phone,
        lead_grade=lead_grade, min_lead_score=min_lead_score, max_lead_score=max_lead_score,
        tags=tags,
        is_favorite=is_favorite,
        lead_status=lead_status,
        business_ids=business_ids,
    ).count()


def _rows_to_targets(rows: Sequence[Business]) -> List[Tuple[int, str]]:
    """
    Convert Business rows to (id, website) pairs.

    Plain tuples rather than ORM objects: a scrape loop commits repeatedly,
    which expires attached instances, and a business deleted mid-run would then
    raise on attribute access.
    """

    targets: List[Tuple[int, str]] = []

    for business in rows:
        website = (business.website or "").strip()

        if website:
            targets.append((business.id, website))

    return targets


def get_scrape_targets(
    db: Session,
    only_missing: bool = False,
) -> List[Tuple[int, str]]:
    """Return (business_id, website) pairs for the scrapers."""

    return _rows_to_targets(
        _scrape_target_query(db, only_missing)
        .order_by(Business.id.asc())
        .all()
    )


def get_selected_scrape_targets(
    db: Session,
    business_ids: Sequence[int],
) -> List[Tuple[int, str]]:
    """
    Return (business_id, website) for the requested ids only.

    Duplicates collapse via the set, ids that do not exist simply match no row,
    and the shared base query drops anything without a website — so the caller
    can pass raw user input straight through.
    """

    unique_ids = {int(business_id) for business_id in business_ids}

    if not unique_ids:
        return []

    return _rows_to_targets(
        _scrape_target_query(db)
        .filter(Business.id.in_(unique_ids))
        .order_by(Business.id.asc())
        .all()
    )


def get_failed_scrape_targets(db: Session) -> List[Tuple[int, str]]:
    """
    Return (business_id, website) for businesses whose most recent scrape
    failed.

    "Most recent" is the highest website_data.id per business, so a business
    that failed once and later succeeded is correctly left out.
    """

    latest_ids = (
        db.query(func.max(WebsiteData.id))
        .group_by(WebsiteData.business_id)
        .scalar_subquery()
    )

    return _rows_to_targets(
        _scrape_target_query(db)
        .join(WebsiteData, WebsiteData.business_id == Business.id)
        .filter(WebsiteData.id.in_(latest_ids))
        .filter(WebsiteData.status == FAILED_STATUS)
        .order_by(Business.id.asc())
        .all()
    )


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


def delete_businesses(db: Session, business_ids: Sequence[int]) -> int:
    """
    Delete the requested businesses in one transaction and return how many
    rows were removed.

    Duplicates collapse via the set and unknown ids simply match nothing, so
    raw user input can be passed straight through.
    """

    unique_ids = {int(business_id) for business_id in business_ids}

    if not unique_ids:
        return 0

    businesses = (
        db.query(Business)
        .filter(Business.id.in_(unique_ids))
        .all()
    )

    if not businesses:
        return 0

    # Deleted one object at a time on purpose: a bulk query.delete() runs as a
    # single DELETE statement and skips the ORM, so the website_data cascade
    # would never fire and those rows would be orphaned.
    for business in businesses:
        db.delete(business)

    # One commit for the whole set, so a failure part-way leaves nothing behind.
    db.commit()

    return len(businesses)


def get_city_summaries(db: Session) -> List[Dict[str, Any]]:
    """
    Aggregate discovered businesses by city.
    Returns counts and qualification breakdowns for each city.
    """
    rows = (
        db.query(
            func.coalesce(Business.city, "Unknown").label("city"),
            func.count(Business.id).label("total_businesses"),
            _count_if(HAS_WEBSITE).label("with_website"),
            _count_if(NO_WEBSITE).label("without_website"),
            _count_if(HAS_EMAIL).label("with_email"),
            _count_if(NO_EMAIL).label("without_email"),
            _count_if(HAS_PHONE).label("with_phone"),
            _count_if(NO_PHONE).label("without_phone"),
            _count_if(and_(NO_WEBSITE, or_(HAS_EMAIL, HAS_PHONE))).label("actionable_leads"),
            func.avg(Business.lead_score).label("average_lead_score"),
            _count_if(Business.lead_grade == "A").label("high_quality_leads"),
        )
        .group_by(Business.city)
        .order_by(func.count(Business.id).desc(), Business.city.asc())
        .all()
    )

    return [
        {
            "city": row.city,
            "totalBusinesses": _as_int(row.total_businesses),
            "withWebsite": _as_int(row.with_website),
            "withoutWebsite": _as_int(row.without_website),
            "withEmail": _as_int(row.with_email),
            "withoutEmail": _as_int(row.without_email),
            "withPhone": _as_int(row.with_phone),
            "withoutPhone": _as_int(row.without_phone),
            "actionableLeads": _as_int(row.actionable_leads),
            "averageLeadScore": round(float(row.average_lead_score or 0), 1),
            "highQualityLeads": _as_int(row.high_quality_leads),
        }
        for row in rows
        if row.total_businesses > 0
    ]


# ======================================================
# SCAN JOB CRUD
# ======================================================

def create_scan_job(db: Session, city: str, category: str):

    job = ScanJob(
        city=city,
        category=category,
        status=RUNNING_STATUS,
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
# SCRAPE JOB CRUD
# ======================================================

class ScrapeJobConflictError(Exception):
    """Raised when creating a scrape job fails due to an existing Pending or Running job."""

    def __init__(self, active_job_id: Optional[int] = None):
        self.active_job_id = active_job_id
        super().__init__("A scrape job is already running.")


def get_active_scrape_job(db: Session) -> Optional[ScrapeJob]:
    """The scrape job currently Pending or Running, or None if the queue is idle."""

    return (
        db.query(ScrapeJob)
        .filter(ScrapeJob.status.in_([PENDING_STATUS, RUNNING_STATUS]))
        .order_by(ScrapeJob.id.desc())
        .first()
    )


def create_scrape_job(db: Session, total_websites: int) -> int:
    """
    Create a new scrape job in Pending status.
    Protected at the database level by partial unique index uq_scrape_jobs_single_active.
    If an active job already exists, catches the integrity error, rolls back cleanly,
    and raises ScrapeJobConflictError.
    """

    job = ScrapeJob(
        status=PENDING_STATUS,
        started_at=datetime.now(timezone.utc),
        progress=0,
        total_websites=total_websites,
        completed=0,
        success=0,
        failed=0,
    )

    db.add(job)
    try:
        db.commit()
        db.refresh(job)
        return job.id
    except IntegrityError as exc:
        db.rollback()
        err_msg = str(exc).lower()
        if (
            "uq_scrape_jobs_single_active" in err_msg
            or ("unique constraint failed" in err_msg and "scrape_jobs" in err_msg)
            or ("duplicate key" in err_msg and "scrape_jobs" in err_msg)
            or "unique constraint" in err_msg
        ):
            active = get_active_scrape_job(db)
            raise ScrapeJobConflictError(active.id if active else None) from exc
        raise


def update_scrape_job(
    db: Session,
    job_id: int,
    progress: Optional[int] = None,
    completed: Optional[int] = None,
    success: Optional[int] = None,
    failed: Optional[int] = None,
    status: Optional[str] = None,
    current_business_id: Optional[int] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
):

    job = (
        db.query(ScrapeJob)
        .filter(ScrapeJob.id == job_id)
        .first()
    )

    if not job:
        return None

    if progress is not None:
        job.progress = progress

    if completed is not None:
        job.completed = completed

    if success is not None:
        job.success = success

    if failed is not None:
        job.failed = failed

    if status is not None:
        job.status = status

    if current_business_id is not None:
        job.current_business_id = current_business_id

    if started_at is not None:
        job.started_at = started_at

    if completed_at is not None:
        job.completed_at = completed_at

    db.commit()
    db.refresh(job)

    return job


def get_running_scrape_job(db: Session):
    """The scrape job currently in flight, or None if the queue is idle."""

    return get_active_scrape_job(db)


def clear_scrape_job_current_business(db: Session, job_id: int):
    """
    Blank out current_business_id.

    update_scrape_job() skips None arguments, so it can set that column but
    never unset it. A finished job needs it cleared or the UI keeps pointing at
    the last business the run touched.
    """

    job = (
        db.query(ScrapeJob)
        .filter(ScrapeJob.id == job_id)
        .first()
    )

    if not job:
        return None

    job.current_business_id = None

    db.commit()
    db.refresh(job)

    return job


def get_scrape_jobs(db: Session):

    return (
        db.query(ScrapeJob)
        .order_by(ScrapeJob.id.desc())
        .all()
    )


def get_scrape_job(db: Session, job_id: int):

    return (
        db.query(ScrapeJob)
        .filter(ScrapeJob.id == job_id)
        .first()
    )


def get_latest_scrape_job(db: Session):

    return (
        db.query(ScrapeJob)
        .order_by(ScrapeJob.id.desc())
        .first()
    )


def delete_scrape_job(db: Session, job_id: int):

    job = get_scrape_job(db, job_id)

    if not job:
        return False

    db.delete(job)
    db.commit()

    return True


def get_scrape_job_results(
    db: Session,
    job_id: int,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: Optional[str] = None,
    city: Optional[str] = None,
    search: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return paginated website records scraped during a specific scrape job,
    along with job summary and city breakdown.
    """
    job = get_scrape_job(db, job_id)
    if job is None:
        return None

    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    summary = {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "total_websites": job.total_websites,
        "completed": job.completed,
        "success": job.success,
        "failed": job.failed,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }

    if not job.started_at:
        return {
            "success": True,
            "data": [],
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "totalItems": 0,
                "totalPages": 1,
            },
            "summary": summary,
            "cities": [],
        }

    # Find the upper bound time window for this job
    start_time = job.started_at
    next_job = (
        db.query(ScrapeJob)
        .filter(ScrapeJob.id != job.id, ScrapeJob.started_at > start_time)
        .order_by(ScrapeJob.started_at.asc())
        .first()
    )

    base_query = (
        db.query(WebsiteData, Business)
        .join(Business, WebsiteData.business_id == Business.id)
        .filter(WebsiteData.scraped_at >= start_time)
    )

    if next_job and next_job.started_at:
        base_query = base_query.filter(WebsiteData.scraped_at < next_job.started_at)
    elif job.completed_at:
        base_query = base_query.filter(WebsiteData.scraped_at <= job.completed_at + timedelta(seconds=10))

    # Calculate city breakdown from all results in this job before pagination
    all_job_items = base_query.all()
    city_counts: Dict[str, Dict[str, int]] = {}
    for w_data, b_info in all_job_items:
        c_name = b_info.city or "Unknown"
        if c_name not in city_counts:
            city_counts[c_name] = {"count": 0, "success": 0, "failed": 0}
        city_counts[c_name]["count"] += 1
        if w_data.status == COMPLETED_STATUS:
            city_counts[c_name]["success"] += 1
        else:
            city_counts[c_name]["failed"] += 1

    cities_list = [
        {"city": k, "count": v["count"], "success": v["success"], "failed": v["failed"]}
        for k, v in sorted(city_counts.items(), key=lambda x: x[1]["count"], reverse=True)
    ]

    filtered_query = base_query
    status_clean = _clean(status)
    if status_clean:
        filtered_query = filtered_query.filter(WebsiteData.status == status_clean)

    city_clean = _clean(city)
    if city_clean:
        filtered_query = filtered_query.filter(Business.city == city_clean)

    search_clean = _clean(search)
    if search_clean:
        pattern = f"%{_escape_like(search_clean)}%"
        filtered_query = filtered_query.filter(
            or_(
                Business.name.ilike(pattern, escape="\\"),
                Business.website.ilike(pattern, escape="\\"),
                WebsiteData.title.ilike(pattern, escape="\\"),
            )
        )

    total_items = filtered_query.count()
    total_pages = max(1, (total_items + page_size - 1) // page_size)

    rows = (
        filtered_query
        .order_by(WebsiteData.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for w_data, b_info in rows:
        emails_list = []
        if w_data.emails:
            try:
                parsed = json.loads(w_data.emails)
                if isinstance(parsed, list):
                    emails_list = parsed
            except Exception:
                emails_list = [w_data.emails]

        items.append({
            "id": w_data.id,
            "business_id": b_info.id,
            "business_name": b_info.name,
            "business_city": b_info.city or "Unknown",
            "business_category": b_info.category or "General",
            "business_phone": b_info.phone,
            "website": b_info.website,
            "status": w_data.status,
            "title": w_data.title,
            "meta_description": w_data.meta_description,
            "emails": emails_list,
            "facebook": w_data.facebook,
            "instagram": w_data.instagram,
            "linkedin": w_data.linkedin,
            "youtube": w_data.youtube,
            "twitter": w_data.twitter,
            "whatsapp": w_data.whatsapp,
            "scraped_at": w_data.scraped_at,
            "failure_reason": "Failed to connect to website or extract content." if w_data.status == FAILED_STATUS else None,
        })

    return {
        "success": True,
        "data": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "totalItems": total_items,
            "totalPages": total_pages,
        },
        "summary": summary,
        "cities": cities_list,
    }


# ======================================================
# DASHBOARD STATS
# ======================================================

def _scan_job_summary(job: Optional[ScanJob]) -> Optional[Dict[str, Any]]:

    if job is None:
        return None

    return {
        "id": job.id,
        "city": job.city,
        "category": job.category,
        "status": job.status,
        "progress": job.progress,
        "total_businesses": job.total_businesses,
        "new_businesses": job.new_businesses,
    }


def _scrape_job_summary(job: Optional[ScrapeJob]) -> Optional[Dict[str, Any]]:

    if job is None:
        return None

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


def get_dashboard_stats(db: Session) -> Dict[str, Any]:
    """
    Every dashboard figure, grouped by the table it describes.

    Each group is one round trip: the per-status counts are conditional
    aggregates in a single SELECT rather than a COUNT query per status.
    """

    # --- Business -------------------------------------------------------
    (business_total, business_with_website, business_with_email,
     business_with_phone, actionable_leads) = db.query(
        func.count(Business.id),
        _count_if(HAS_WEBSITE),
        _count_if(HAS_EMAIL),
        _count_if(HAS_PHONE),
        _count_if(and_(NO_WEBSITE, or_(HAS_EMAIL, HAS_PHONE))),
    ).one()

    business_total = _as_int(business_total)
    with_website = _as_int(business_with_website)
    with_email = _as_int(business_with_email)
    with_phone = _as_int(business_with_phone)

    # --- Website data ---------------------------------------------------
    # Scored on the most recent row per business, matching how
    # get_failed_scrape_targets() decides what still needs retrying.
    latest_ids = (
        db.query(func.max(WebsiteData.id))
        .group_by(WebsiteData.business_id)
        .scalar_subquery()
    )

    scraped_total, scraped_completed, scraped_failed = (
        db.query(
            func.count(WebsiteData.id),
            _count_if(WebsiteData.status == COMPLETED_STATUS),
            _count_if(WebsiteData.status == FAILED_STATUS),
        )
        .filter(WebsiteData.id.in_(latest_ids))
        .one()
    )

    # --- Scrape jobs ----------------------------------------------------
    scrape_total, scrape_running, scrape_completed, scrape_failed = (
        db.query(
            func.count(ScrapeJob.id),
            _count_if(ScrapeJob.status == RUNNING_STATUS),
            _count_if(ScrapeJob.status == COMPLETED_STATUS),
            _count_if(ScrapeJob.status == FAILED_STATUS),
        ).one()
    )

    # --- Scan jobs ------------------------------------------------------
    scan_total, scan_running, scan_completed = (
        db.query(
            func.count(ScanJob.id),
            _count_if(ScanJob.status == RUNNING_STATUS),
            _count_if(ScanJob.status == COMPLETED_STATUS),
        ).one()
    )

    return {
        "business": {
            "totalBusinesses": business_total,
            "withWebsite": with_website,
            "withoutWebsite": business_total - with_website,
            "withEmail": with_email,
            "withoutEmail": business_total - with_email,
            "withPhone": with_phone,
            "actionableLeads": _as_int(actionable_leads),
        },
        "websiteData": {
            "completed": _as_int(scraped_completed),
            "failed": _as_int(scraped_failed),
            # Has a website but no website_data row yet — the same set
            # POST /scrape/missing would pick up.
            "pending": count_scrape_targets(db, only_missing=True),
            "totalScraped": _as_int(scraped_total),
        },
        "scrapeJobs": {
            "total": _as_int(scrape_total),
            "running": _as_int(scrape_running),
            "completed": _as_int(scrape_completed),
            "failed": _as_int(scrape_failed),
        },
        "scanJobs": {
            "total": _as_int(scan_total),
            "running": _as_int(scan_running),
            "completed": _as_int(scan_completed),
        },
        "latestScanJob": _scan_job_summary(get_latest_scan_job(db)),
        "latestScrapeJob": _scrape_job_summary(get_latest_scrape_job(db)),
    }

def get_latest_scan_job(db: Session):
    return (
        db.query(ScanJob)
        .order_by(ScanJob.id.desc())
        .first()
    )


# ======================================================
# SYSTEM / HEALTH
# ======================================================

def check_database_connection(db: Session) -> bool:
    """
    Cheapest possible round trip proving the database answers.

    Returns False rather than raising, so a health probe can report the outcome
    instead of turning a failed check into a 500.
    """

    try:
        db.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


# ======================================================
# WEBSITE DATA CRUD
# ======================================================

def get_website_data(db: Session, business_id: int) -> Optional[WebsiteData]:

    return (
        db.query(WebsiteData)
        .filter(WebsiteData.business_id == business_id)
        .order_by(WebsiteData.id.desc())
        .first()
    )


def save_website_data(
    db: Session,
    business_id: int,
    title: Optional[str] = None,
    meta_description: Optional[str] = None,
    emails: Optional[Sequence[str]] = None,
    facebook: Optional[str] = None,
    instagram: Optional[str] = None,
    linkedin: Optional[str] = None,
    twitter: Optional[str] = None,
    youtube: Optional[str] = None,
    whatsapp: Optional[str] = None,
    status: str = COMPLETED_STATUS,
    update_content: bool = True,
) -> WebsiteData:
    """
    Insert the scrape result for a business, or overwrite the existing row.

    `emails` is stored as a JSON string so the column keeps a single, parseable
    format regardless of how many addresses were found.

    Set `update_content=False` to record only `status` and `scraped_at` — used
    when a scrape fails, so a site being unreachable today does not wipe the
    title, e-mails and social links captured by an earlier successful run.
    """

    record = get_website_data(db, business_id)

    if record is None:
        record = WebsiteData(business_id=business_id)
        db.add(record)

    if update_content:
        record.title = title
        record.meta_description = meta_description
        record.emails = json.dumps(list(emails or []))
        record.facebook = facebook
        record.instagram = instagram
        record.linkedin = linkedin
        record.twitter = twitter
        record.youtube = youtube
        record.whatsapp = whatsapp

    record.status = status

    # Column defaults only fire on INSERT, so an update needs this set by hand.
    record.scraped_at = datetime.now(timezone.utc)

    # Recalculate parent business lead_score and lead_grade
    business = db.query(Business).filter(Business.id == business_id).first()
    if business:
        prev_score = business.lead_score
        prev_grade = business.lead_grade
        scored = calculate_lead_score(business, record)
        business.lead_score = scored.score
        business.lead_grade = scored.grade
        db.add(business)

        from services.activity_service import (
            ACTIVITY_EMAIL_ADDED,
            ACTIVITY_LEAD_SCORE_CHANGED,
            ACTIVITY_SCRAPE_COMPLETED,
            create_activity,
        )

        # Observational email activity if previously missing
        if emails and not business.email:
            create_activity(
                db=db,
                business_id=business.id,
                activity_type=ACTIVITY_EMAIL_ADDED,
                title="Email found",
                description="Discovered email address from website scrape",
                metadata={"source": "scraper"},
                commit=False,
            )

        # Observational lead score change activity
        if scored.score != prev_score or scored.grade != prev_grade:
            create_activity(
                db=db,
                business_id=business.id,
                activity_type=ACTIVITY_LEAD_SCORE_CHANGED,
                title=f"Lead score changed to {scored.score}",
                description=f"Score updated from {prev_score} ({prev_grade}) to {scored.score} ({scored.grade})",
                metadata={
                    "previous_score": prev_score,
                    "new_score": scored.score,
                    "previous_grade": prev_grade,
                    "new_grade": scored.grade,
                },
                commit=False,
            )

        # Scrape completed activity
        if status == COMPLETED_STATUS:
            fields_updated = []
            if emails:
                fields_updated.append("email")
            if facebook or instagram or linkedin or twitter or youtube or whatsapp:
                fields_updated.append("social_links")
            if title or meta_description:
                fields_updated.append("meta_data")
            create_activity(
                db=db,
                business_id=business.id,
                activity_type=ACTIVITY_SCRAPE_COMPLETED,
                title="Website scrape completed",
                description="Successfully scraped website content and metadata",
                metadata={"source": "scraper", "fields_updated": fields_updated},
                commit=False,
            )

    db.commit()
    db.refresh(record)

    return record
