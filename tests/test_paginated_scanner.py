"""
Unit tests for the upgraded paginated scanner architecture.
"""

import pytest
from database import crud
from providers.exceptions import GeoapifyError
from services.scanner import ScanFailed, scan_city
from tests.fakes import geoapify_feature


def test_paginated_scanner_fetches_multiple_pages_and_deduplicates(client, db, monkeypatch):
    """
    Test that a multi-page scan fetches page 1 (500 items) and page 2 (10 items),
    persists all items, deduplicates, and accumulates counts correctly.
    """
    monkeypatch.setattr("services.scanner.geocode_city", lambda city: (23.02, 72.58, "place-123"))

    page1 = [geoapify_feature(name=f"Page1_{i}", place_id=f"p1-{i}") for i in range(500)]
    # Include 2 duplicates from page 1 in page 2
    page2 = [geoapify_feature(name=f"Page1_{i}", place_id=f"p1-{i}") for i in range(2)] + [
        geoapify_feature(name=f"Page2_{i}", place_id=f"p2-{i}") for i in range(8)
    ]

    fetched_offsets = []

    def fake_fetch_places_page(category, place_id, latitude, longitude, limit, offset):
        fetched_offsets.append(offset)
        if offset == 0:
            return page1
        elif offset == 500:
            return page2
        return []

    monkeypatch.setattr("services.scanner.search_businesses", lambda city, category: page1)
    monkeypatch.setattr("services.scanner.fetch_places_page", fake_fetch_places_page)

    job_id = scan_city("Ahmedabad", "catering")
    job = crud.get_scan_job(db, job_id)

    assert job.status == crud.COMPLETED_STATUS
    assert job.progress == 100
    assert job.total_businesses == 510  # 500 from page 1 + 10 total returned on page 2
    assert job.new_businesses == 508  # 500 from page 1 + 8 new from page 2 (2 dupes skipped)


def test_paginated_scanner_stops_at_max_scan_pages(client, db, monkeypatch):
    """
    Test that the scanner stops at GEOAPIFY_MAX_SCAN_PAGES cap.
    """
    monkeypatch.setattr("services.scanner.geocode_city", lambda city: (23.02, 72.58, "place-123"))
    monkeypatch.setattr("config.settings.GEOAPIFY_MAX_SCAN_PAGES", 2)

    full_page = [geoapify_feature(name=f"B_{i}", place_id=f"p-{i}") for i in range(500)]

    def fake_fetch_places_page(category, place_id, latitude, longitude, limit, offset):
        return full_page

    monkeypatch.setattr("services.scanner.search_businesses", lambda city, category: full_page)
    monkeypatch.setattr("services.scanner.fetch_places_page", fake_fetch_places_page)

    job_id = scan_city("Mumbai", "commercial")
    job = crud.get_scan_job(db, job_id)

    assert job.status == crud.COMPLETED_STATUS
    assert job.total_businesses == 1000  # 2 full pages of 500


def test_paginated_scanner_partial_failure_preserves_first_page(client, db, monkeypatch):
    """
    Test that if page 2 raises GeoapifyError, page 1 results remain persisted and job is marked Failed.
    """
    monkeypatch.setattr("services.scanner.geocode_city", lambda city: (23.02, 72.58, "place-123"))

    page1 = [geoapify_feature(name=f"Saved_{i}", place_id=f"saved-{i}") for i in range(500)]

    def fake_fetch_places_page(category, place_id, latitude, longitude, limit, offset):
        if offset == 0:
            return page1
        raise GeoapifyError("Connection reset on page 2")

    monkeypatch.setattr("services.scanner.search_businesses", lambda city, category: page1)
    monkeypatch.setattr("services.scanner.fetch_places_page", fake_fetch_places_page)

    with pytest.raises(ScanFailed) as exc_info:
        scan_city("Delhi", "healthcare")

    assert exc_info.value.upstream is True

    job = crud.get_scan_job(db, exc_info.value.job_id)
    assert job.status == crud.FAILED_STATUS
    assert job.total_businesses == 500
    assert job.new_businesses == 500
