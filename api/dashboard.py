from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import get_dashboard_stats

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
)


@router.get("/stats")
def dashboard_stats(
    db: Session = Depends(get_db),
):
    return get_dashboard_stats(db)