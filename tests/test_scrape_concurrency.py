import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.crud import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    PENDING_STATUS,
    RUNNING_STATUS,
    ScrapeJobConflictError,
    create_scrape_job,
    get_active_scrape_job,
    get_scrape_job,
    update_scrape_job,
)
from database.models import Business, ScrapeJob


class TestScrapeJobConcurrency:
    """Deterministic concurrency and invariant tests for bulk scraping jobs."""

    def test_running_job_blocks_new_jobs(self, client, db):
        """TEST 6: An existing Running job causes a 409 response."""
        job = ScrapeJob(status=RUNNING_STATUS, total_websites=5)
        db.add(job)
        db.commit()

        resp = client.post("/scrape/all")
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "CONFLICT"
        assert body["details"]["job_id"] == job.id

    def test_pending_job_blocks_new_jobs(self, client, db):
        """TEST 7: An existing Pending job causes a 409 response."""
        job = ScrapeJob(status=PENDING_STATUS, total_websites=5)
        db.add(job)
        db.commit()

        resp = client.post("/scrape/missing")
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "CONFLICT"
        assert body["details"]["job_id"] == job.id

    def test_completed_job_allows_new_job(self, client, db):
        """TEST 4 & 8: A Completed job does not block subsequent jobs."""
        job1 = ScrapeJob(status=COMPLETED_STATUS, total_websites=5)
        db.add(job1)
        db.commit()

        # Should succeed
        resp = client.post("/scrape/all")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["job_id"] != job1.id

    def test_failed_job_allows_new_job(self, client, db):
        """TEST 5 & 9: A Failed job does not block subsequent jobs."""
        job1 = ScrapeJob(status=FAILED_STATUS, total_websites=5)
        db.add(job1)
        db.commit()

        # Should succeed
        resp = client.post("/scrape/retry-failed")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_multiple_historical_jobs_coexist(self, db):
        """TEST 8 & 9: Multiple completed and failed jobs coexist in the database."""
        job1 = ScrapeJob(status=COMPLETED_STATUS, total_websites=1)
        job2 = ScrapeJob(status=COMPLETED_STATUS, total_websites=2)
        job3 = ScrapeJob(status=FAILED_STATUS, total_websites=3)
        job4 = ScrapeJob(status=FAILED_STATUS, total_websites=4)
        db.add_all([job1, job2, job3, job4])
        db.commit()

        # Add a new active job without issue
        active_id = create_scrape_job(db, total_websites=10)
        assert active_id is not None
        assert get_active_scrape_job(db).id == active_id

    def test_session_remains_usable_after_integrity_error(self, db):
        """TEST 10: After catching an IntegrityError, db.rollback() keeps session usable."""
        job1 = ScrapeJob(status=PENDING_STATUS, total_websites=5)
        db.add(job1)
        db.commit()

        with pytest.raises(ScrapeJobConflictError) as exc_info:
            create_scrape_job(db, total_websites=10)

        assert exc_info.value.active_job_id == job1.id

        # Verify session is healthy and can execute subsequent queries normally
        active = get_active_scrape_job(db)
        assert active is not None
        assert active.id == job1.id

    def test_unrelated_integrity_error_is_not_converted_to_409(self, db):
        """TEST 11: An unrelated database IntegrityError (e.g. unique place_id) is re-raised."""
        b1 = Business(name="Biz 1", place_id="unique_123")
        b2 = Business(name="Biz 2", place_id="unique_123")
        db.add(b1)
        db.commit()

        db.add(b2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_409_response_envelope_standardization(self, client, db):
        """TEST 12: 409 response adheres strictly to the standardized error envelope."""
        job = ScrapeJob(status=RUNNING_STATUS, total_websites=3)
        db.add(job)
        db.commit()

        resp = client.post("/scrape/all")
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "CONFLICT"
        assert body["message"] == "A scrape job is already running."
        assert body["details"] == {"job_id": job.id}
        assert "requestId" in body
        assert "timestamp" in body

    def test_concurrent_database_job_creation(self):
        """Test concurrent create_scrape_job calls on independent DB sessions."""
        from database.db import SessionLocal

        results = []
        errors = []
        barrier = threading.Barrier(5)

        def worker():
            s = SessionLocal()
            try:
                barrier.wait()
                job_id = create_scrape_job(s, total_websites=10)
                results.append(job_id)
            except ScrapeJobConflictError as exc:
                errors.append(exc)
            finally:
                s.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1
        assert len(errors) == 4

    def test_simultaneous_post_scrape_all(self, client, db, monkeypatch):
        """TEST 1: Simultaneous requests to /scrape/all produce exactly one 200 and one 409."""
        event = threading.Event()
        barrier = threading.Barrier(2)
        results = []

        def slow_scrape(*args, **kwargs):
            event.wait(timeout=2.0)

        monkeypatch.setattr("api.business.scrape_all_websites", slow_scrape)

        def call_scrape_all():
            barrier.wait()
            resp = client.post("/scrape/all")
            results.append((resp.status_code, resp.json()))

        threads = [threading.Thread(target=call_scrape_all) for _ in range(2)]
        for t in threads:
            t.start()

        # Let the first request enter and block
        threading.Event().wait(0.05)
        event.set()

        for t in threads:
            t.join()

        status_codes = [r[0] for r in results]
        assert sorted(status_codes) == [200, 409]

        success_resp = next(r[1] for r in results if r[0] == 200)
        conflict_resp = next(r[1] for r in results if r[0] == 409)

        assert success_resp["success"] is True
        assert conflict_resp["success"] is False
        assert conflict_resp["details"]["job_id"] == success_resp["job_id"]

    def test_simultaneous_post_scrape_missing(self, client, db, monkeypatch):
        """TEST 2: Simultaneous requests to /scrape/missing produce exactly one active job."""
        event = threading.Event()
        barrier = threading.Barrier(2)
        results = []

        def slow_scrape(*args, **kwargs):
            event.wait(timeout=2.0)

        monkeypatch.setattr("api.business.scrape_missing_websites", slow_scrape)

        def call_scrape_missing():
            barrier.wait()
            resp = client.post("/scrape/missing")
            results.append((resp.status_code, resp.json()))

        threads = [threading.Thread(target=call_scrape_missing) for _ in range(2)]
        for t in threads:
            t.start()

        threading.Event().wait(0.05)
        event.set()

        for t in threads:
            t.join()

        status_codes = [r[0] for r in results]
        assert sorted(status_codes) == [200, 409]

    def test_different_bulk_endpoints_simultaneously(self, client, db, monkeypatch):
        """TEST 3: Calling /scrape/all and /scrape/retry-failed simultaneously produces exactly one active job."""
        event = threading.Event()
        barrier = threading.Barrier(2)
        results = []

        def slow_scrape(*args, **kwargs):
            event.wait(timeout=2.0)

        monkeypatch.setattr("api.business.scrape_all_websites", slow_scrape)
        monkeypatch.setattr("api.business.scrape_failed_websites", slow_scrape)

        def call_endpoint(endpoint):
            barrier.wait()
            resp = client.post(endpoint)
            results.append((resp.status_code, resp.json()))

        t1 = threading.Thread(target=call_endpoint, args=("/scrape/all",))
        t2 = threading.Thread(target=call_endpoint, args=("/scrape/retry-failed",))
        t1.start()
        t2.start()

        threading.Event().wait(0.05)
        event.set()

        t1.join()
        t2.join()

        status_codes = [r[0] for r in results]
        assert sorted(status_codes) == [200, 409]
