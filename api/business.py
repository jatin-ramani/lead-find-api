from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import (
    get_businesses,
    get_business_by_id,
    delete_business,
)

from schemas.business import BusinessResponse

router = APIRouter(
    prefix="/businesses",
    tags=["Businesses"],
)


@router.get(
    "",
    response_model=List[BusinessResponse]
)
def list_businesses(
    db: Session = Depends(get_db),
):
    return get_businesses(db)


@router.get(
    "/{business_id}",
    response_model=BusinessResponse
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