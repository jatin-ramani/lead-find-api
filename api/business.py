import csv
import io
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT_BY,
    DEFAULT_SORT_ORDER,
    MAX_PAGE_SIZE,
    SORTABLE_COLUMNS,
    SORT_ORDERS,
    get_businesses,
    get_business_by_id,
    delete_business,
)

from schemas.business import (
    BusinessResponse,
    BusinessListResponse,
)

router = APIRouter(
    prefix="/businesses",
    tags=["Businesses"],
)

CSV_FILENAME = "businesses.csv"

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


def _iter_businesses_csv(
    db: Session,
    search: Optional[str],
    city: Optional[str],
    category: Optional[str],
    status: Optional[str],
    sort_by: Optional[str],
    sort_order: Optional[str],
) -> Iterator[str]:
    """
    Yield the filtered businesses as CSV, one page of rows at a time.

    Pagination is walked internally rather than bypassed, so the export reuses
    get_businesses() untouched and never holds the whole table in memory.
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

        for business in result["data"]:
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

        page += 1


@router.get("/export/csv")
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

    return StreamingResponse(
        _iter_businesses_csv(
            db=db,
            search=search,
            city=city,
            category=category,
            status=status,
            sort_by=sortBy,
            sort_order=sortOrder,
        ),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{CSV_FILENAME}"',
        },
    )


@router.get(
    "/{business_id}",
    response_model=BusinessResponse,
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


@router.delete("/{business_id}")
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
