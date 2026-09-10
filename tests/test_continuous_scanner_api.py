"""Tests for continuous scanner API endpoints."""

import pytest


def test_list_category_families_endpoint(client):
    """GET /scan/families returns category families taxonomy."""
    res = client.get("/scan/families")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert len(body["families"]) > 0
    family_ids = [f["id"] for f in body["families"]]
    assert "healthcare" in family_ids
    assert "catering" in family_ids
    assert "commercial" in family_ids


def test_scan_start_and_controls_endpoint(client, monkeypatch):
    """POST /scan starts continuous background scan and supports pause/resume/cancel."""
    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    # Start scan
    res = client.post(
        "/scan",
        json={"city": "Ahmedabad", "category": "healthcare", "radius_km": 15.0},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    job_id = body["job_id"]
    assert job_id > 0
    assert body["total_cells"] > 0
    assert body["total_search_units"] > 0

    # Get status detail
    detail_res = client.get(f"/scan/jobs/{job_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == job_id
    assert detail["city"] == "Ahmedabad"
    assert "recent_leads" in detail

    # Pause scan
    pause_res = client.post(f"/scan/{job_id}/pause")
    assert pause_res.status_code == 200
    assert pause_res.json()["status"] == "Paused"

    # Resume scan
    resume_res = client.post(f"/scan/{job_id}/resume")
    assert resume_res.status_code == 200
    assert resume_res.json()["status"] == "Running"

    # Cancel scan
    cancel_res = client.post(f"/scan/{job_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "Cancelled"


def test_clear_data_endpoint(client, monkeypatch):
    """POST /scan/clear-data clears scanned data when confirmed."""
    # Attempt without confirmation -> 400 or 422
    res_bad = client.post("/scan/clear-data", json={"confirm": False})
    assert res_bad.status_code == 400

    # With confirmation -> 200
    res_ok = client.post("/scan/clear-data", json={"confirm": True})
    assert res_ok.status_code == 200
    assert res_ok.json()["success"] is True
    assert "deleted_count" in res_ok.json()
