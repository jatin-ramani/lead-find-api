from typing import Any, Dict, Optional

from pydantic import BaseModel


# ======================================================
# SHARED RESPONSE MODELS
# ======================================================

class MessageResponse(BaseModel):
    """Plain acknowledgement returned by actions that carry no payload."""

    success: bool
    message: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": True,
                "message": "Website scraped successfully.",
            }
        }
    }


class DeletedCountResponse(BaseModel):
    """Result of a bulk delete."""

    success: bool
    deleted: int

    model_config = {
        "json_schema_extra": {
            "example": {"success": True, "deleted": 12}
        }
    }


class JobStartedResponse(BaseModel):
    """Acknowledgement that a background job was queued."""

    success: bool
    job_id: int
    message: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": True,
                "job_id": 12,
                "message": "Bulk scraping started.",
            }
        }
    }


class JobConflictResponse(BaseModel):
    """Returned with 409 when another scrape job is still running."""

    success: bool
    message: str
    job_id: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": False,
                "message": "A scrape job is already running.",
                "job_id": 7,
            }
        }
    }


class ErrorResponse(BaseModel):
    """
    The single error envelope for the whole API.

    Every failure — 4xx and 5xx alike — comes back in this shape. `error` is a
    stable machine-readable code; `message` is the human-readable text;
    `requestId` matches the `X-Request-ID` response header and the server logs,
    so a user can quote it and the exact request can be found.
    """

    success: bool = False
    message: str
    error: str
    timestamp: str
    requestId: str
    details: Optional[Any] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": False,
                "message": "Business not found",
                "error": "NOT_FOUND",
                "timestamp": "2026-08-07T09:15:00.123456+00:00",
                "requestId": "9f2c1b7e4a5d4f0c8e3b6a1d2c4e5f70",
                "details": None,
            }
        }
    }


# ======================================================
# OPENAPI RESPONSE HELPERS
# ======================================================

def _envelope(message: str, error: str, details: Any = None) -> Dict[str, Any]:
    """A filled-in example of the shared error envelope."""

    return {
        "success": False,
        "message": message,
        "error": error,
        "timestamp": "2026-08-07T09:15:00.123456+00:00",
        "requestId": "9f2c1b7e4a5d4f0c8e3b6a1d2c4e5f70",
        "details": details,
    }


def error_response(
    description: str,
    example: str,
    error: str = "HTTP_ERROR",
) -> Dict[str, Any]:
    """
    Build one `responses={}` entry for a failure.

    Keeps the per-endpoint documentation to a single readable line instead of
    repeating the same nested content/example dict everywhere.
    """

    return {
        "model": ErrorResponse,
        "description": description,
        "content": {
            "application/json": {"example": _envelope(example, error)}
        },
    }


def conflict_response() -> Dict[str, Any]:
    """The 409 every bulk scrape endpoint shares."""

    return {
        "model": ErrorResponse,
        "description": "Another scrape job is already running.",
        "content": {
            "application/json": {
                "example": _envelope(
                    "A scrape job is already running.",
                    "CONFLICT",
                    details={"job_id": 7},
                )
            }
        },
    }


def csv_response(description: str) -> Dict[str, Any]:
    """A streamed text/csv download rather than a JSON body."""

    return {
        "description": description,
        "content": {
            "text/csv": {
                "schema": {"type": "string", "format": "binary"},
                "example": (
                    "ID,Name,Phone,Email,Website,City,Category,Address,Status\r\n"
                    '1,"Asopalav","+91 79 2676 5592","","https://asopalav.com",'
                    '"Ahmedabad","commercial","132 Ft Ring Road","Has Website"\r\n'
                ),
            }
        },
    }


VALIDATION_ERROR_RESPONSE: Dict[str, Any] = {
    "model": ErrorResponse,
    "description": "The request body or query string failed validation.",
    "content": {
        "application/json": {
            "example": _envelope(
                "Request validation failed.",
                "VALIDATION_ERROR",
                details=[
                    {
                        "field": "body.business_ids.0",
                        "message": (
                            "Input should be a valid integer, unable to parse "
                            "string as an integer"
                        ),
                        "type": "int_parsing",
                    }
                ],
            )
        }
    },
}

PATH_VALIDATION_ERROR_RESPONSE: Dict[str, Any] = {
    "model": ErrorResponse,
    "description": "The id in the path is not an integer.",
    "content": {
        "application/json": {
            "example": _envelope(
                "Request validation failed.",
                "VALIDATION_ERROR",
                details=[
                    {
                        "field": "path.business_id",
                        "message": (
                            "Input should be a valid integer, unable to parse "
                            "string as an integer"
                        ),
                        "type": "int_parsing",
                    }
                ],
            )
        }
    },
}


SERVER_ERROR_RESPONSE: Dict[str, Any] = {
    "model": ErrorResponse,
    "description": (
        "An unexpected error. Internal details are never returned — quote "
        "`requestId` when reporting it."
    ),
    "content": {
        "application/json": {
            "example": _envelope(
                "An unexpected error occurred.", "INTERNAL_ERROR"
            )
        }
    },
}
