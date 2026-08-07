from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.db import get_db
from database.crud import get_dashboard_stats

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
)


@router.get(
    "/stats",
    summary="Aggregated dashboard statistics",
    description=(
        "Every headline figure in one call, grouped by the table it "
        "describes.\n\n"
        "- **business** — totals, and how many carry a website or an e-mail\n"
        "- **websiteData** — scrape outcomes scored on the most recent record "
        "per business; `pending` is the set `POST /scrape/missing` would pick "
        "up\n"
        "- **scrapeJobs** / **scanJobs** — job counts by state\n"
        "- **latestScanJob** / **latestScrapeJob** — `null` when none exist "
        "yet\n\n"
        "A website or e-mail counts as present only when it is a non-blank "
        "string, matching the rule the scrapers use — so "
        "`completed + failed + pending` always equals `withWebsite`."
    ),
    response_description="Grouped counts plus the most recent job of each kind.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "business": {
                            "totalBusinesses": 250,
                            "withWebsite": 96,
                            "withoutWebsite": 154,
                            "withEmail": 41,
                            "withoutEmail": 209,
                        },
                        "websiteData": {
                            "completed": 74,
                            "failed": 9,
                            "pending": 13,
                            "totalScraped": 83,
                        },
                        "scrapeJobs": {
                            "total": 12,
                            "running": 0,
                            "completed": 10,
                            "failed": 2,
                        },
                        "scanJobs": {
                            "total": 5,
                            "running": 0,
                            "completed": 5,
                        },
                        "latestScanJob": {
                            "id": 5,
                            "city": "Ahmedabad",
                            "category": "commercial",
                            "status": "Completed",
                            "progress": 100,
                            "total_businesses": 20,
                            "new_businesses": 6,
                        },
                        "latestScrapeJob": {
                            "id": 12,
                            "status": "Completed",
                            "progress": 100,
                            "total_websites": 96,
                            "completed": 96,
                            "success": 87,
                            "failed": 9,
                            "current_business_id": None,
                            "started_at": "2026-08-07T09:02:11.004512+00:00",
                            "completed_at": "2026-08-07T09:07:48.771903+00:00",
                        },
                    }
                }
            }
        }
    },
)
def dashboard_stats(
    db: Session = Depends(get_db),
):
    return get_dashboard_stats(db)
