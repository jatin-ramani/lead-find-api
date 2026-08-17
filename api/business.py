import csv
import io
from typing import Iterable, Iterator, List, Optional, Sequence

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from errors import AppError, ErrorCode
from database.db import get_db
from database.crud import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT_BY,
    DEFAULT_SORT_ORDER,
    COMPLETED_STATUS,
    FAILED_STATUS,
    MAX_PAGE_SIZE,
    SORTABLE_COLUMNS,
    SORT_ORDERS,
    count_scrape_targets,
    create_scrape_job,
    get_businesses,
    get_businesses_by_ids,
    get_business_by_id,
    delete_business,
    delete_businesses,
    get_failed_scrape_targets,
    get_running_scrape_job,
    get_selected_scrape_targets,
    get_website_data,
    save_website_data,
)
from database.models import Business

from schemas.business import (
    BulkBusinessRequest,
    BusinessResponse,
    BusinessListResponse,
    ScrapeSelectedRequest,
    WebsiteDataDetailResponse,
)
from schemas.common import (
    PATH_VALIDATION_ERROR_RESPONSE,
    VALIDATION_ERROR_RESPONSE,
    DeletedCountResponse,
    JobStartedResponse,
    MessageResponse,
    conflict_response,
    csv_response,
    error_response,
)

from services.bulk_scraper import (
    scrape_all_websites,
    scrape_failed_websites,
    scrape_missing_websites,
    scrape_selected_websites,
)
from services.website_scraper import scrape_website

router = APIRouter(
    prefix="/businesses",
    tags=["Businesses"],
)

# Bulk scraping is not scoped to a single business, so it sits on its own
# prefix rather than under /businesses. Registered separately in app.py.
scrape_router = APIRouter(
    prefix="/scrape",
    tags=["Website Scraping"],
)

CSV_FILENAME = "businesses.csv"

BUSINESS_NOT_FOUND = error_response(
    "No business with that id.",
    "Business not found",
)

FILTER_DOCS = (
    "Optional filters: `search` matches name, phone, e-mail or website; "
    "`city`, `category` and `status` are exact matches. `sortBy` accepts "
    "id, name, city, category or status and `sortOrder` asc or desc — an "
    "unrecognised value falls back to the default rather than erroring."
)

CSV_COLUMNS = (
    "ID",
    "Name",
    "Phone",
    "Email",
    "Website",
    "City",
    "Category",
    "Address",
    "Status",
)


@router.get(
    "",
    response_model=BusinessListResponse,
    summary="List businesses",
    description=(
        "A page of discovered businesses with pagination metadata.\n\n"
        + FILTER_DOCS
    ),
    response_description="One page of businesses plus pagination metadata.",
    responses={422: VALIDATION_ERROR_RESPONSE},
)
def list_businesses(
    page: int = Query(
        1,
        ge=1,
        description="1-based page number.",
    ),
    pageSize: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Rows per page (max {MAX_PAGE_SIZE}).",
    ),
    search: Optional[str] = Query(
        None,
        description="Matches name, phone, email or website.",
    ),
    city: Optional[str] = Query(
        None,
        description="Exact city match.",
    ),
    category: Optional[str] = Query(
        None,
        description="Exact category match.",
    ),
    status: Optional[str] = Query(
        None,
        description="Exact status match, e.g. 'No Website'.",
    ),
    sortBy: Optional[str] = Query(
        DEFAULT_SORT_BY,
        description=(
            "One of: "
            + ", ".join(SORTABLE_COLUMNS)
            + f". Any other value falls back to '{DEFAULT_SORT_BY}'."
        ),
    ),
    sortOrder: Optional[str] = Query(
        DEFAULT_SORT_ORDER,
        description=(
            "One of: "
            + ", ".join(SORT_ORDERS)
            + f". Any other value falls back to '{DEFAULT_SORT_ORDER}'."
        ),
    ),
    db: Session = Depends(get_db),
):
    return get_businesses(
        db=db,
        page=page,
        page_size=pageSize,
        search=search,
        city=city,
        category=category,
        status=status,
        sort_by=sortBy,
        sort_order=sortOrder,
    )


def _csv_stream(batches: Iterable[Sequence[Business]]) -> Iterator[str]:
    """
    Serialise batches of businesses as CSV, emitting one chunk per batch.

    The app's only CSV generator: each export route supplies a different row
    source, so column order and the BOM cannot drift between them.
    """

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def drain() -> str:
        chunk = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return chunk

    # BOM first so Excel reads the UTF-8 accents in scraped names correctly.
    writer.writerow(CSV_COLUMNS)
    yield "﻿" + drain()

    for batch in batches:

        for business in batch:
            writer.writerow(
                [
                    business.id,
                    business.name,
                    business.phone,
                    business.email,
                    business.website,
                    business.city,
                    business.category,
                    business.address,
                    business.status,
                ]
            )

        yield drain()


def _filtered_business_batches(
    db: Session,
    search: Optional[str],
    city: Optional[str],
    category: Optional[str],
    status: Optional[str],
    sort_by: Optional[str],
    sort_order: Optional[str],
) -> Iterator[List[Business]]:
    """
    Walk the filtered businesses one page at a time.

    Pagination is walked internally rather than bypassed, so the export reuses
    get_businesses() untouched and never holds the whole table in memory.
    """

    page = 1
    total_pages = 1

    while page <= total_pages:

        result = get_businesses(
            db=db,
            page=page,
            page_size=MAX_PAGE_SIZE,
            search=search,
            city=city,
            category=category,
            status=status,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        if page == 1:
            # Fixed on the first page so a concurrent insert cannot keep the
            # loop running indefinitely.
            total_pages = result["pagination"]["totalPages"]

        yield result["data"]

        page += 1


def _selected_business_batches(
    db: Session,
    business_ids: Sequence[int],
) -> Iterator[List[Business]]:
    """
    Fetch the requested businesses in chunks, mirroring the paged export so a
    very large selection is never loaded in one go.
    """

    unique_ids = sorted({int(business_id) for business_id in business_ids})

    for start in range(0, len(unique_ids), MAX_PAGE_SIZE):
        yield get_businesses_by_ids(
            db,
            unique_ids[start:start + MAX_PAGE_SIZE],
        )


def _csv_response(batches: Iterable[Sequence[Business]]) -> StreamingResponse:
    """Wrap the CSV stream so every export route answers with identical headers."""

    return StreamingResponse(
        _csv_stream(batches),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{CSV_FILENAME}"',
        },
    )


@router.get(
    "/export/csv",
    response_class=StreamingResponse,
    summary="Export businesses as CSV",
    description=(
        "Streams every business matching the filters as a UTF-8 CSV "
        "download.\n\n"
        "Pagination does **not** apply - the whole matching set is "
        "exported, streamed in chunks so the table is never held in "
        "memory. A byte-order mark is emitted first so Excel reads "
        "accented names correctly.\n\n"
        + FILTER_DOCS
    ),
    response_description="A streamed businesses.csv attachment.",
    responses={
        200: csv_response("The CSV download."),
        422: VALIDATION_ERROR_RESPONSE,
    },
)
def export_businesses_csv(
    search: Optional[str] = Query(
        None,
        description="Matches name, phone, email or website.",
    ),
    city: Optional[str] = Query(
        None,
        description="Exact city match.",
    ),
    category: Optional[str] = Query(
        None,
        description="Exact category match.",
    ),
    status: Optional[str] = Query(
        None,
        description="Exact status match, e.g. 'No Website'.",
    ),
    sortBy: Optional[str] = Query(
        DEFAULT_SORT_BY,
        description=(
            "One of: "
            + ", ".join(SORTABLE_COLUMNS)
            + f". Any other value falls back to '{DEFAULT_SORT_BY}'."
        ),
    ),
    sortOrder: Optional[str] = Query(
        DEFAULT_SORT_ORDER,
        description=(
            "One of: "
            + ", ".join(SORT_ORDERS)
            + f". Any other value falls back to '{DEFAULT_SORT_ORDER}'."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Export every business matching the filters. Pagination does not apply."""

    return _csv_response(
        _filtered_business_batches(
            db=db,
            search=search,
            city=city,
            category=category,
            status=status,
            sort_by=sortBy,
            sort_order=sortOrder,
        )
    )


@router.post(
    "/export/csv",
    response_class=StreamingResponse,
    summary="Export selected businesses as CSV",
    description=(
        "Streams only the requested businesses, in exactly the format "
        "the GET route produces - same generator, same column order, "
        "same BOM.\n\n"
        "Duplicate ids collapse and unknown ids are skipped, so a "
        "partly-stale selection still exports whatever remains valid."
    ),
    response_description="A streamed businesses.csv attachment.",
    responses={
        200: csv_response("The CSV download."),
        422: VALIDATION_ERROR_RESPONSE,
    },
)
def export_selected_businesses_csv(
    payload: BulkBusinessRequest,
    db: Session = Depends(get_db),
):
    """
    Export only the requested businesses.

    Byte-for-byte the same format as the GET route — same generator, same
    column order, same BOM — just a different row source.
    """

    return _csv_response(
        _selected_business_batches(db, payload.business_ids)
    )


@router.post(
    "/{business_id}/scrape",
    response_model=MessageResponse,
    summary="Scrape one business website",
    description=(
        "Fetches the website for a single business and stores its title, "
        "meta description, e-mail addresses and social links.\n\n"
        "Runs synchronously - the request waits for the scrape, up to a "
        "20 second timeout. A failure is recorded with a `Failed` status "
        "so `POST /scrape/retry-failed` can pick it up later, and any "
        "previously scraped content is left intact."
    ),
    response_description="The website was scraped and the result stored.",
    responses={
        400: error_response(
            "The business has no website to scrape.",
            "Business does not have a website.",
        ),
        404: BUSINESS_NOT_FOUND,
        502: error_response(
            "The business website could not be reached or parsed.",
            "Could not connect to the site.",
        ),
        422: PATH_VALIDATION_ERROR_RESPONSE,
    },
)
def scrape_business_website(
    business_id: int,
    db: Session = Depends(get_db),
):
    """Scrape the business website and store the result in website_data."""

    business = get_business_by_id(db, business_id)

    if not business:
        raise HTTPException(
            status_code=404,
            detail="Business not found",
        )

    website = (business.website or "").strip()

    if not website:
        raise HTTPException(
            status_code=400,
            detail="Business does not have a website.",
        )

    result = scrape_website(website)

    if not result.get("success"):
        # Record the attempt so /scrape/retry-failed can pick it up later.
        # Only status and scraped_at change, leaving any earlier successful
        # scrape of this business intact.
        save_website_data(
            db=db,
            business_id=business.id,
            status=FAILED_STATUS,
            update_content=False,
        )

        # The failure is upstream, not in this request, so it is reported as a
        # bad gateway rather than a client error.
        raise HTTPException(
            status_code=502,
            detail=result.get("error", "Failed to scrape the website."),
        )

    save_website_data(
        db=db,
        business_id=business.id,
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

    return {
        "success": True,
        "message": "Website scraped successfully.",
    }


def _raise_if_job_running(db: Session) -> None:
    """
    Refuse a new scrape while one is in flight.

    Raises rather than returning a response so the 409 goes through the shared
    exception handler and carries the same envelope as every other error; the
    running job id travels in `details`.
    """

    running = get_running_scrape_job(db)

    if running is None:
        return

    raise AppError(
        "A scrape job is already running.",
        status_code=409,
        error=ErrorCode.CONFLICT,
        details={"job_id": running.id},
    )


@scrape_router.post(
    "/all",
    response_model=JobStartedResponse,
    summary="Scrape every business website",
    description=(
        "Queues a background scrape of every business that has a website "
        "and returns immediately with a job id.\n\n"
        "Poll `GET /scrape/jobs/{job_id}` for progress. Only one scrape "
        "job may run at a time - a second request while one is in flight "
        "returns **409** with the running job id."
    ),
    response_description="The bulk scrape was queued.",
    responses={409: conflict_response()},
)
def scrape_all_business_websites(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Queue a bulk scrape of every business website and return immediately."""

    _raise_if_job_running(db)

    # Same query the scraper itself uses, so total_websites always agrees with
    # the number of rows the job actually processes.
    total_websites = count_scrape_targets(db)

    job_id = create_scrape_job(
        db=db,
        total_websites=total_websites,
    )

    # Runs after the response is sent, so the caller is never held open for
    # the length of the scrape.
    background_tasks.add_task(scrape_all_websites, job_id)

    return {
        "success": True,
        "job_id": job_id,
        "message": "Bulk scraping started.",
    }


@scrape_router.post(
    "/missing",
    response_model=JobStartedResponse,
    summary="Scrape businesses never scraped before",
    description=(
        "As `/scrape/all`, but limited to businesses that have a website "
        "and no stored scrape result yet - the usual way to top up after "
        "adding businesses, without re-fetching everything."
    ),
    response_description="The scrape was queued.",
    responses={409: conflict_response()},
)
def scrape_missing_business_websites(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Queue a scrape of businesses that have no website_data row yet."""

    _raise_if_job_running(db)

    total_websites = count_scrape_targets(db, only_missing=True)

    job_id = create_scrape_job(
        db=db,
        total_websites=total_websites,
    )

    background_tasks.add_task(scrape_missing_websites, job_id)

    return {
        "success": True,
        "job_id": job_id,
        "message": "Missing website scraping started.",
    }


@scrape_router.post(
    "/retry-failed",
    response_model=JobStartedResponse,
    summary="Retry businesses whose last scrape failed",
    description=(
        "Re-scrapes only businesses whose **most recent** scrape ended "
        "in `Failed`.\n\n"
        "A business that failed once and later succeeded is excluded, so "
        "repeated calls converge rather than re-fetching healthy sites."
    ),
    response_description="The retry was queued.",
    responses={409: conflict_response()},
)
def scrape_failed_business_websites(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Queue a retry of businesses whose most recent scrape failed."""

    _raise_if_job_running(db)

    total_websites = len(get_failed_scrape_targets(db))

    job_id = create_scrape_job(
        db=db,
        total_websites=total_websites,
    )

    background_tasks.add_task(scrape_failed_websites, job_id)

    return {
        "success": True,
        "job_id": job_id,
        "message": "Retry failed scraping started.",
    }


@scrape_router.post(
    "/selected",
    response_model=JobStartedResponse,
    summary="Scrape selected businesses",
    description=(
        "Scrapes only the businesses whose ids are supplied.\n\n"
        "Duplicate ids collapse, unknown ids are skipped and businesses "
        "without a website are ignored - `total_websites` on the job "
        "reflects only the ids that survived that filtering."
    ),
    response_description="The scrape was queued.",
    responses={
        409: conflict_response(),
        422: VALIDATION_ERROR_RESPONSE,
    },
)
def scrape_selected_business_websites(
    payload: ScrapeSelectedRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Queue a scrape of the requested businesses only."""

    _raise_if_job_running(db)

    # Resolving here rather than in the task means total_websites counts only
    # the ids that survived validation — unknown, duplicate and website-less
    # entries are already gone.
    targets = get_selected_scrape_targets(db, payload.business_ids)

    job_id = create_scrape_job(
        db=db,
        total_websites=len(targets),
    )

    background_tasks.add_task(
        scrape_selected_websites,
        job_id,
        [business_id for business_id, _ in targets],
    )

    return {
        "success": True,
        "job_id": job_id,
        "message": "Selected website scraping started.",
    }


@router.get(
    "/{business_id}/website",
    response_model=WebsiteDataDetailResponse,
    summary="Get scraped website data for a business",
    description=(
        "The stored scrape result for one business, with `emails` "
        "decoded from its stored JSON string back into a list.\n\n"
        "Check `status` to tell a successful scrape from a failed "
        "attempt: a business whose scrape failed still has a record, "
        "marked `Failed`, rather than returning 404."
    ),
    response_description="The stored scrape result.",
    responses={
        404: error_response(
            "This business has never been scraped.",
            "No website data found for this business.",
        ),
        422: PATH_VALIDATION_ERROR_RESPONSE,
    },
)
def get_business_website_data(
    business_id: int,
    db: Session = Depends(get_db),
):
    """Return the stored scrape result, with `emails` decoded back to a list."""

    website_data = get_website_data(db, business_id)

    if not website_data:
        raise HTTPException(
            status_code=404,
            detail="No website data found for this business.",
        )

    return {
        "success": True,
        "data": website_data,
    }


@router.get(
    "/{business_id}",
    response_model=BusinessResponse,
    summary="Get one business",
    description="A single business record by id.",
    response_description="The requested business.",
    responses={
        404: BUSINESS_NOT_FOUND,
        422: PATH_VALIDATION_ERROR_RESPONSE,
    },
)
def get_business(
    business_id: int,
    db: Session = Depends(get_db),
):

    business = get_business_by_id(
        db,
        business_id,
    )

    if not business:
        raise HTTPException(
            status_code=404,
            detail="Business not found",
        )

    return business


@router.delete(
    "",
    response_model=DeletedCountResponse,
    summary="Delete several businesses",
    description=(
        "Deletes every listed business in one transaction and reports "
        "how many rows were removed. Their scraped `website_data` goes "
        "with them through the ORM cascade.\n\n"
        "Idempotent: duplicate and unknown ids are ignored rather than "
        "rejected, so re-sending the same request simply reports `0`. "
        "Never returns 404."
    ),
    response_description="How many businesses were deleted.",
    responses={422: VALIDATION_ERROR_RESPONSE},
)
def remove_businesses(
    payload: BulkBusinessRequest,
    db: Session = Depends(get_db),
):
    """
    Delete several businesses at once.

    Unknown and duplicate ids are ignored rather than rejected, so the call is
    idempotent — deleting an already-deleted id simply contributes nothing to
    the count.
    """

    deleted = delete_businesses(db, payload.business_ids)

    return {
        "success": True,
        "deleted": deleted,
    }


@router.delete(
    "/{business_id}",
    response_model=MessageResponse,
    summary="Delete one business",
    description=(
        "Deletes a single business and, through the ORM cascade, its "
        "stored website data."
    ),
    response_description="The business was deleted.",
    responses={
        404: BUSINESS_NOT_FOUND,
        422: PATH_VALIDATION_ERROR_RESPONSE,
    },
)
def remove_business(
    business_id: int,
    db: Session = Depends(get_db),
):

    deleted = delete_business(
        db,
        business_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Business not found",
        )

    return {
        "success": True,
        "message": "Business deleted successfully.",
    }
