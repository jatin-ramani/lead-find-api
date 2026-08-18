import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

import database.crud as crud
from database.db import engine
from database.models import Business, ScanJob, ScrapeJob, WebsiteData


class TestDatabasePerformanceAndIndexes:
    """Audit tests for database indexes, constraints, and query execution plans."""

    def test_expected_indexes_exist(self, db):
        """Verify that all required indexes exist on tables."""
        inspector = inspect(db.get_bind())

        # 1. Businesses table indexes
        biz_indexes = {idx["name"]: idx for idx in inspector.get_indexes("businesses")}
        assert "ix_businesses_city" in biz_indexes
        assert biz_indexes["ix_businesses_city"]["column_names"] == ["city"]
        assert bool(biz_indexes["ix_businesses_city"]["unique"]) is False

        assert "ix_businesses_category" in biz_indexes
        assert biz_indexes["ix_businesses_category"]["column_names"] == ["category"]
        assert bool(biz_indexes["ix_businesses_category"]["unique"]) is False

        assert "ix_businesses_id" in biz_indexes

        # 2. WebsiteData table indexes
        web_indexes = {idx["name"]: idx for idx in inspector.get_indexes("website_data")}
        assert "ix_website_data_business_id" in web_indexes
        assert web_indexes["ix_website_data_business_id"]["column_names"] == ["business_id"]

        # 3. ScrapeJobs table indexes
        bind = db.get_bind()
        if bind.dialect.name == "sqlite":
            indexes = [r[1] for r in db.execute(text("PRAGMA index_list('scrape_jobs')")).fetchall()]
            assert "uq_scrape_jobs_single_active" in indexes
        else:
            scrape_indexes = {idx["name"]: idx for idx in inspector.get_indexes("scrape_jobs")}
            assert "uq_scrape_jobs_single_active" in scrape_indexes

    def test_place_id_uniqueness_and_deduplication(self, db):
        """Verify place_id unique constraint and scanner deduplication remain intact."""
        saved_first = crud.save_business(
            db=db,
            name="First Place",
            phone="123",
            email="test1@example.com",
            website="https://first.com",
            city="Ahmedabad",
            category="dental",
            address="Street 1",
            status="Has Website",
            place_id="unique_geo_place_123",
        )
        assert saved_first is True

        # Second save with same place_id should return False (deduplicated)
        saved_duplicate = crud.save_business(
            db=db,
            name="Duplicate Place",
            phone="456",
            email="test2@example.com",
            website="https://second.com",
            city="Ahmedabad",
            category="dental",
            address="Street 2",
            status="Has Website",
            place_id="unique_geo_place_123",
        )
        assert saved_duplicate is False

        # Attempting direct insert bypassing CRUD must raise IntegrityError
        b_raw = Business(name="Raw Duplicate", place_id="unique_geo_place_123")
        db.add(b_raw)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_active_scrape_job_uniqueness_remains_intact(self, db):
        """Verify Phase 6C partial unique index on scrape_jobs is preserved."""
        job1 = ScrapeJob(status=crud.PENDING_STATUS, total_websites=5)
        db.add(job1)
        db.commit()

        # Second active job creation fails
        with pytest.raises(crud.ScrapeJobConflictError):
            crud.create_scrape_job(db, total_websites=10)

    def test_query_plan_uses_city_index(self, db):
        """Verify SQLite query plan searches using ix_businesses_city."""
        bind = db.get_bind()
        if bind.dialect.name == "sqlite":
            res = db.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM businesses WHERE city = 'Ahmedabad' ORDER BY id DESC LIMIT 50")
            ).fetchall()
            plan_str = " ".join(str(row) for row in res)
            assert "ix_businesses_city" in plan_str

    def test_query_plan_uses_category_index(self, db):
        """Verify SQLite query plan searches using ix_businesses_category."""
        bind = db.get_bind()
        if bind.dialect.name == "sqlite":
            res = db.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM businesses WHERE category = 'dentist' ORDER BY id DESC LIMIT 50")
            ).fetchall()
            plan_str = " ".join(str(row) for row in res)
            assert "ix_businesses_category" in plan_str

    def test_query_plan_uses_website_data_business_id_index(self, db):
        """Verify SQLite query plan searches using ix_website_data_business_id."""
        bind = db.get_bind()
        if bind.dialect.name == "sqlite":
            res = db.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM website_data WHERE business_id = 1 ORDER BY id DESC LIMIT 1")
            ).fetchall()
            plan_str = " ".join(str(row) for row in res)
            assert "ix_website_data_business_id" in plan_str

    def test_filtered_business_pagination_correctness(self, db):
        """Verify get_businesses with city and category filters returns correct items & counts."""
        crud.save_business(
            db, name="B1", phone=None, email=None, website="https://b1.com",
            city="Ahmedabad", category="hotel", address=None, status="Has Website", place_id="p1"
        )
        crud.save_business(
            db, name="B2", phone=None, email=None, website="https://b2.com",
            city="Ahmedabad", category="restaurant", address=None, status="Has Website", place_id="p2"
        )
        crud.save_business(
            db, name="B3", phone=None, email=None, website="https://b3.com",
            city="Surat", category="hotel", address=None, status="Has Website", place_id="p3"
        )

        res_city = crud.get_businesses(db, city="Ahmedabad")
        assert res_city["pagination"]["totalItems"] == 2
        assert len(res_city["data"]) == 2

        res_cat = crud.get_businesses(db, category="hotel")
        assert res_cat["pagination"]["totalItems"] == 2
        assert len(res_cat["data"]) == 2

        res_both = crud.get_businesses(db, city="Ahmedabad", category="hotel")
        assert res_both["pagination"]["totalItems"] == 1
        assert res_both["data"][0].name == "B1"
