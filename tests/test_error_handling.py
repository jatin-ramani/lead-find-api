from config import settings
"""
Centralised error handling: one envelope, a request id on everything, and no
internal detail crossing the boundary.
"""

import re
from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app import app
from errors import ErrorCode, REQUEST_ID_HEADER

ENVELOPE_KEYS = {"success", "message", "error", "timestamp", "requestId", "details"}


def assert_envelope(response, *, status, error, message=None):
    """Every failure must satisfy this, whatever produced it."""

    body = response.json()

    assert response.status_code == status
    assert set(body) == ENVELOPE_KEYS, f"unexpected error shape: {sorted(body)}"
    assert body["success"] is False
    assert body["error"] == error
    assert isinstance(body["message"], str) and body["message"]

    # Parseable ISO-8601 at UTC.
    stamp = datetime.fromisoformat(body["timestamp"])
    assert stamp.tzinfo is not None
    assert stamp.utcoffset().total_seconds() == 0

    # Matches the header, so a user quoting either one is quoting the same id.
    assert body["requestId"] == response.headers[REQUEST_ID_HEADER]

    if message is not None:
        assert body["message"] == message

    return body


# ---------------------------------------------------------------------------
# Routes that exist only to raise. Registered once, at import.
# ---------------------------------------------------------------------------

@app.get("/_test/http-error", include_in_schema=False)
def _raise_http_error():
    raise HTTPException(status_code=418, detail="I am a teapot")


@app.get("/_test/db-error", include_in_schema=False)
def _raise_db_error():
    raise OperationalError(
        "SELECT secret_column FROM secret_table WHERE token = 'abc123'",
        {"token": "abc123"},
        Exception("no such table: secret_table"),
    )


@app.get("/_test/sqlalchemy-error", include_in_schema=False)
def _raise_generic_sqlalchemy_error():
    raise SQLAlchemyError("connection pool exhausted")


@app.get("/_test/boom", include_in_schema=False)
def _raise_unexpected():
    secret = "super-secret-token-do-not-leak"
    raise RuntimeError(f"internal failure involving {secret}")


@app.get("/_test/zero-division", include_in_schema=False)
def _raise_zero_division():
    return 1 / 0


@pytest.fixture
def quiet_client():
    """
    TestClient that returns 500s instead of re-raising them.

    With the default `raise_server_exceptions=True` the exception propagates
    into the test, so the handler's response is never observed.
    """
    headers = {"Authorization": f"Bearer {settings.admin_secret}"}
    with TestClient(app, headers=headers, raise_server_exceptions=False) as test_client:
        yield test_client


class TestRequestId:
    def test_every_response_carries_one(self, client):
        response = client.get("/health")

        assert REQUEST_ID_HEADER in response.headers
        assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])

    def test_it_is_different_per_request(self, client):
        first = client.get("/health").headers[REQUEST_ID_HEADER]
        second = client.get("/health").headers[REQUEST_ID_HEADER]

        assert first != second

    def test_an_inbound_id_is_honoured(self, client):
        """Lets a trace survive across services."""

        response = client.get(
            "/health", headers={REQUEST_ID_HEADER: "trace-from-the-gateway"}
        )

        assert response.headers[REQUEST_ID_HEADER] == "trace-from-the-gateway"

    def test_an_absurd_inbound_id_is_truncated(self, client):
        """An unbounded client value would otherwise end up in every log line."""

        response = client.get("/health", headers={REQUEST_ID_HEADER: "x" * 5000})

        assert len(response.headers[REQUEST_ID_HEADER]) == 64

    def test_success_responses_have_it_too(self, client, sample_businesses):
        assert REQUEST_ID_HEADER in client.get("/businesses").headers


class TestHttpExceptions:
    def test_explicit_raise(self, client):
        response = client.get("/_test/http-error")

        assert_envelope(
            response, status=418, error=ErrorCode.HTTP_ERROR,
            message="I am a teapot",
        )

    def test_404_from_an_endpoint(self, client):
        assert_envelope(
            client.get("/businesses/9999"),
            status=404, error=ErrorCode.NOT_FOUND,
            message="Business not found",
        )

    def test_404_from_an_unmatched_route(self, client):
        """Starlette's own 404 must use the envelope too."""

        assert_envelope(
            client.get("/no/such/route"),
            status=404, error=ErrorCode.NOT_FOUND,
        )

    def test_405_method_not_allowed(self, client):
        assert_envelope(
            client.put("/businesses"), status=405, error=ErrorCode.HTTP_ERROR
        )

    def test_400_from_a_business_rule(self, client, business_factory):
        business = business_factory(website=None)

        assert_envelope(
            client.post(f"/businesses/{business.id}/scrape"),
            status=400, error=ErrorCode.HTTP_ERROR,
            message="Business does not have a website.",
        )


class TestValidationErrors:
    def test_body_validation(self, client):
        body = assert_envelope(
            client.request("DELETE", "/businesses", json={"business_ids": ["abc"]}),
            status=422, error=ErrorCode.VALIDATION_ERROR,
            message="Request validation failed.",
        )

        assert isinstance(body["details"], list) and body["details"]
        assert set(body["details"][0]) == {"field", "message", "type"}
        assert body["details"][0]["field"] == "body.business_ids.0"

    def test_missing_required_field(self, client):
        body = assert_envelope(
            client.post("/scan", json={"city": "Ahmedabad"}),
            status=422, error=ErrorCode.VALIDATION_ERROR,
        )

        assert any("category" in d["field"] for d in body["details"])

    def test_query_parameter_validation(self, client):
        body = assert_envelope(
            client.get("/businesses?pageSize=101"),
            status=422, error=ErrorCode.VALIDATION_ERROR,
        )

        assert any("pageSize" in d["field"] for d in body["details"])

    def test_path_parameter_validation(self, client):
        body = assert_envelope(
            client.get("/businesses/not-an-int"),
            status=422, error=ErrorCode.VALIDATION_ERROR,
        )

        assert body["details"][0]["field"] == "path.business_id"

    def test_raw_input_is_not_echoed_back(self, client):
        """Pydantic includes the offending value; reflecting it is needless."""

        response = client.request(
            "DELETE", "/businesses",
            json={"business_ids": ["<script>alert(1)</script>"]},
        )

        assert "<script>" not in response.text


class TestDatabaseErrors:
    def test_operational_error_becomes_a_clean_500(self, quiet_client):
        response = quiet_client.get("/_test/db-error")

        assert_envelope(
            response, status=500, error=ErrorCode.DATABASE_ERROR,
            message="A database error occurred. Please try again.",
        )

    def test_sql_and_parameters_never_reach_the_client(self, quiet_client):
        response = quiet_client.get("/_test/db-error")

        for leak in ("SELECT", "secret_column", "secret_table", "abc123",
                     "no such table"):
            assert leak not in response.text, f"leaked {leak!r}"

    def test_generic_sqlalchemy_error(self, quiet_client):
        response = quiet_client.get("/_test/sqlalchemy-error")

        assert_envelope(response, status=500, error=ErrorCode.DATABASE_ERROR)
        assert "connection pool" not in response.text

    def test_a_real_database_failure_is_handled(self, quiet_client, monkeypatch):
        """Not a synthetic raise — break the query the endpoint actually runs."""

        def broken(*args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))

        monkeypatch.setattr("api.dashboard.get_dashboard_stats", broken)

        response = quiet_client.get("/dashboard/stats")

        assert_envelope(response, status=500, error=ErrorCode.DATABASE_ERROR)
        assert "database is locked" not in response.text


class TestUnexpectedExceptions:
    def test_runtime_error_becomes_a_generic_500(self, quiet_client):
        assert_envelope(
            quiet_client.get("/_test/boom"),
            status=500, error=ErrorCode.INTERNAL_ERROR,
            message="An unexpected error occurred.",
        )

    def test_no_stack_trace_or_internals_are_exposed(self, quiet_client):
        response = quiet_client.get("/_test/boom")

        for leak in ("Traceback", "RuntimeError", "super-secret-token-do-not-leak",
                     "errors.py", "File \"", "line "):
            assert leak not in response.text, f"leaked {leak!r}"

    def test_zero_division_is_handled_too(self, quiet_client):
        assert_envelope(
            quiet_client.get("/_test/zero-division"),
            status=500, error=ErrorCode.INTERNAL_ERROR,
        )

    def test_the_failure_is_logged_with_the_request_id(self, quiet_client, caplog):
        import logging

        with caplog.at_level(logging.ERROR, logger="errors"):
            response = quiet_client.get(
                "/_test/boom", headers={REQUEST_ID_HEADER: "trace-me-please"}
            )

        assert response.json()["requestId"] == "trace-me-please"

        record = next(r for r in caplog.records if "Unhandled" in r.message)

        assert record.exc_info is not None, "traceback not captured in the log"
        assert "RuntimeError" in record.getMessage()
        assert getattr(record, "request_id", None) == "trace-me-please"

    def test_debug_mode_includes_the_exception_type(self, quiet_client, monkeypatch):
        """Local debugging is bearable; production still says nothing."""

        import errors

        monkeypatch.setattr(errors.settings, "DEBUG", True)

        body = quiet_client.get("/_test/boom").json()

        assert body["details"]["exception"] == "RuntimeError"


class TestConflictEnvelope:
    def test_409_uses_the_shared_envelope_with_the_job_id(
        self, client, db, sample_businesses
    ):
        from database import crud

        job_id = crud.create_scrape_job(db, total_websites=1)
        crud.update_scrape_job(db, job_id, status=crud.RUNNING_STATUS)

        body = assert_envelope(
            client.post("/scrape/all"),
            status=409, error=ErrorCode.CONFLICT,
            message="A scrape job is already running.",
        )

        assert body["details"] == {"job_id": job_id}

    def test_every_bulk_endpoint_agrees(self, client, db, sample_businesses):
        from database import crud

        job_id = crud.create_scrape_job(db, total_websites=1)
        crud.update_scrape_job(db, job_id, status=crud.RUNNING_STATUS)

        for endpoint, payload in (
            ("/scrape/all", None),
            ("/scrape/missing", None),
            ("/scrape/retry-failed", None),
            ("/scrape/selected", {"business_ids": [1]}),
        ):
            response = client.post(endpoint, json=payload)

            assert_envelope(response, status=409, error=ErrorCode.CONFLICT)


class TestSuccessPathsAreUnaffected:
    """The envelope must not have leaked into successful responses."""

    def test_success_bodies_are_unchanged(self, client, sample_businesses):
        body = client.get("/businesses").json()

        assert set(body) == {"success", "data", "pagination"}
        assert body["success"] is True

    def test_streaming_csv_still_streams(self, client, sample_businesses):
        """A response-buffering middleware would break this."""

        response = client.get("/businesses/export/csv")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.text.startswith("﻿")
        assert len(response.text.splitlines()) == 11
        assert REQUEST_ID_HEADER in response.headers

    def test_background_tasks_still_run(self, client, db, sample_businesses, monkeypatch):
        """BaseHTTPMiddleware historically broke these; the ASGI one must not."""

        from database import crud

        monkeypatch.setattr(
            "services.bulk_scraper.scrape_website",
            lambda url: {"success": True, "title": "T", "meta_description": None,
                         "emails": [], "facebook": None, "instagram": None,
                         "linkedin": None, "twitter": None, "youtube": None,
                         "whatsapp": None},
        )

        job_id = client.post("/scrape/all").json()["job_id"]
        db.expire_all()

        assert crud.get_scrape_job(db, job_id).status == crud.COMPLETED_STATUS


class TestOpenApiDocumentsTheEnvelope:
    def test_error_schema_is_published(self, client):
        schema = client.get("/openapi.json").json()

        error_schema = schema["components"]["schemas"]["ErrorResponse"]

        assert set(error_schema["properties"]) == ENVELOPE_KEYS

    def test_documented_examples_match_the_real_shape(self, client):
        """Docs that describe a different body than the code emits are worse
        than no docs."""

        schema = client.get("/openapi.json").json()

        # /health is deliberately exempt: its 503 body is a probe result that
        # orchestrators read (status / database), not an error report. It is
        # returned, never raised, so it does not pass through the handlers.
        exempt = {"/health"}

        examples = [
            content["application/json"]["example"]
            for path, methods in schema["paths"].items()
            if path not in exempt
            for operation in methods.values()
            for code, response in operation.get("responses", {}).items()
            if code.startswith(("4", "5"))
            for content in [response.get("content", {})]
            if "application/json" in content
            and "example" in content["application/json"]
        ]

        assert examples, "no error examples found in the schema"

        # FastAPI runs the schema through jsonable_encoder(exclude_none=True),
        # so a documented `"details": null` is stripped. Everything else must
        # be present and nothing outside the envelope may appear.
        required = ENVELOPE_KEYS - {"details"}

        for example in examples:
            assert set(example) <= ENVELOPE_KEYS, f"stale doc example: {example}"
            assert required <= set(example), f"incomplete doc example: {example}"
