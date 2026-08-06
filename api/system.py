import platform
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database.db import engine, get_db
from database.crud import check_database_connection

router = APIRouter(
    tags=["System"],
)


def _utc_now() -> str:
    """Current time as ISO-8601 with an explicit UTC offset."""

    return datetime.now(timezone.utc).isoformat()


@router.get(
    "/health",
    summary="Liveness and database probe",
    description=(
        "Confirms the process is up and the database answers a `SELECT 1`.\n\n"
        "Returns **503** when the database is unreachable, so a load balancer "
        "or orchestrator can act on it."
    ),
    response_description="The service is healthy and the database responded.",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Healthy.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "healthy",
                        "database": "connected",
                        "timestamp": "2026-08-07T09:15:00.123456+00:00",
                    }
                }
            },
        },
        503: {
            "description": "The database did not respond.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "unhealthy",
                        "database": "disconnected",
                        "timestamp": "2026-08-07T09:15:00.123456+00:00",
                    }
                }
            },
        },
    },
)
def health(
    db: Session = Depends(get_db),
):
    connected = check_database_connection(db)

    payload: Dict[str, Any] = {
        "status": "healthy" if connected else "unhealthy",
        "database": "connected" if connected else "disconnected",
        "timestamp": _utc_now(),
    }

    if not connected:
        # 503 so a load balancer or orchestrator actually reacts — a health
        # check that always answers 200 tells them nothing.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload,
        )

    return payload


@router.get(
    "/version",
    summary="API name and version",
    description=(
        "Read straight off the FastAPI application, so `app.py` stays the "
        "single source of truth for both values."
    ),
    response_description="The running API's name and semantic version.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {"name": "Lead Finder API", "version": "1.0.0"}
                }
            }
        }
    },
)
def version(request: Request):
    return {
        "name": request.app.title,
        "version": request.app.version,
    }


@router.get(
    "/system",
    summary="Runtime environment",
    description=(
        "Interpreter, host and database facts about the running service.\n\n"
        "The database is reported by **dialect** (`sqlite`, `postgresql`) "
        "rather than by its URL: a connection string carries credentials on "
        "any non-SQLite deployment. No environment variable or secret is "
        "exposed by this endpoint."
    ),
    response_description="Runtime details of the host and interpreter.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "pythonVersion": "3.14.3",
                        "platform": "Windows 11",
                        "database": "sqlite",
                        "apiVersion": "1.0.0",
                        "serverTime": "2026-08-07T09:15:00.123456+00:00",
                    }
                }
            }
        }
    },
)
def system_info(request: Request):
    return {
        "pythonVersion": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}".strip(),
        "database": engine.dialect.name,
        "apiVersion": request.app.version,
        "serverTime": _utc_now(),
    }
