from typing import Any, Dict

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
    """FastAPI's HTTPException body."""

    detail: str

    model_config = {
        "json_schema_extra": {"example": {"detail": "Business not found"}}
    }


# ======================================================
# OPENAPI RESPONSE HELPERS
# ======================================================

def error_response(description: str, example: str) -> Dict[str, Any]:
    """
    Build one `responses={}` entry for an HTTPException-style failure.

    Keeps the per-endpoint documentation to a single readable line instead of
    repeating the same nested content/example dict everywhere.
    """

    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": {"detail": example}}},
    }


def conflict_response() -> Dict[str, Any]:
    """The 409 every bulk scrape endpoint shares."""

    return {
        "model": JobConflictResponse,
        "description": "Another scrape job is already running.",
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "message": "A scrape job is already running.",
                    "job_id": 7,
                }
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
    "description": "The request body or query string failed validation.",
    "content": {
        "application/json": {
            "example": {
                "detail": [
                    {
                        "type": "int_parsing",
                        "loc": ["body", "business_ids", 0],
                        "msg": (
                            "Input should be a valid integer, unable to parse "
                            "string as an integer"
                        ),
                        "input": "abc",
                    }
                ]
            }
        }
    },
}

PATH_VALIDATION_ERROR_RESPONSE: Dict[str, Any] = {
    "description": "The id in the path is not an integer.",
    "content": {
        "application/json": {
            "example": {
                "detail": [
                    {
                        "type": "int_parsing",
                        "loc": ["path", "business_id"],
                        "msg": (
                            "Input should be a valid integer, unable to parse "
                            "string as an integer"
                        ),
                        "input": "abc",
                    }
                ]
            }
        }
    },
}
