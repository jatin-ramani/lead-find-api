import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Imported first: instantiating Settings validates the whole configuration, so
# a misconfigured process dies here rather than on its first request.
from config import settings

from api.business import router as business_router
from api.business import scrape_router
from api.scanner import router as scanner_router
from api.scan_jobs import router as scan_jobs_router
from api.scrape_jobs import router as scrape_jobs_router
from api.dashboard import router as dashboard_router
from api.system import router as system_router

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format=settings.LOG_FORMAT,
)

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
* Failures use FastAPI's standard `{"detail": "..."}` body.
* Endpoints that take a list of ids ignore duplicates and unknown ids rather
  than rejecting the whole request.
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

app = FastAPI(
    title=settings.APP_NAME,
    description=DESCRIPTION,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def log_startup_configuration() -> None:
    """
    Record the effective configuration once, so a misbehaving deployment can
    be diagnosed from its logs. Secrets are reported as present/absent only.
    """

    logger.info(
        "%s %s starting | environment=%s debug=%s database=%s "
        "geoapify_key=%s cors_origins=%s",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT.value,
        settings.DEBUG,
        "sqlite" if settings.is_sqlite else "postgresql",
        "set" if settings.GEOAPIFY_API_KEY else "MISSING",
        ",".join(settings.CORS_ORIGINS),
    )

    if not settings.GEOAPIFY_API_KEY:
        logger.warning(
            "GEOAPIFY_API_KEY is not set — scanning will fail. "
            "This is refused outright when ENVIRONMENT=production."
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


app.include_router(business_router)
app.include_router(scrape_router)
app.include_router(scanner_router)
app.include_router(scan_jobs_router)
app.include_router(scrape_jobs_router)
app.include_router(dashboard_router)
app.include_router(system_router)
