"""
Structured logging.

Assertions run against the *formatted output*, not just the records — a field
that exists on a LogRecord but never reaches the stream is not logged.
"""

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import logging_config
from app import app
from config import settings
from logging_config import (
    HumanFormatter,
    JsonFormatter,
    RequestContextFilter,
    build_formatter,
    configure_logging,
    redact,
)

ACCESS = "access"


# ---------------------------------------------------------------------------
# Routes that exist only to be logged. Registered once, at import.
# ---------------------------------------------------------------------------

@app.get("/_log/ok", include_in_schema=False)
def _log_ok():
    return {"ok": True}


@app.get("/_log/boom", include_in_schema=False)
def _log_boom():
    raise RuntimeError("unhandled failure for the logging tests")


@app.get("/_log/slow-body", include_in_schema=False)
def _log_slow_body():
    """Streams, so time-to-headers and time-to-done genuinely differ."""

    import time

    from fastapi.responses import StreamingResponse

    def chunks():
        for _ in range(3):
            time.sleep(0.02)
            yield b"x" * 16

    return StreamingResponse(chunks(), media_type="text/plain")


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

class Capture:
    """The formatted stream, plus the records that produced it."""

    def __init__(self, stream, records):
        self.stream = stream
        self.records = records

    @property
    def text(self):
        return self.stream.getvalue()

    def lines(self, logger_name=None):
        return [
            line
            for line, record in zip(self.text.splitlines(), self.records)
            if logger_name is None or record.name == logger_name
        ]

    def access_record(self):
        matching = [r for r in self.records if r.name == ACCESS]

        assert matching, f"no access record; got {[r.name for r in self.records]}"

        return matching[-1]

    def access_line(self):
        lines = self.lines(ACCESS)

        assert lines, "no access line in the formatted output"

        return lines[-1]


@pytest.fixture
def capture(monkeypatch):
    """
    Root handler writing to a buffer through the real formatter and filter.

    The suite runs at CRITICAL so the console stays clean; these tests need
    INFO, and put it back afterwards.
    """

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level

    records = []

    class Recorder(logging.Filter):
        def filter(self, record):
            records.append(record)
            return True

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(build_formatter())
    handler.addFilter(RequestContextFilter())
    handler.addFilter(Recorder())

    root.handlers = [handler]
    root.setLevel(logging.INFO)

    yield Capture(stream, records)

    root.handlers = previous_handlers
    root.setLevel(previous_level)


@pytest.fixture
def logged_client(capture):
    headers = {"Authorization": f"Bearer {settings.admin_secret}"}
    with TestClient(app, headers=headers, raise_server_exceptions=False) as client:
        yield client, capture


# ---------------------------------------------------------------------------


class TestAccessRecordFields:
    """Requirement 2: every field, on every request."""

    def test_a_request_produces_exactly_one_access_record(self, logged_client):
        client, capture = logged_client

        client.get("/_log/ok")

        assert len([r for r in capture.records if r.name == ACCESS]) == 1

    def test_every_required_field_is_present(self, logged_client):
        client, capture = logged_client

        client.get("/_log/ok")
        record = capture.access_record()

        assert record.request_id and record.request_id != "-"
        assert record.method == "GET"
        assert record.path == "/_log/ok"
        assert record.status == 200
        assert isinstance(record.duration_ms, float)
        assert record.client_ip
        assert record.name == ACCESS
        assert record.levelname == "INFO"
        assert record.created > 0

    def test_the_formatted_line_carries_them_too(self, logged_client):
        """A field on the record that never reaches the stream is not logged."""

        client, capture = logged_client

        client.get("/_log/ok")
        line = capture.access_line()

        assert "GET /_log/ok -> 200 in " in line
        assert "ms" in line
        assert "client_ip=" in line
        assert ACCESS in line

    def test_the_request_id_matches_the_response_header(self, logged_client):
        """The id in the log is the one the caller can quote back."""

        client, capture = logged_client

        response = client.get("/_log/ok")

        assert capture.access_record().request_id == response.headers[
            "X-Request-ID"
        ]

    def test_an_inbound_request_id_is_used(self, logged_client):
        client, capture = logged_client

        client.get("/_log/ok", headers={"X-Request-ID": "trace-from-upstream"})

        assert capture.access_record().request_id == "trace-from-upstream"

    def test_application_logs_share_the_request_id(self, logged_client):
        """
        The point of the whole exercise: one request's lines can be grepped
        out of an interleaved log by a single id.
        """

        client, capture = logged_client

        client.get("/businesses/9999")

        # Everything emitted while handling the request. The lifespan lines and
        # httpx's own round-trip log sit outside it and correctly have no id.
        during = [r for r in capture.records if r.request_id != "-"]

        assert len(during) >= 2, "expected the handler's line and the access line"
        assert len({r.request_id for r in during}) == 1
        assert {r.name for r in during} == {"errors", ACCESS}

    def test_method_and_path_reach_non_access_records(self, logged_client):
        """A handler's own log line must say which request it belongs to."""

        client, capture = logged_client

        client.get("/businesses/9999")
        errors_line = capture.lines("errors")[-1]

        assert "method=GET" in errors_line
        assert "path=/businesses/9999" in errors_line


class TestDuration:
    def test_duration_is_measured_and_plausible(self, logged_client):
        client, capture = logged_client

        client.get("/_log/ok")
        record = capture.access_record()

        assert 0 <= record.duration_ms < 10_000
        assert f"in {record.duration_ms}ms" in capture.access_line()

    def test_time_to_headers_is_separated_from_time_to_done(self, logged_client):
        """
        A streamed response answers immediately and finishes later. Reporting
        the total as "duration" would blame the endpoint for the download.
        """

        client, capture = logged_client

        client.get("/_log/slow-body")
        record = capture.access_record()

        assert record.total_ms > record.duration_ms
        assert record.total_ms >= 50, "three 20ms chunks should have been sent"
        assert "completed in" in capture.access_line()

    def test_a_plain_response_does_not_report_two_timings(self, logged_client):
        client, capture = logged_client

        client.get("/_log/ok")

        assert "completed in" not in capture.access_line()


class TestLevels:
    @pytest.mark.parametrize(
        "path,expected_level,expected_status",
        [
            ("/_log/ok", "INFO", 200),
            ("/businesses/9999", "WARNING", 404),
            ("/_log/boom", "ERROR", 500),
        ],
    )
    def test_level_follows_the_status(
        self, logged_client, path, expected_level, expected_status
    ):
        """`grep -w ERROR` must find the failures and nothing else."""

        client, capture = logged_client

        client.get(path)
        record = capture.access_record()

        assert record.status == expected_status
        assert record.levelname == expected_level


class TestExceptionLogging:
    def test_an_unhandled_exception_is_access_logged_as_500(self, logged_client):
        client, capture = logged_client

        response = client.get("/_log/boom")
        record = capture.access_record()

        assert response.status_code == 500
        assert record.status == 500
        assert record.levelname == "ERROR"
        assert record.unhandled_exception is True
        assert isinstance(record.duration_ms, float)

    def test_the_traceback_is_logged_with_the_same_context(self, logged_client):
        client, capture = logged_client

        client.get("/_log/boom")

        traced = [r for r in capture.records if r.exc_info]

        assert traced, "the exception was never logged with a traceback"

        record = traced[-1]

        assert record.request_id == capture.access_record().request_id
        assert record.method == "GET"
        assert record.path == "/_log/boom"

    def test_the_traceback_reaches_the_output(self, logged_client):
        client, capture = logged_client

        client.get("/_log/boom")

        assert "RuntimeError" in capture.text
        assert "unhandled failure for the logging tests" in capture.text

    def test_the_client_still_gets_the_error_envelope(self, logged_client):
        """Requirement 8: logging must not change what the caller sees."""

        client, _ = logged_client

        body = client.get("/_log/boom").json()

        assert body["success"] is False
        assert body["error"] == "INTERNAL_ERROR"
        assert "RuntimeError" not in json.dumps(body)


class TestNoSecretLeakage:
    def test_the_api_key_is_redacted_wherever_it_appears(self, capture):
        """A backstop for the line nobody predicted."""

        logging.getLogger("careless").info(
            "calling with apiKey=%s", settings.geoapify_api_key
        )

        assert settings.geoapify_api_key not in capture.text
        assert "***REDACTED***" in capture.text

    def test_the_database_password_is_redacted(self, capture, monkeypatch):
        monkeypatch.setattr(
            settings,
            "DATABASE_URL",
            SecretStr("postgresql://app:sup3rsecretpw@db.internal/leadfinder"),
        )

        logging.getLogger("careless").error(
            "connection failed: postgresql://app:sup3rsecretpw@db.internal/leadfinder"
        )

        assert "sup3rsecretpw" not in capture.text
        assert "db.internal" in capture.text, "only the password should go"

    def test_a_traceback_containing_a_secret_is_redacted(self, capture):
        try:
            raise RuntimeError(f"boom with {settings.geoapify_api_key}")
        except RuntimeError:
            logging.getLogger("careless").exception("failed")

        assert settings.geoapify_api_key not in capture.text
        assert "Traceback" in capture.text

    def test_a_sqlite_url_is_not_redacted(self, capture):
        """
        Over-redaction is its own bug: SQLite has no password, and the path is
        the one thing worth knowing when a process opens the wrong file.
        """

        logging.getLogger("careless").info("using %s", settings.database_url)

        assert "sqlite" in capture.text
        assert settings.database_url in capture.text

    def test_redaction_is_a_no_op_without_secrets(self, monkeypatch):
        monkeypatch.setattr(settings, "GEOAPIFY_API_KEY", None)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr("sqlite:///x.db"))

        assert redact("nothing secret here") == "nothing secret here"

    def test_a_short_value_is_not_used_for_redaction(self, monkeypatch):
        """Redacting a 3-character string would corrupt half the log."""

        monkeypatch.setattr(settings, "GEOAPIFY_API_KEY", SecretStr("abc"))

        assert redact("abc appears in many words: abcdefg") == (
            "abc appears in many words: abcdefg"
        )

    def test_no_request_logs_the_key(self, logged_client):
        client, capture = logged_client

        client.get("/businesses")
        client.get("/system")
        client.post("/scan", json={"city": "X"})

        assert settings.geoapify_api_key not in capture.text


class TestJsonOutput:
    @pytest.fixture
    def json_capture(self, monkeypatch, capture):
        monkeypatch.setattr(settings, "LOG_JSON", True)
        capture.stream.truncate(0)
        capture.stream.seek(0)
        capture.records.clear()

        for handler in logging.getLogger().handlers:
            handler.setFormatter(JsonFormatter())

        return capture

    def test_every_line_is_valid_json(self, json_capture):
        with TestClient(app, headers={'Authorization': f'Bearer {settings.admin_secret}'}) as client:
            client.get("/_log/ok")

        lines = [l for l in json_capture.text.splitlines() if l.strip()]

        assert lines

        for line in lines:
            json.loads(line)

    def test_the_access_object_carries_every_field(self, json_capture):
        with TestClient(app, headers={'Authorization': f'Bearer {settings.admin_secret}'}) as client:
            client.get("/_log/ok")

        payloads = [json.loads(l) for l in json_capture.text.splitlines() if l]
        access = [p for p in payloads if p["logger"] == ACCESS][-1]

        assert set(access) >= {
            "timestamp", "level", "logger", "requestId", "message",
            "method", "path", "status", "duration_ms", "client_ip",
        }
        assert access["method"] == "GET"
        assert access["path"] == "/_log/ok"
        assert access["status"] == 200
        assert access["level"] == "INFO"

    def test_json_still_redacts(self, json_capture):
        logging.getLogger("careless").info("key=%s", settings.geoapify_api_key)

        line = [l for l in json_capture.text.splitlines() if l][-1]

        assert settings.geoapify_api_key not in line
        assert json.loads(line)["message"].endswith("***REDACTED***")

    def test_an_exception_is_a_field_not_a_broken_line(self, json_capture):
        try:
            raise ValueError("structured failure")
        except ValueError:
            logging.getLogger("careless").exception("failed")

        line = [l for l in json_capture.text.splitlines() if l][-1]
        payload = json.loads(line)

        assert "Traceback" in payload["exception"]
        assert payload["message"] == "failed"

    def test_switching_format_is_configuration_only(self, monkeypatch):
        monkeypatch.setattr(settings, "LOG_JSON", False)
        assert isinstance(build_formatter(), HumanFormatter)

        monkeypatch.setattr(settings, "LOG_JSON", True)
        assert isinstance(build_formatter(), JsonFormatter)


class TestExcludedPaths:
    def test_a_quiet_path_is_not_logged_when_healthy(
        self, logged_client, monkeypatch
    ):
        client, capture = logged_client
        monkeypatch.setattr(settings, "LOG_ACCESS_EXCLUDE_PATHS", ["/health"])

        client.get("/health")

        assert [r for r in capture.records if r.name == ACCESS] == []

    def test_a_quiet_path_is_logged_when_it_fails(self, logged_client, monkeypatch):
        """The moment a probe fails it is the most interesting line in the file."""

        client, capture = logged_client
        monkeypatch.setattr(settings, "LOG_ACCESS_EXCLUDE_PATHS", ["/_log/boom"])

        client.get("/_log/boom")

        assert capture.access_record().status == 500

    def test_nothing_is_excluded_by_default(self, logged_client):
        client, capture = logged_client

        client.get("/health")

        assert capture.access_record().path == "/health"


class TestClientAddress:
    def test_the_socket_peer_is_used_by_default(self, logged_client):
        client, capture = logged_client

        client.get("/_log/ok", headers={"X-Forwarded-For": "1.2.3.4"})

        assert capture.access_record().client_ip != "1.2.3.4", (
            "a forgeable header must not be trusted unless configured"
        )

    def test_the_forwarded_header_is_used_when_trusted(
        self, logged_client, monkeypatch
    ):
        client, capture = logged_client
        monkeypatch.setattr(settings, "LOG_TRUST_PROXY_HEADERS", True)

        client.get(
            "/_log/ok", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
        )

        assert capture.access_record().client_ip == "203.0.113.7"


class TestSetup:
    def test_configure_logging_does_not_stack_handlers(self):
        """Re-running it must not print every line twice."""

        root = logging.getLogger()
        previous = list(root.handlers)
        previous_level = root.level

        try:
            configure_logging()
            first = len(root.handlers)

            configure_logging()

            assert len(root.handlers) == first == 1
        finally:
            root.handlers = previous
            root.setLevel(previous_level)

    def test_uvicorn_loggers_are_adopted(self, monkeypatch):
        """
        uvicorn sets propagate=False, so without this its lines would be the
        only unstructured, unredacted output in the file.
        """

        target = logging.getLogger("uvicorn.access")
        handler = logging.StreamHandler(io.StringIO())
        monkeypatch.setattr(target, "handlers", [handler])

        logging_config.adopt_logger(target)

        assert isinstance(handler.formatter, (HumanFormatter, JsonFormatter))
        assert any(
            isinstance(f, RequestContextFilter) for f in handler.filters
        )

    def test_uvicorn_access_is_silenced_so_requests_are_not_logged_twice(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings, "LOG_UVICORN_ACCESS", False)
        root = logging.getLogger()
        previous, previous_level = list(root.handlers), root.level

        try:
            configure_logging()

            assert logging.getLogger("uvicorn.access").disabled is True
        finally:
            root.handlers = previous
            root.setLevel(previous_level)
            logging.getLogger("uvicorn.access").disabled = False

    def test_uvicorn_access_can_be_kept(self, monkeypatch):
        monkeypatch.setattr(settings, "LOG_UVICORN_ACCESS", True)
        root = logging.getLogger()
        previous, previous_level = list(root.handlers), root.level

        try:
            configure_logging()

            assert logging.getLogger("uvicorn.access").disabled is False
        finally:
            root.handlers = previous
            root.setLevel(previous_level)

    def test_records_outside_a_request_still_format(self, capture):
        """A startup line has no method or path and must not crash the format."""

        logging.getLogger("standalone").info("no request in sight")

        line = capture.text.strip()

        assert "no request in sight" in line
        assert "[-]" in line, "the request id falls back to a placeholder"
        assert "method=" not in line
