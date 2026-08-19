import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Imported first: instantiating Settings validates the whole configuration, so
# a misconfigured process dies here rather than on its first request.
from config import settings

from errors import (
    RequestIDMiddleware,
    register_exception_handlers,
)

from logging_config import RequestLogMiddleware, configure_logging

from api.business import router as business_router
from api.business import scrape_router
from api.scanner import router as scanner_router
from api.scan_jobs import router as scan_jobs_router
from api.scrape_jobs import router as scrape_jobs_router
from api.dashboard import router as dashboard_router
from api.system import router as system_router
from api.auth import router as auth_router
from database.auth import verify_admin
from database.migrations import run_startup_migrations

# Installs the formatter (human or JSON, per LOG_JSON), the filter that
# supplies %(request_id)s and the request context, and the secret redaction
# that wraps both. Must run before anything logs.
configure_logging()

logger = logging.getLogger(__name__)

DESCRIPTION = """
Finds local businesses and works out which of them have no website — the ones
worth pitching.

### How it fits together

1. **Business Scanning** — `POST /scan` queries the Geoapify Places API for a
   city and category and stores whatever it finds, de-duplicated by `place_id`.
2. **Website Scraping** — the scrapers visit each stored website and extract
   its title, meta description, e-mail addresses and social links.
3. **Businesses** — browse, filter, export and delete what has been collected.
4. **Dashboard** — one call for every headline figure.

### Jobs

Both long-running operations record their progress:

* scanning writes a **scan job**, polled via `GET /scan/jobs/latest`
* scraping writes a **scrape job**, polled via `GET /scrape/jobs/{job_id}`

Bulk scrapes run in the background and return a `job_id` immediately. Only one
scrape job may run at a time; starting another while one is in flight returns
**409** together with the id of the job already running.

### Conventions

* Action endpoints answer `{"success": true, ...}`.
* Endpoints that take a list of ids ignore duplicates and unknown ids rather
  than rejecting the whole request.

### Errors

Every failure — validation, not-found, conflict, database, unexpected — comes
back in one envelope:

```json
{
  "success": false,
  "message": "Business not found",
  "error": "NOT_FOUND",
  "timestamp": "2026-08-07T09:15:00.123456+00:00",
  "requestId": "9f2c1b7e4a5d4f0c8e3b6a1d2c4e5f70",
  "details": null
}
```

`error` is a stable code to switch on: `VALIDATION_ERROR`, `NOT_FOUND`,
`CONFLICT`, `DATABASE_ERROR`, `INTERNAL_ERROR`, `HTTP_ERROR`. `details` carries
per-field problems for validation failures and is `null` otherwise.

Every response — success or failure — also carries an `X-Request-ID` header,
which matches `requestId` and the server logs. Quote it when reporting a
problem. Internal details are never returned for a 500.
"""

TAGS_METADATA = [
    {
        "name": "Businesses",
        "description": (
            "Browse, filter, export and delete the businesses discovered so "
            "far, and read the website data scraped from them."
        ),
    },
    {
        "name": "Business Scanning",
        "description": "Discover new businesses from the Geoapify Places API.",
    },
    {
        "name": "Scan Jobs",
        "description": "Progress and history of business scans.",
    },
    {
        "name": "Website Scraping",
        "description": (
            "Bulk scrape business websites — all of them, only the ones never "
            "scraped, only the ones that failed, or a chosen subset."
        ),
    },
    {
        "name": "Scrape Jobs",
        "description": "Progress and history of website scrapes.",
    },
    {
        "name": "Dashboard",
        "description": "Aggregated statistics for the admin dashboard.",
    },
    {
        "name": "System",
        "description": "Health, version and runtime information.",
    },
]


def log_startup_configuration() -> None:
    """
    Record the effective configuration once, so a misbehaving deployment can
    be diagnosed from its logs.

    Secrets never appear: the API key is reported as set/MISSING, and the
    database URL goes through `safe_database_url`, which masks the password
    while keeping the host and database name — the parts you actually need
    when a deployment is talking to the wrong database.
    """

    logger.info(
        "%s %s starting | environment=%s debug=%s database=%s "
        "geoapify_key=%s cors_origins=%s",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT.value,
        settings.DEBUG,
        settings.safe_database_url,
        "set" if settings.has_geoapify_key else "MISSING",
        ",".join(settings.CORS_ORIGINS),
    )

    for warning in settings.configuration_warnings():
        logger.warning("Configuration: %s", warning)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Startup and shutdown, in one place.

    Everything before the `yield` runs once the server is ready to accept
    connections; everything after runs as it drains. This replaces the
    deprecated `@app.on_event` decorators — Starlette only ever runs one
    lifespan, so both halves live here rather than in two separate callbacks.

    Note what is deliberately *not* here: configuration is validated when
    `config` is imported and logging is configured immediately after, both
    long before this runs. A process with bad configuration must die before it
    binds a port, not after — so that work stays at import time.
    """

    run_startup_migrations()
    log_startup_configuration()

    yield

    logger.info("%s shutting down", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    description=DESCRIPTION,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
    # Interactive docs are withheld in production unless DEBUG is on: they
    # advertise every route and payload shape to anyone who finds the host.
    docs_url=settings.docs_url,
    redoc_url=settings.redoc_url,
    contact={
        "name": "Lead Finder",
        "email": "jatinrmn@gmail.com",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
    # Fixes the order of the Swagger UI groups; without this they appear in
    # whichever order the routers happen to be included.
    openapi_tags=TAGS_METADATA,
)

# Middleware order, outermost first: CORS, RequestID, RequestLog. Starlette
# builds the stack so the LAST one added ends up outermost, hence the reversed
# order of these three calls.
#
#   CORS       must stay outermost, so the error responses that RequestID
#              builds still get their Access-Control-Allow-Origin header —
#              without it a browser sees an opaque failure instead of the 500.
#   RequestID  must wrap RequestLog, so the id exists before the access
#              record is written.
#   RequestLog innermost of the three, timing the actual application work.
app.add_middleware(RequestLogMiddleware)
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(
    "/",
    tags=["System"],
    summary="API root",
    description="A cheap check that the service is serving requests at all.",
    response_description="A greeting confirming the API is up.",
)
def home():
    return {
        "message": "Lead Finder API is running 🚀"
    }


# Every failure from here on answers with the shared error envelope.
register_exception_handlers(app)

app.include_router(auth_router)
app.include_router(system_router)

# Protected routers requiring administrative authentication
app.include_router(business_router, dependencies=[Depends(verify_admin)])
app.include_router(scrape_router, dependencies=[Depends(verify_admin)])
app.include_router(scanner_router, dependencies=[Depends(verify_admin)])
app.include_router(scan_jobs_router, dependencies=[Depends(verify_admin)])
app.include_router(scrape_jobs_router, dependencies=[Depends(verify_admin)])
app.include_router(dashboard_router, dependencies=[Depends(verify_admin)])
