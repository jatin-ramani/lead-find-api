"""Scan jobs, scrape jobs, website data, scrape targets and dashboard stats."""

import json
from datetime import datetime, timezone

import pytest

from database import crud
from database.models import ScrapeJob, WebsiteData


class TestScanJobCrud:
    def test_create_returns_id_with_running_status(self, db):
        job_id = crud.create_scan_job(db, city="Ahmedabad", category="commercial")
        job = crud.get_scan_job(db, job_id)

        assert isinstance(job_id, int)
        assert job.status == crud.RUNNING_STATUS
        assert (job.progress, job.total_businesses, job.new_businesses) == (0, 0, 0)

    def test_update_applies_only_supplied_fields(self, db):
        job_id = crud.create_scan_job(db, city="A", category="c")
        crud.update_scan_job(db, job_id, progress=50)
        job = crud.get_scan_job(db, job_id)

        assert job.progress == 50
        assert job.status == crud.RUNNING_STATUS  # untouched

    def test_update_missing_job_returns_none(self, db):
        assert crud.update_scan_job(db, 9999, progress=1) is None

    def test_list_is_newest_first(self, db):
        ids = [crud.create_scan_job(db, city=f"C{i}", category="c") for i in range(3)]

        assert [j.id for j in crud.get_scan_jobs(db)] == sorted(ids, reverse=True)

    def test_latest(self, db):
        crud.create_scan_job(db, city="first", category="c")
        last = crud.create_scan_job(db, city="last", category="c")

        assert crud.get_latest_scan_job(db).id == last

    def test_latest_on_empty_table(self, db):
        assert crud.get_latest_scan_job(db) is None

    def test_delete(self, db):
        job_id = crud.create_scan_job(db, city="A", category="c")

        assert crud.delete_scan_job(db, job_id) is True
        assert crud.delete_scan_job(db, job_id) is False


class TestScrapeJobCrud:
    def test_create_defaults(self, db):
        job_id = crud.create_scrape_job(db, total_websites=10)
        job = crud.get_scrape_job(db, job_id)

        assert job.status == crud.PENDING_STATUS
        assert job.total_websites == 10
        assert (job.progress, job.completed, job.success, job.failed) == (0, 0, 0, 0)

    def test_update_only_touches_non_none_fields(self, db):
        job_id = crud.create_scrape_job(db, total_websites=5)
        crud.update_scrape_job(db, job_id, status=crud.RUNNING_STATUS)

        before = crud.get_scrape_job(db, job_id)
        snapshot = (before.progress, before.completed, before.success, before.failed)

        crud.update_scrape_job(db, job_id)  # everything None

        after = crud.get_scrape_job(db, job_id)

        assert (after.progress, after.completed, after.success, after.failed) == snapshot
        assert after.status == crud.RUNNING_STATUS

    def test_explicit_zeros_are_applied(self, db):
        """`is not None` rather than truthiness, so 0 must still be written."""

        job_id = crud.create_scrape_job(db, total_websites=5)
        crud.update_scrape_job(db, job_id, progress=50, completed=5, success=5)
        crud.update_scrape_job(db, job_id, progress=0, completed=0, success=0)

        job = crud.get_scrape_job(db, job_id)

        assert (job.progress, job.completed, job.success) == (0, 0, 0)

    def test_running_job_lookup(self, db):
        assert crud.get_running_scrape_job(db) is None

        job_id = crud.create_scrape_job(db, total_websites=1)
        crud.update_scrape_job(db, job_id, status=crud.RUNNING_STATUS)

        assert crud.get_running_scrape_job(db).id == job_id

        crud.update_scrape_job(db, job_id, status=crud.COMPLETED_STATUS)

        assert crud.get_running_scrape_job(db) is None

    def test_clear_current_business(self, db, business_factory):
        """update_scrape_job cannot unset this column; the helper exists for it."""

        business = business_factory()
        job_id = crud.create_scrape_job(db, total_websites=1)
        crud.update_scrape_job(db, job_id, current_business_id=business.id)

        assert crud.get_scrape_job(db, job_id).current_business_id == business.id

        crud.clear_scrape_job_current_business(db, job_id)

        assert crud.get_scrape_job(db, job_id).current_business_id is None

    def test_clear_on_missing_job_returns_none(self, db):
        assert crud.clear_scrape_job_current_business(db, 9999) is None

    def test_timestamps_round_trip(self, db):
        job_id = crud.create_scrape_job(db, total_websites=1)
        finished = datetime.now(timezone.utc)

        crud.update_scrape_job(db, job_id, completed_at=finished)

        assert crud.get_scrape_job(db, job_id).completed_at is not None

    def test_list_newest_first_and_delete(self, db):
        ids = []
        for i in range(3):
            job_id = crud.create_scrape_job(db, total_websites=i)
            crud.update_scrape_job(db, job_id, status=crud.COMPLETED_STATUS)
            ids.append(job_id)

        assert [j.id for j in crud.get_scrape_jobs(db)] == sorted(ids, reverse=True)
        assert crud.delete_scrape_job(db, ids[0]) is True
        assert crud.delete_scrape_job(db, ids[0]) is False


class TestWebsiteData:
    def test_insert_serialises_emails_as_json(self, db, business_factory):
        business = business_factory()

        record = crud.save_website_data(
            db, business_id=business.id, title="T",
            emails=["a@x.test", "b@x.test"],
        )

        assert isinstance(record.emails, str)
        assert json.loads(record.emails) == ["a@x.test", "b@x.test"]
        assert record.status == crud.COMPLETED_STATUS

    def test_upsert_updates_rather_than_inserting(self, db, business_factory):
        business = business_factory()

        first = crud.save_website_data(db, business_id=business.id, title="One")
        second = crud.save_website_data(db, business_id=business.id, title="Two")

        assert first.id == second.id
        assert db.query(WebsiteData).count() == 1
        assert second.title == "Two"

    def test_scraped_at_advances_on_update(self, db, business_factory):
        business = business_factory()

        crud.save_website_data(db, business_id=business.id, title="One")
        first_seen = crud.get_website_data(db, business.id).scraped_at

        crud.save_website_data(db, business_id=business.id, title="Two")
        second_seen = crud.get_website_data(db, business.id).scraped_at

        assert second_seen >= first_seen

    def test_failure_preserves_previously_scraped_content(self, db, business_factory):
        """A site being down today must not wipe last week's good data."""

        business = business_factory()
        crud.save_website_data(
            db, business_id=business.id, title="Good Title",
            emails=["keep@me.test"], facebook="https://fb.test/x",
        )

        crud.save_website_data(
            db, business_id=business.id,
            status=crud.FAILED_STATUS, update_content=False,
        )

        record = crud.get_website_data(db, business.id)

        assert record.status == crud.FAILED_STATUS
        assert record.title == "Good Title"
        assert json.loads(record.emails) == ["keep@me.test"]
        assert record.facebook == "https://fb.test/x"

    def test_get_website_data_returns_latest_row(self, db, business_factory):
        business = business_factory()
        db.add(WebsiteData(business_id=business.id, title="older", status="Failed"))
        db.commit()
        db.add(WebsiteData(business_id=business.id, title="newer", status="Completed"))
        db.commit()

        assert crud.get_website_data(db, business.id).title == "newer"

    def test_missing_returns_none(self, db, business_factory):
        assert crud.get_website_data(db, business_factory().id) is None


class TestScrapeTargets:
    def test_only_businesses_with_a_real_website(self, db, sample_businesses):
        targets = crud.get_scrape_targets(db)
        ids = [i for i, _ in targets]

        # ids 3, 6, 9 have no website; 5 is "" and 8 is "   "
        assert set(ids).isdisjoint({3, 5, 6, 8, 9})
        assert all(url.strip() for _, url in targets)

    def test_count_matches_the_list(self, db, sample_businesses):
        assert crud.count_scrape_targets(db) == len(crud.get_scrape_targets(db))

    def test_missing_excludes_already_scraped(self, db, sample_businesses):
        all_ids = [i for i, _ in crud.get_scrape_targets(db)]
        crud.save_website_data(db, business_id=all_ids[0], title="done")

        missing = [i for i, _ in crud.get_scrape_targets(db, only_missing=True)]

        assert all_ids[0] not in missing
        assert set(missing) == set(all_ids[1:])

    def test_selected_filters_dedupes_and_ignores_unknown(self, db, sample_businesses):
        selected = crud.get_selected_scrape_targets(db, [1, 1, 2, 3, 9999])
        ids = [i for i, _ in selected]

        assert ids == [1, 2]  # 3 has no website, 9999 does not exist

    def test_selected_with_empty_input(self, db, sample_businesses):
        assert crud.get_selected_scrape_targets(db, []) == []

    def test_failed_targets_use_the_latest_status(self, db, sample_businesses):
        ids = [i for i, _ in crud.get_scrape_targets(db)]
        a, b, c = ids[0], ids[1], ids[2]

        crud.save_website_data(db, business_id=a, status=crud.FAILED_STATUS)
        crud.save_website_data(db, business_id=b, status=crud.COMPLETED_STATUS)
        # failed, then later succeeded -> must NOT be retried
        db.add(WebsiteData(business_id=c, status=crud.FAILED_STATUS))
        db.commit()
        db.add(WebsiteData(business_id=c, status=crud.COMPLETED_STATUS))
        db.commit()

        failed = [i for i, _ in crud.get_failed_scrape_targets(db)]

        assert failed == [a]

    def test_no_failures_returns_empty(self, db, sample_businesses):
        assert crud.get_failed_scrape_targets(db) == []


class TestDashboardStats:
    def test_empty_database(self, db):
        stats = crud.get_dashboard_stats(db)

        assert stats["business"]["totalBusinesses"] == 0
        assert stats["websiteData"]["totalScraped"] == 0
        assert stats["scrapeJobs"]["total"] == 0
        assert stats["scanJobs"]["total"] == 0
        assert stats["latestScanJob"] is None
        assert stats["latestScrapeJob"] is None

    def test_business_counts_use_the_blank_aware_rule(self, db, sample_businesses):
        business = crud.get_dashboard_stats(db)["business"]

        # 10 total; 3/6/9 null, 5 blank, 8 whitespace -> 5 have a real website
        assert business["totalBusinesses"] == 10
        assert business["withWebsite"] == 5
        assert business["withoutWebsite"] == 5

    def test_with_and_without_always_sum_to_total(self, db, sample_businesses):
        business = crud.get_dashboard_stats(db)["business"]

        assert business["withWebsite"] + business["withoutWebsite"] == business["totalBusinesses"]
        assert business["withEmail"] + business["withoutEmail"] == business["totalBusinesses"]

    def test_website_groups_reconcile_with_the_business_group(self, db, sample_businesses):
        """completed + failed + pending must equal withWebsite."""

        targets = [i for i, _ in crud.get_scrape_targets(db)]
        crud.save_website_data(db, business_id=targets[0], status=crud.COMPLETED_STATUS)
        crud.save_website_data(db, business_id=targets[1], status=crud.FAILED_STATUS)

        stats = crud.get_dashboard_stats(db)
        web = stats["websiteData"]

        assert web["completed"] == 1
        assert web["failed"] == 1
        assert web["completed"] + web["failed"] + web["pending"] == stats["business"]["withWebsite"]

    def test_pending_matches_the_missing_target_count(self, db, sample_businesses):
        stats = crud.get_dashboard_stats(db)

        assert stats["websiteData"]["pending"] == crud.count_scrape_targets(
            db, only_missing=True
        )

    def test_job_counts_by_status(self, db):
        for status in (crud.COMPLETED_STATUS, crud.COMPLETED_STATUS,
                       crud.FAILED_STATUS, crud.RUNNING_STATUS):
            job_id = crud.create_scrape_job(db, total_websites=1)
            crud.update_scrape_job(db, job_id, status=status)

        jobs = crud.get_dashboard_stats(db)["scrapeJobs"]

        assert jobs == {"total": 4, "running": 1, "completed": 2, "failed": 1}

    def test_latest_jobs_are_summarised(self, db):
        crud.create_scan_job(db, city="Ahmedabad", category="commercial")
        crud.create_scrape_job(db, total_websites=7)

        stats = crud.get_dashboard_stats(db)

        assert stats["latestScanJob"]["city"] == "Ahmedabad"
        assert stats["latestScrapeJob"]["total_websites"] == 7


class TestDatabaseHealth:
    def test_healthy_connection(self, db):
        assert crud.check_database_connection(db) is True

    def test_unreachable_database_returns_false_without_raising(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        bad = create_engine("sqlite:////nonexistent-dir/nope.db")
        session = sessionmaker(bind=bad)()

        try:
            assert crud.check_database_connection(session) is False
        finally:
            session.close()
            bad.dispose()


def test_deleting_current_business_sets_scrape_pointer_null(db, business_factory):
    business = business_factory(name="Current scrape target", place_id="fk-set-null")
    job = ScrapeJob(status="Running", current_business_id=business.id)
    db.add(job)
    db.commit()
    job_id = job.id

    assert crud.delete_business(db, business.id) is True
    db.expire_all()
    surviving = db.get(ScrapeJob, job_id)
    assert surviving is not None
    assert surviving.current_business_id is None
