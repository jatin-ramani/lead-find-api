from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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
