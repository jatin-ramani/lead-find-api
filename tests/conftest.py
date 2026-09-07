"""
Shared pytest fixtures.

The database must be redirected BEFORE any application module is imported:
`config.py` builds its singleton `settings` at import time and `database.db`
builds the engine from it, so setting the environment later would have no
effect and the suite would run against the developer's real database.
"""

import os
import tempfile
from pathlib import Path

import pytest

# --- must happen before the first application import -----------------------
_TMP_DB = Path(tempfile.gettempdir()) / "leadfinder_pytest.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.as_posix()}"
os.environ["ENVIRONMENT"] = "testing"
os.environ["GEOAPIFY_API_KEY"] = "test-key-not-real"
os.environ["LOG_LEVEL"] = "CRITICAL"
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from database.db import Base, SessionLocal, engine  # noqa: E402
from database.models import (  # noqa: E402
    AdminSession,
    Business,
    BusinessActivity,
    BusinessNote,
    BusinessTag,
    ScanJob,
    ScrapeJob,
    Tag,
    WebsiteData,
)


@pytest.fixture(scope="session", autouse=True)
def _guard_against_real_database():
    """Refuse to run if the suite is somehow pointed at the real database."""

    url = str(engine.url)

    assert "leadfinder_pytest" in url, (
        f"tests would run against {url!r} — expected the temporary database. "
        "Check that conftest.py is imported before the application."
    )

    yield

    engine.dispose()

    if _TMP_DB.exists():
        try:
            _TMP_DB.unlink(missing_ok=True)
        except Exception:
            pass


@pytest.fixture(scope="session", autouse=True)
def _schema(_guard_against_real_database):
    """
    Build the schema once for the suite.

    `create_all` is used here deliberately: production owns its schema through
    Alembic, but a test database wants the models' current definition without
    replaying migration history.
    """

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    yield

    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_tables(_schema):
    """Empty every table between tests so they cannot leak state into one another."""

    yield

    session = SessionLocal()

    try:
        # Children first: website_data, business_notes, business_activities, business_tags reference businesses / tags.
        for model in (AdminSession, BusinessActivity, BusinessNote, BusinessTag, Tag, WebsiteData, ScrapeJob, ScanJob, Business):
            session.query(model).delete()

        session.commit()
    finally:
        session.close()


@pytest.fixture
def db():
    """A session for tests that exercise the CRUD layer directly."""

    session = SessionLocal()

    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def unauth_client():
    """Unauthenticated HTTP client for testing 401 protection."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client():
    """Pre-authenticated HTTP client for general route testing."""
    from config import settings

    headers = {"Authorization": f"Bearer {settings.admin_secret}"}
    with TestClient(app, headers=headers) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------

def make_business(db, **overrides) -> Business:
    """Insert one business, with sensible defaults that each field can override."""

    index = overrides.pop("_index", db.query(Business).count() + 1)

    defaults = {
        "name": f"Business {index}",
        "phone": f"+91 90000 {index:05d}",
        "email": f"biz{index}@example.test",
        "website": f"https://biz{index}.test",
        "city": "Ahmedabad",
        "category": "commercial",
        "address": f"{index} Ring Road",
        "status": "Has Website",
        "place_id": f"place-{index}",
    }
    defaults.update(overrides)

    business = Business(**defaults)
    db.add(business)
    db.commit()
    db.refresh(business)

    return business


@pytest.fixture
def business_factory(db):
    """Callable that creates businesses inside the test's session."""

    def _factory(**overrides) -> Business:
        return make_business(db, **overrides)

    return _factory


@pytest.fixture
def sample_businesses(db):
    """
    Ten businesses with a deliberate spread:

    - ids 1..10, cities cycling Ahmedabad / Surat / Baroda
    - every third has no website (and status "No Website")
    - every fourth has no e-mail
    - one blank-string website and one whitespace-only website, to exercise
      the "has a website" rule used by the scrapers and the dashboard
    """

    created = []

    for i in range(1, 11):
        has_website = i % 3 != 0

        website = f"https://biz{i}.test" if has_website else None

        if i == 5:
            website = ""
        if i == 8:
            website = "   "

        created.append(
            make_business(
                db,
                _index=i,
                name=f"Business {i:02d}",
                city=["Ahmedabad", "Surat", "Baroda"][i % 3],
                category=["commercial", "retail"][i % 2],
                website=website,
                email=None if i % 4 == 0 else f"biz{i}@example.test",
                status="Has Website" if website else "No Website",
                place_id=f"place-{i}",
            )
        )

    return created


@pytest.fixture
def scraped_html() -> str:
    """A page exercising every extraction path the scraper supports."""

    return """<!doctype html><html><head>
    <title>  Asopalav  &mdash;  Ethnic  Wear  </title>
    <meta name="Description" content="Designer   sarees in Ahmedabad." >
    </head><body>
    <script>var junk="fake@script.com";</script>
    <p>Contact: Sales@Asopalav.test or support@asopalav.test</p>
    <a href="mailto:Sales@asopalav.test?subject=Hi">mail</a>
    <p>Bad: logo@2x.png someone@example.com</p>
    <a href="https://www.facebook.com/asopalav">fb</a>
    <a href="https://facebook.com/sharer/sharer.php?u=x">share</a>
    <a href="https://instagram.com/asopalav/">ig</a>
    <a href="https://in.linkedin.com/company/asopalav">li</a>
    <a href="https://twitter.com/intent/tweet?text=x">tweet</a>
    <a href="https://x.com/asopalav">x</a>
    <a href="https://youtu.be/abc123">yt</a>
    <a href="https://wa.me/919876543210">wa</a>
    </body></html>"""
