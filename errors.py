"""
Centralised error handling.

Two responsibilities, kept together because they are useless apart:

* a request id, generated per request, attached to the response and to every
  log line emitted while handling it
* exception handlers that turn anything raised inside the app into one
  consistent JSON envelope

Nothing here knows about businesses or scraping; it is transport-level only.
"""

import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import settings

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Set by the middleware, read by the handlers and by the logging filter. A
# ContextVar rather than a parameter so log calls anywhere in the stack can
# pick it up without every function having to thread it through.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """The current request's id, or "-" outside a request."""

    return request_id_ctx.get()


# ======================================================
# ERROR CODES
# ======================================================

class ErrorCode:
    """Stable, machine-readable identifiers. Clients switch on these."""

    UNAUTHORIZED = "UNAUTHORIZED"
    HTTP_ERROR = "HTTP_ERROR"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFLICT = "CONFLICT"
    DATABASE_ERROR = "DATABASE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"


_STATUS_TO_CODE = {
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    502: ErrorCode.UPSTREAM_ERROR,
}


# ======================================================
# APPLICATION EXCEPTIONS
# ======================================================

class AppError(Exception):
    """
    Raised by application code that wants to control the whole envelope.

    Exists so a handler-level concern such as "a job is already running" can
    carry structured context (`details`) without inventing a second response
    shape.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error: str = ErrorCode.HTTP_ERROR,
        details: Optional[Any] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error = error
        self.details = details


# ======================================================
# ENVELOPE
# ======================================================

def build_error_response(
    status_code: int,
    message: str,
    error: str,
    details: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    """The single place an error body is constructed."""

    payload: Dict[str, Any] = {
        "success": False,
        "message": message,
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "requestId": get_request_id(),
    }

    # Always present so clients can rely on the key existing.
    payload["details"] = details

    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers=headers,
    )


# ======================================================
# REQUEST ID
# ======================================================

class RequestIDMiddleware:
    """
    Pure ASGI middleware, deliberately not BaseHTTPMiddleware.

    BaseHTTPMiddleware buffers responses and has a long history of interfering
    with StreamingResponse and BackgroundTasks — this app uses both (CSV export
    and the bulk scrapers), so it stays out of the way at the ASGI level.

    An inbound X-Request-ID is honoured so a trace survives across services;
    otherwise one is generated.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None

        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                incoming = value.decode("latin-1").strip()
                break

        # Bounded: an unbounded client-supplied value would end up in logs.
        request_id = (incoming or "")[:64] or uuid.uuid4().hex

        token = request_id_ctx.set(request_id)
        started = False

        async def send_with_header(message):
            nonlocal started

            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id

            await send(message)

        try:
            await self.app(scope, receive, send_with_header)

        except Exception as exc:
            # Starlette's ServerErrorMiddleware wraps this one, so letting the
            # exception through would mean the 500 is built after the context
            # var is reset (requestId "-") and sent through the outer `send`,
            # skipping the header wrapper above. Handling it here keeps both.
            if started:
                # Headers are already on the wire; nothing can be substituted.
                raise

            from starlette.requests import Request

            response = await unhandled_exception_handler(Request(scope), exc)
            await response(scope, receive, send_with_header)

        finally:
            request_id_ctx.reset(token)


class RequestIDLogFilter(logging.Filter):
    """
    Makes `%(request_id)s` available to every formatter.

    Superseded by `logging_config.RequestContextFilter`, which adds the method,
    path, status, duration and client IP on top of this. Kept because it is the
    minimum a log line needs and depends on nothing but this module — useful
    for a script or a management command that wants request ids without the
    rest of the logging setup.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


# ======================================================
# HANDLERS
# ======================================================

async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """
    Deliberate 4xx/5xx raised by endpoints.

    `exc.detail` is already client-safe — endpoints choose that wording — so it
    becomes `message`. Headers are preserved because some statuses require
    them (401 carries WWW-Authenticate).
    """

    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    details = None if isinstance(exc.detail, str) else exc.detail

    logger.info(
        "HTTP %s on %s %s: %s",
        exc.status_code,
        request.method,
        request.url.path,
        message,
    )

    return build_error_response(
        status_code=exc.status_code,
        message=message,
        error=_STATUS_TO_CODE.get(exc.status_code, ErrorCode.HTTP_ERROR),
        details=details,
        headers=getattr(exc, "headers", None),
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.info(
        "%s on %s %s: %s",
        exc.error,
        request.method,
        request.url.path,
        exc.message,
    )

    return build_error_response(
        status_code=exc.status_code,
        message=exc.message,
        error=exc.error,
        details=exc.details,
    )


async def validation_exception_handler(
    request: Request, exc: Union[RequestValidationError, ValidationError]
) -> JSONResponse:
    """
    Body / query / path validation failures.

    The per-field list is kept — it is the useful part — but normalised and
    stripped of the raw `input`, which can echo back whatever the client sent.
    """

    details = [
        {
            "field": ".".join(str(part) for part in err.get("loc", [])),
            "message": err.get("msg", "invalid value"),
            "type": err.get("type", "value_error"),
        }
        for err in exc.errors()
    ]

    logger.info(
        "Validation failed on %s %s: %s",
        request.method,
        request.url.path,
        details,
    )

    return build_error_response(
        status_code=422,
        message="Request validation failed.",
        error=ErrorCode.VALIDATION_ERROR,
        details=details,
    )


async def sqlalchemy_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """
    Database failures.

    The real exception carries SQL, table names and sometimes parameter values,
    so it is logged and never returned. The client gets the request id and can
    quote it in a support ticket.
    """

    logger.exception(
        "Database error on %s %s", request.method, request.url.path
    )

    return build_error_response(
        status_code=500,
        message="A database error occurred. Please try again.",
        error=ErrorCode.DATABASE_ERROR,
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """
    Anything that reached the top of the stack.

    Logged with a traceback, reported as a bare 500. In debug the exception
    type and message are echoed back to make local work bearable; in any other
    environment nothing internal crosses the boundary.
    """

    logger.exception(
        "Unhandled %s on %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
    )

    details = None

    if settings.DEBUG:
        details = {"exception": type(exc).__name__, "detail": str(exc)}

    return build_error_response(
        status_code=500,
        message="An unexpected error occurred.",
        error=ErrorCode.INTERNAL_ERROR,
        details=details,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire every handler onto the application. Called once from app.py."""

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)

    # Starlette routes this through ServerErrorMiddleware, so it catches
    # anything the handlers above did not.
    app.add_exception_handler(Exception, unhandled_exception_handler)


def install_request_id_logging() -> None:
    """
    Attach the request-id filter to the root handlers.

    The application calls `logging_config.configure_logging()` instead, which
    does this and more. This remains for anything that wants request ids in
    its logs without the full setup.
    """

    log_filter = RequestIDLogFilter()

    for handler in logging.getLogger().handlers:
        handler.addFilter(log_filter)
