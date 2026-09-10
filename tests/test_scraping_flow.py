"""
Scraping endpoints and the bulk-scrape engine.

`scrape_website` is replaced everywhere so no test reaches the network.
TestClient runs BackgroundTasks synchronously once the response is returned,
so a bulk job has finished by the time the request call returns.
"""

import json

import pytest

from database import crud
from database.models import WebsiteData
from services import bulk_scraper


def scrape_result(success=True, title="Scraped Title", **overrides):
    """A response shaped exactly like services.website_scraper.scrape_website."""

    if not success:
        return {"success": False, "error": overrides.get("error", "Could not connect")}

    payload = {
        "success": True,
        "title": title,
        "meta_description": "A description.",
        "emails": ["found@example.test"],
        "facebook": "https://facebook.com/x",
        "instagram": None,
        "linkedin": None,
        "twitter": None,
        "youtube": None,
        "whatsapp": None,
    }
    payload.update(overrides)

    return payload


@pytest.fixture
def mock_scrape(monkeypatch):
    """
    Patch scrape_website in every module that imported it by name.

    `from ... import scrape_website` binds the function into each importing
    module, so patching the source module alone would leave the callers using
    the original and hitting the network.
    """

    def _install(handler):
        calls = []

        def fake(url):
            calls.append(url)
            return handler(url) if callable(handler) else handler

        monkeypatch.setattr("services.bulk_scraper.scrape_website", fake)
        monkeypatch.setattr("api.business.scrape_website", fake)

        return calls

    return _install


class TestSingleBusinessScrape:
    def test_success_stores_the_result(self, client, db, business_factory, mock_scrape):
        business = business_factory()
        mock_scrape(scrape_result())

        response = client.post(f"/businesses/{business.id}/scrape")

        assert response.status_code == 200
        assert response.json()["success"] is True

        record = crud.get_website_data(db, business.id)
        assert record.title == "Scraped Title"
        assert record.status == crud.COMPLETED_STATUS
        assert json.loads(record.emails) == ["found@example.test"]

    def test_missing_business_returns_404(self, client, mock_scrape):
        mock_scrape(scrape_result())

        assert client.post("/businesses/9999/scrape").status_code == 404

    @pytest.mark.parametrize("website", [None, "", "   "])
    def test_business_without_a_website_returns_400(
        self, client, business_factory, mock_scrape, website
    ):
        business = business_factory(website=website, place_id=f"pid-{website!r}")
        mock_scrape(scrape_result())

        response = client.post(f"/businesses/{business.id}/scrape")

        assert response.status_code == 400
        assert response.json()["message"] == "Business does not have a website."

    def test_scrape_failure_returns_502_and_records_it(
        self, client, db, business_factory, mock_scrape
    ):
        business = business_factory()
        mock_scrape(scrape_result(success=False, error="Could not connect to the site."))

        response = client.post(f"/businesses/{business.id}/scrape")

        assert response.status_code == 502
        assert response.json()["message"] == "Could not connect to the site."

        record = crud.get_website_data(db, business.id)
        assert record is not None, "failure must be recorded so retry-failed can find it"
        assert record.status == crud.FAILED_STATUS

    def test_failure_preserves_earlier_successful_content(
        self, client, db, business_factory, mock_scrape
    ):
        business = business_factory()

        mock_scrape(scrape_result(title="Good Title"))
        client.post(f"/businesses/{business.id}/scrape")

        mock_scrape(scrape_result(success=False))
        client.post(f"/businesses/{business.id}/scrape")

        record = crud.get_website_data(db, business.id)

        assert record.status == crud.FAILED_STATUS
        assert record.title == "Good Title"

    def test_repeated_scrapes_do_not_duplicate_rows(
        self, client, db, business_factory, mock_scrape
    ):
        business = business_factory()
        mock_scrape(scrape_result())

        for _ in range(3):
            client.post(f"/businesses/{business.id}/scrape")

        assert db.query(WebsiteData).filter(
            WebsiteData.business_id == business.id
        ).count() == 1


class TestBulkScrapeEndpoints:
    @pytest.mark.parametrize(
        "endpoint,message",
        [
            ("/scrape/all", "Bulk scraping started."),
            ("/scrape/missing", "Missing website scraping started."),
            ("/scrape/retry-failed", "Retry failed scraping started."),
        ],
    )
    def test_queue_a_job_and_return_immediately(
        self, client, sample_businesses, mock_scrape, endpoint, message
    ):
        mock_scrape(scrape_result())

        body = client.post(endpoint).json()

        assert body["success"] is True
        assert body["message"] == message
        assert isinstance(body["job_id"], int)

    def test_selected_endpoint(self, client, sample_businesses, mock_scrape):
        mock_scrape(scrape_result())

        body = client.post(
            "/scrape/selected", json={"business_ids": [1, 1, 9999]}
        ).json()

        assert body["message"] == "Selected website scraping started."

    def test_selected_rejects_a_malformed_body(self, client):
        assert client.post(
            "/scrape/selected", json={"business_ids": ["abc"]}
        ).status_code == 422

    def test_job_records_real_totals(self, client, db, sample_businesses, mock_scrape):
        mock_scrape(scrape_result())

        job_id = client.post("/scrape/all").json()["job_id"]
        job = crud.get_scrape_job(db, job_id)

        # 5 of the 10 fixtures have a usable website
        assert job.total_websites == 5
        assert job.completed == 5
        assert job.success == 5
        assert job.status == crud.COMPLETED_STATUS

    def test_concurrency_guard_returns_409(self, client, db, sample_businesses):
        """A second job must be refused while one is Running."""

        job_id = crud.create_scrape_job(db, total_websites=1)
        crud.update_scrape_job(db, job_id, status=crud.RUNNING_STATUS)

        for endpoint in ("/scrape/all", "/scrape/missing", "/scrape/retry-failed"):
            response = client.post(endpoint)
            body = response.json()

            assert response.status_code == 409
            # Shared error envelope; the running job id travels in `details`.
            assert body["success"] is False
            assert body["error"] == "CONFLICT"
            assert body["message"] == "A scrape job is already running."
            assert body["details"] == {"job_id": job_id}

        selected = client.post("/scrape/selected", json={"business_ids": [1]})
        assert selected.status_code == 409


class TestBulkScraperEngine:
    def test_all_mode_scrapes_every_eligible_business(
        self, db, sample_businesses, mock_scrape
    ):
        calls = mock_scrape(scrape_result())
        job_id = crud.create_scrape_job(db, total_websites=5)

        summary = bulk_scraper.scrape_all_websites(job_id)

        assert summary["status"] == "Completed"
        assert summary["total_websites"] == 5
        assert len(calls) == 5
        assert all(url.strip() for url in calls), "blank website was scraped"

    def test_missing_mode_skips_already_scraped(self, db, sample_businesses, mock_scrape):
        targets = [i for i, _ in crud.get_scrape_targets(db)]
        crud.save_website_data(db, business_id=targets[0], title="already done")

        mock_scrape(scrape_result())
        job_id = crud.create_scrape_job(db, total_websites=0)

        summary = bulk_scraper.scrape_missing_websites(job_id)

        assert summary["total_websites"] == len(targets) - 1

    def test_retry_mode_only_touches_previous_failures(
        self, db, sample_businesses, mock_scrape
    ):
        targets = [i for i, _ in crud.get_scrape_targets(db)]
        crud.save_website_data(db, business_id=targets[0], status=crud.FAILED_STATUS)
        crud.save_website_data(db, business_id=targets[1], status=crud.COMPLETED_STATUS)

        mock_scrape(scrape_result())
        job_id = crud.create_scrape_job(db, total_websites=1)

        summary = bulk_scraper.scrape_failed_websites(job_id)

        assert summary["total_websites"] == 1
        assert summary["success"] == 1

    def test_selected_mode(self, db, sample_businesses, mock_scrape):
        mock_scrape(scrape_result())
        job_id = crud.create_scrape_job(db, total_websites=1)

        summary = bulk_scraper.scrape_selected_websites(job_id, [1])

        assert summary["total_websites"] == 1

    def test_one_failure_does_not_stop_the_run(self, db, sample_businesses, mock_scrape):
        seen = {"n": 0}

        def flaky(url):
            seen["n"] += 1
            return scrape_result(success=seen["n"] != 2)

        mock_scrape(flaky)
        job_id = crud.create_scrape_job(db, total_websites=5)

        summary = bulk_scraper.scrape_all_websites(job_id)

        assert summary["completed"] == 5
        assert summary["failed"] == 1
        assert summary["success"] == 4

    def test_progress_and_terminal_state(self, db, sample_businesses, mock_scrape):
        mock_scrape(scrape_result())
        job_id = crud.create_scrape_job(db, total_websites=5)

        bulk_scraper.scrape_all_websites(job_id)
        job = crud.get_scrape_job(db, job_id)

        assert job.status == crud.COMPLETED_STATUS
        assert job.progress == 100
        assert job.completed_at is not None
        assert job.current_business_id is None, "stale progress pointer left behind"

    def test_missing_job_id_is_a_no_op(self, db, mock_scrape):
        mock_scrape(scrape_result())

        summary = bulk_scraper.scrape_all_websites(999_999)

        assert summary["status"] == "Missing"

    def test_no_targets_completes_without_dividing_by_zero(self, db, mock_scrape):
        mock_scrape(scrape_result())
        job_id = crud.create_scrape_job(db, total_websites=0)

        summary = bulk_scraper.scrape_all_websites(job_id)
        job = crud.get_scrape_job(db, job_id)

        assert summary["total_websites"] == 0
        assert job.status == crud.COMPLETED_STATUS
        assert job.progress == 100

    def test_crash_before_the_loop_marks_the_job_failed(
        self, db, sample_businesses, monkeypatch, mock_scrape
    ):
        """A job must never be left stuck on Running."""

        mock_scrape(scrape_result())
        monkeypatch.setattr(
            bulk_scraper, "get_scrape_targets",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        job_id = crud.create_scrape_job(db, total_websites=3)
        crud.update_scrape_job(db, job_id, current_business_id=1)

        summary = bulk_scraper.scrape_all_websites(job_id)
        db.expire_all()
        job = crud.get_scrape_job(db, job_id)

        assert summary["status"] == "Failed"
        assert job.status == "Failed"
        assert job.completed_at is not None
        assert job.current_business_id is None

    def test_persistence_error_is_counted_not_fatal(
        self, db, sample_businesses, monkeypatch, mock_scrape
    ):
        mock_scrape(scrape_result())

        def exploding(**kwargs):
            raise RuntimeError("database write failed")

        monkeypatch.setattr(bulk_scraper, "save_website_data", exploding)

        job_id = crud.create_scrape_job(db, total_websites=5)
        summary = bulk_scraper.scrape_all_websites(job_id)

        assert summary["status"] == "Completed"
        assert summary["failed"] == 5
        assert summary["success"] == 0

    def test_session_is_usable_after_a_failed_run(
        self, db, sample_businesses, monkeypatch, mock_scrape
    ):
        """A rollback must leave the pool healthy for the next job."""

        mock_scrape(scrape_result())
        monkeypatch.setattr(
            bulk_scraper, "save_website_data",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("nope")),
        )

        first = crud.create_scrape_job(db, total_websites=5)
        bulk_scraper.scrape_all_websites(first)

        monkeypatch.undo()
        mock_scrape(scrape_result())

        second = crud.create_scrape_job(db, total_websites=5)
        summary = bulk_scraper.scrape_all_websites(second)

        assert summary["success"] == 5

    def test_no_job_is_left_running(self, db, sample_businesses, mock_scrape):
        mock_scrape(scrape_result())

        for _ in range(3):
            bulk_scraper.scrape_all_websites(
                crud.create_scrape_job(db, total_websites=5)
            )

        db.expire_all()
        stuck = [j.id for j in crud.get_scrape_jobs(db) if j.status == "Running"]

        assert stuck == []


SCAN_BODY = {"city": "Ahmedabad", "category": "commercial"}


def raising(exception):
    """A stand-in for any function that raises instead of returning."""

    def _raise(*args, **kwargs):
        raise exception

    return _raise


class TestScanEndpoint:
    """
    Test suite for continuous background scanner endpoints and job tracking.
    """

    def test_scan_runs_and_saves(self, client, db, monkeypatch):
        from tests.fakes import geoapify_feature
        import services.scan_worker as worker_mod

        monkeypatch.setattr(
            "services.scan_worker.geocode_city",
            lambda city: (23.0225, 72.5714, "place_123"),
        )
        monkeypatch.setattr(
            "services.scan_worker.fetch_places_page",
            lambda **kwargs: [
                geoapify_feature(name="Found A", place_id="p-a", phone="+15550100"),
                geoapify_feature(name="Found B", place_id="p-b", phone="+15550101"),
            ],
        )
        monkeypatch.setattr(
            "services.scan_worker._spawn_background_worker",
            lambda job_id: worker_mod._run_scan_job_worker(job_id),
        )

        response = client.post("/scan", json=SCAN_BODY)

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["job_id"] > 0

        names = {b.name for b in crud.get_businesses(db, page_size=100)["data"]}
        assert "Found A" in names
        assert "Found B" in names

    def test_successful_scan_completes_the_job_with_its_counts(
        self, client, db, monkeypatch
    ):
        from tests.fakes import geoapify_feature
        import services.scan_worker as worker_mod

        monkeypatch.setattr(
            "services.scan_worker.geocode_city",
            lambda city: (23.0225, 72.5714, "place_123"),
        )
        monkeypatch.setattr(
            "services.scan_worker.fetch_places_page",
            lambda **kwargs: [
                geoapify_feature(name=f"B{i}", place_id=f"p-{i}", phone=f"+1555010{i}") for i in range(3)
            ],
        )
        monkeypatch.setattr(
            "services.scan_worker._spawn_background_worker",
            lambda job_id: worker_mod._run_scan_job_worker(job_id),
        )

        response = client.post("/scan", json=SCAN_BODY)
        assert response.status_code == 200

        job = crud.get_latest_scan_job(db)
        assert job.status == crud.COMPLETED_STATUS
        assert job.progress == 100
        assert job.businesses_stored > 0

    def test_scan_records_a_job(self, client, db, monkeypatch):
        import services.scan_worker as worker_mod

        monkeypatch.setattr(
            "services.scan_worker.geocode_city",
            lambda city: (23.0225, 72.5714, "place_123"),
        )
        monkeypatch.setattr(
            "services.scan_worker.fetch_places_page",
            lambda **kwargs: [],
        )
        monkeypatch.setattr(
            "services.scan_worker._spawn_background_worker",
            lambda job_id: worker_mod._run_scan_job_worker(job_id),
        )

        response = client.post("/scan", json=SCAN_BODY)
        assert response.status_code == 200

        job = crud.get_latest_scan_job(db)
        assert job.city == "Ahmedabad"
        assert job.status == crud.COMPLETED_STATUS

    def test_a_scan_that_finds_nothing_still_succeeds(self, client, db, monkeypatch):
        """Zero results is an outcome, not a failure."""
        import services.scan_worker as worker_mod

        monkeypatch.setattr(
            "services.scan_worker.geocode_city",
            lambda city: (23.0225, 72.5714, "place_123"),
        )
        monkeypatch.setattr(
            "services.scan_worker.fetch_places_page",
            lambda **kwargs: [],
        )
        monkeypatch.setattr(
            "services.scan_worker._spawn_background_worker",
            lambda job_id: worker_mod._run_scan_job_worker(job_id),
        )

        response = client.post("/scan", json=SCAN_BODY)

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert crud.get_latest_scan_job(db).status == crud.COMPLETED_STATUS

    @pytest.mark.parametrize(
        "payload",
        [{}, {"city": "A"}, {"category": "c"}, {"city": None, "category": None}],
    )
    def test_malformed_scan_body_returns_422(self, client, payload):
        assert client.post("/scan", json=payload).status_code == 422

    def test_geocoding_failure_returns_400(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "services.scan_worker.geocode_city",
            raising(ValueError("Could not find coordinates for city")),
        )

        response = client.post(
            "/scan", json={"city": "NonExistentCity12345", "category": "commercial"}
        )
        assert response.status_code == 400
        assert response.json()["success"] is False

    def test_unexpected_exception_in_worker_fails_the_job(
        self, client, db, monkeypatch
    ):
        import services.scan_worker as worker_mod

        monkeypatch.setattr(
            "services.scan_worker.geocode_city",
            lambda city: (23.0225, 72.5714, "place_123"),
        )
        monkeypatch.setattr(
            "services.scan_worker._execute_search_unit",
            raising(RuntimeError("fatal worker crash")),
        )
        monkeypatch.setattr(
            "services.scan_worker._spawn_background_worker",
            lambda job_id: worker_mod._run_scan_job_worker(job_id),
        )

        response = client.post("/scan", json=SCAN_BODY)
        assert response.status_code == 200

        db.expire_all()
        job = crud.get_latest_scan_job(db)
        assert job.status == crud.FAILED_STATUS

    def test_a_failed_scan_never_leaves_the_job_running(
        self, client, db, monkeypatch
    ):
        """A job stuck on "Running" would be indistinguishable from a live one."""
        import services.scan_worker as worker_mod

        monkeypatch.setattr(
            "services.scan_worker.geocode_city",
            lambda city: (23.0225, 72.5714, "place_123"),
        )
        monkeypatch.setattr(
            "services.scan_worker._execute_search_unit",
            raising(RuntimeError("worker failed unexpectedly")),
        )
        monkeypatch.setattr(
            "services.scan_worker._spawn_background_worker",
            lambda job_id: worker_mod._run_scan_job_worker(job_id),
        )

        client.post("/scan", json=SCAN_BODY)

        db.expire_all()
        stuck = [j.id for j in crud.get_scan_jobs(db) if j.status == crud.RUNNING_STATUS]
        assert stuck == []
