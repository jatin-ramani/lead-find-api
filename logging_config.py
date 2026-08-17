"""
Structured logging.

One log record carries the same fields whether it is rendered for a human or
as JSON, so switching output format is a configuration change and never a code
change. The fields come from three places:

* the record itself — timestamp, level, logger name, message
* request context, carried in `ContextVar`s so any log call anywhere in the
  stack picks it up without threading a parameter through every function
* the access middleware, which is the only thing that knows a request's status
  and how long it took

`errors.py` still owns the request id; this module reads it. The two are kept
apart because one is about *responses* and the other about *records*, and they
are useful independently.
"""

import json
import logging
import time
from contextvars import ContextVar
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import settings
from errors import get_request_id

logger = logging.getLogger(__name__)

access_logger = logging.getLogger("access")

# Set by RequestLogMiddleware, read by the filter below. Absent outside a
# request, which is why every one has a default.
method_ctx: ContextVar[Optional[str]] = ContextVar("method", default=None)
path_ctx: ContextVar[Optional[str]] = ContextVar("path", default=None)
client_ip_ctx: ContextVar[Optional[str]] = ContextVar("client_ip", default=None)

# The order fields appear in both the human suffix and the JSON object.
CONTEXT_FIELDS: Tuple[str, ...] = (
    "method",
    "path",
    "status",
    "duration_ms",
    "client_ip",
)

MISSING = "-"


# ======================================================
# REDACTION
# ======================================================

MIN_REDACTABLE_LENGTH = 8


def _database_password() -> Optional[str]:
    """
    The password out of the DSN, if it has one.

    Only the password, not the whole URL: a SQLite URL is entirely
    non-sensitive and redacting it would replace the one thing worth knowing —
    which database file this process opened.
    """

    try:
        from sqlalchemy.engine import make_url

        return make_url(settings.database_url).password

    except Exception:
        return None


def _secret_values() -> List[str]:
    """
    The live secret values, longest first.

    Longest first so that if one secret contains another, the longer match is
    replaced before the shorter one can chop it up.
    """

    candidates = [settings.geoapify_api_key, _database_password()]

    # A very short value would match half the log file and turn redaction into
    # corruption. A real key or password is comfortably longer than this; one
    # that is not has a bigger problem than its appearance in a log.
    return sorted(
        {
            value
            for value in candidates
            if value and len(value) >= MIN_REDACTABLE_LENGTH
        },
        key=len,
        reverse=True,
    )


def redact(text: str) -> str:
    """
    Replace any secret that made it into a log line.

    A backstop, not the primary control — the primary control is SecretStr and
    the `safe_database_url` accessor. This catches the case nobody predicted:
    a third-party library logging a connection string, or a traceback that
    happens to render a `params` dict containing the API key.
    """

    if not text:
        return text

    for secret in _secret_values():
        if secret in text:
            text = text.replace(secret, "***REDACTED***")

    return text


# ======================================================
# FILTER
# ======================================================

class RequestContextFilter(logging.Filter):
    """
    Puts the request context on every record.

    A filter rather than formatter logic so the fields exist as real record
    attributes: `%(request_id)s` works in a custom LOG_FORMAT, and the JSON
    formatter reads the same attributes rather than re-deriving them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()

        # The access record sets these itself — it knows the real values, and
        # by the time it is emitted the context may already be unwinding.
        if not hasattr(record, "method"):
            record.method = method_ctx.get()

        if not hasattr(record, "path"):
            record.path = path_ctx.get()

        if not hasattr(record, "client_ip"):
            record.client_ip = client_ip_ctx.get()

        # Only ever known at the end of a request, so absent everywhere else.
        if not hasattr(record, "status"):
            record.status = None

        if not hasattr(record, "duration_ms"):
            record.duration_ms = None

        return True


# ======================================================
# FORMATTERS
# ======================================================

def _context_items(
    record: logging.LogRecord, skip: Iterable[str] = ()
) -> Iterable[Tuple[str, Any]]:
    skipped = set(skip)

    for field in CONTEXT_FIELDS:
        if field in skipped:
            continue

        value = getattr(record, field, None)

        if value is not None:
            yield field, value


class HumanFormatter(logging.Formatter):
    """
    The default. `settings.LOG_FORMAT` renders the line exactly as before, and
    the request context is appended after it — so an existing LOG_FORMAT keeps
    working and gains fields rather than being replaced by them.

        11:10:37.123 INFO  [a3720b10] access | GET /businesses -> 200 | ...
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)

        # The access record already states its method, path, status and
        # duration in prose. Repeating them as key=value would double the
        # width of the busiest line in the file for no added information —
        # the JSON formatter still emits every field.
        context = " ".join(
            f"{name}={value}"
            for name, value in _context_items(
                record, skip=getattr(record, "rendered_fields", ())
            )
        )

        if context:
            base = f"{base} | {context}"

        return redact(base)


class JsonFormatter(logging.Formatter):
    """
    One JSON object per line, for a log shipper.

    Enabled with `LOG_JSON=true`. Nothing else changes: the same records, the
    same fields, the same redaction.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S")
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "requestId": getattr(record, "request_id", MISSING),
            "message": record.getMessage(),
        }

        for name, value in _context_items(record):
            payload[name] = value

        if record.exc_info:
            # Rendered rather than raised: the type and message are the useful
            # part, and the traceback would swamp a single JSON line.
            payload["exception"] = self.formatException(record.exc_info)

        # `default=str` so an unexpected object in a field cannot take down
        # the logging call itself.
        return redact(json.dumps(payload, default=str))


def build_formatter() -> logging.Formatter:
    if settings.LOG_JSON:
        return JsonFormatter()

    return HumanFormatter(settings.LOG_FORMAT)


# ======================================================
# SETUP
# ======================================================

def configure_logging() -> None:
    """
    Install the formatter and the context filter on the root logger.

    Called once from app.py, before anything logs. Replaces any handler
    `logging.basicConfig` created so a re-run — which the test suite does —
    cannot stack duplicate handlers and print every line twice.
    """

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler()
    handler.setFormatter(build_formatter())
    handler.addFilter(RequestContextFilter())

    root.addHandler(handler)

    # uvicorn installs its own handlers and sets propagate=False, so records
    # from these never reach the root handler above and would otherwise be the
    # only unstructured, unredacted lines in the output.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        adopt_logger(logging.getLogger(name))

    # uvicorn logs its own access line for every request, which says strictly
    # less than ours — no duration, no status-derived level. Left on, every
    # deployment logs each request twice. Set LOG_UVICORN_ACCESS=true to keep
    # both, which is only useful when debugging the server itself.
    logging.getLogger("uvicorn.access").disabled = not settings.LOG_UVICORN_ACCESS


def adopt_logger(target: logging.Logger) -> None:
    """Give a third-party logger the same formatter, filter and redaction."""

    for handler in target.handlers:
        handler.setFormatter(build_formatter())
        handler.addFilter(RequestContextFilter())


# ======================================================
# ACCESS MIDDLEWARE
# ======================================================

def _client_ip(scope) -> str:
    """
    The caller's address.

    Behind a load balancer every request appears to come from the balancer, so
    `LOG_TRUST_PROXY_HEADERS` switches to the left-most X-Forwarded-For entry.
    Off by default: that header is client-supplied and trivially forged unless
    something upstream is guaranteed to overwrite it.
    """

    if settings.LOG_TRUST_PROXY_HEADERS:
        for key, value in scope.get("headers", []):
            if key == b"x-forwarded-for":
                first = value.decode("latin-1").split(",")[0].strip()

                if first:
                    return first[:64]

    client = scope.get("client")

    if client:
        return str(client[0])

    return MISSING


class RequestLogMiddleware:
    """
    Times every request and writes one access record when it finishes.

    Pure ASGI, deliberately not BaseHTTPMiddleware — that one buffers
    responses and interferes with StreamingResponse and BackgroundTasks, both
    of which this app uses.

    Sits inside RequestIDMiddleware so the id already exists, and inside
    CORSMiddleware so error responses still get their CORS headers. The cost
    is that a preflight answered by CORS never reaches here; see the README.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", MISSING)
        path = scope.get("path", MISSING)
        client_ip = _client_ip(scope)

        tokens = (
            method_ctx.set(method),
            path_ctx.set(path),
            client_ip_ctx.set(client_ip),
        )

        started = time.perf_counter()
        status: Optional[int] = None
        responded_at: Optional[float] = None

        async def send_wrapper(message):
            nonlocal status, responded_at

            if message["type"] == "http.response.start":
                status = message["status"]
                responded_at = time.perf_counter()

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)

        except Exception:
            # The response is built further out, by RequestIDMiddleware. Record
            # what happened here, where the timing is, then let it through.
            self._emit(
                method, path, client_ip, 500, started, responded_at,
                exception=True,
            )

            # Deliberately NOT resetting the context below: the traceback for
            # this exception is logged further out, and that line — the single
            # most useful one in the file — would otherwise lose its method and
            # path. Nothing else runs in this context; ASGI gives each request
            # its own, so there is nothing left to leak into.
            raise

        self._emit(method, path, client_ip, status, started, responded_at)

        method_ctx.reset(tokens[0])
        path_ctx.reset(tokens[1])
        client_ip_ctx.reset(tokens[2])

    def _emit(
        self,
        method: str,
        path: str,
        client_ip: str,
        status: Optional[int],
        started: float,
        responded_at: Optional[float],
        exception: bool = False,
    ) -> None:
        finished = time.perf_counter()

        # Time until the client had its status and headers — what a caller
        # experiences as "how slow is this endpoint".
        duration_ms = round(((responded_at or finished) - started) * 1000, 2)

        # Time until the request was completely done, which for a streamed CSV
        # or a background scrape is much longer and is not the client's wait.
        total_ms = round((finished - started) * 1000, 2)

        status = status or 500

        if self._excluded(path, status):
            return

        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        else:
            level = logging.INFO

        message = f"{method} {path} -> {status} in {duration_ms}ms"

        if total_ms - duration_ms > max(1.0, duration_ms * 0.05):
            message += f" (completed in {total_ms}ms)"

        access_logger.log(
            level,
            message,
            extra={
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
                "total_ms": total_ms,
                "client_ip": client_ip,
                "unhandled_exception": exception,
                # Already in the message above; see HumanFormatter.
                "rendered_fields": ("method", "path", "status", "duration_ms"),
            },
        )

    @staticmethod
    def _excluded(path: str, status: int) -> bool:
        """
        Quiet paths stay quiet only while they are healthy.

        A health probe every two seconds drowns the log, but the moment one
        fails it is the most interesting line in the file.
        """

        if status >= 400:
            return False

        return path in settings.LOG_ACCESS_EXCLUDE_PATHS


__all__ = [
    "HumanFormatter",
    "JsonFormatter",
    "RequestContextFilter",
    "RequestLogMiddleware",
    "adopt_logger",
    "build_formatter",
    "configure_logging",
    "redact",
]
