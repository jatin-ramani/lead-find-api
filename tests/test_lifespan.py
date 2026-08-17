"""
The lifespan replaced the deprecated `@app.on_event` handlers.

Two things are worth pinning: that the startup and shutdown halves actually
run — a lifespan silently does nothing if it is never passed to
`FastAPI(...)` — and that nobody reintroduces `on_event`, which would restore
the deprecation warning and, in a future FastAPI, stop running at all.
"""

import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import app, lifespan, log_startup_configuration
from config import settings

BACKEND = Path(__file__).resolve().parent.parent

# The decorator/call form only, so the prose in app.py's own docstring that
# mentions `@app.on_event` is not a false positive.
LEGACY_EVENT_API = re.compile(r"@\w+\.on_event\(|\.add_event_handler\(")


def _source_files():
    for path in BACKEND.rglob("*.py"):
        parts = path.parts

        if "venv" in parts or "__pycache__" in parts or "alembic" in parts:
            continue

        yield path


class TestNoLegacyEventApi:
    def test_no_startup_or_shutdown_handlers_registered(self):
        """
        Starlette keeps `on_event` callbacks in these two lists. Both empty
        means every one of them has moved into the lifespan.
        """

        assert app.router.on_startup == []
        assert app.router.on_shutdown == []

    def test_source_is_free_of_the_deprecated_api(self):
        offenders = [
            f"{path.relative_to(BACKEND)}:{number}"
            for path in _source_files()
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if LEGACY_EVENT_API.search(line)
        ]

        assert offenders == [], (
            f"deprecated on_event API reintroduced at {offenders} — "
            "move the handler into the lifespan in app.py instead"
        )

    def test_importing_the_app_emits_no_deprecation_warning(self):
        """
        `on_event` warns while app.py executes, so the check has to happen on
        a fresh import. A subprocess with `-W error::DeprecationWarning` turns
        any such warning into a non-zero exit, and leaves this session's
        already-imported modules untouched.
        """

        result = subprocess.run(
            [sys.executable, "-W", "error::DeprecationWarning", "-c", "import app"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert "DeprecationWarning" not in result.stderr


class TestLifespanRuns:
    def test_both_halves_run_around_the_requests(self, caplog):
        """
        Also the check that `lifespan=` reached the constructor: nothing
        structural proves it, because Starlette reports a `merged_lifespan`
        wrapper whether or not one was supplied. Only the log lines do.
        """

        caplog.set_level(logging.INFO, logger="app")

        with TestClient(app) as client:
            during = [record.getMessage() for record in caplog.records]

            assert client.get("/health").json()["status"] == "healthy"

        after = [record.getMessage() for record in caplog.records]

        assert any("starting | environment=" in m for m in during), "startup half did not run"
        assert any("shutting down" in m for m in after), "shutdown half did not run"
        # Ordering: the shutdown line cannot have appeared before the exit.
        assert not any("shutting down" in m for m in during)

    def test_the_app_serves_requests_between_the_halves(self):
        """A lifespan that raises leaves the app unable to answer anything."""

        with TestClient(app) as client:
            assert client.get("/").status_code == 200
            assert client.get("/version").status_code == 200
            assert client.get("/businesses").status_code == 200

    def test_entering_and_exiting_directly_is_symmetric(self):
        """Neither half may depend on TestClient's plumbing to complete."""

        async def cycle():
            async with lifespan(app):
                return True

        assert asyncio.run(cycle()) is True


class TestStartupLogging:
    def test_reports_configuration_without_leaking_the_key(self, caplog):
        caplog.set_level(logging.INFO, logger="app")

        log_startup_configuration()

        logged = "\n".join(record.getMessage() for record in caplog.records)

        assert settings.APP_NAME in logged
        assert "environment=testing" in logged
        assert "geoapify_key=set" in logged
        assert settings.geoapify_api_key not in logged, (
            "the key itself must never be logged"
        )

    def test_the_database_password_is_masked_in_the_startup_line(
        self, caplog, monkeypatch
    ):
        """The host and database name are useful; the password is not."""

        from pydantic import SecretStr

        monkeypatch.setattr(
            settings,
            "DATABASE_URL",
            SecretStr("postgresql://app:hunter2@db.internal:5432/leadfinder"),
        )
        caplog.set_level(logging.INFO, logger="app")

        log_startup_configuration()

        logged = "\n".join(record.getMessage() for record in caplog.records)

        assert "hunter2" not in logged
        assert "db.internal:5432/leadfinder" in logged, (
            "masking must not throw away the part that identifies the database"
        )

    def test_warns_when_the_api_key_is_missing(self, caplog, monkeypatch):
        """Without a key every scan fails, so it is worth saying at boot."""

        caplog.set_level(logging.WARNING, logger="app")
        monkeypatch.setattr(settings, "GEOAPIFY_API_KEY", None)

        log_startup_configuration()

        logged = "\n".join(record.getMessage() for record in caplog.records)

        assert "GEOAPIFY_API_KEY is not set" in logged
