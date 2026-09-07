"""
Comprehensive QA, Edge-Case, Security, Performance, and Regression Tests
for Phase 1, Feature 5 — Lead Status / CRM Pipeline.
"""
from typing import List
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database.models import Business
from services.lead_scoring import calculate_lead_score
from services.lead_status_service import (
    bulk_update_lead_status,
    get_business_by_id,
    get_lead_status_counts,
    update_business_lead_status,
)
from services.note_service import create_business_note
from services.tag_service import attach_tag_to_business, create_tag


def test_lead_status_defaults_and_service(db: Session):
    b1 = Business(name="Status Test Biz 1", city="Ahmedabad")
    b2 = Business(name="Status Test Biz 2", city="Ahmedabad")
    db.add_all([b1, b2])
    db.commit()

    # 1. Default status is "new"
    assert b1.lead_status == "new"
    assert b2.lead_status == "new"

    # 2. Update single status
    updated = update_business_lead_status(db, b1.id, "contacted")
    assert updated is not None
    assert updated.lead_status == "contacted"

    # 3. Valid status lifecycle
    for st in ["interested", "follow_up", "converted", "lost", "new"]:
        res = update_business_lead_status(db, b1.id, st)
        assert res is not None
        assert res.lead_status == st

    # 4. Invalid status returns None
    assert update_business_lead_status(db, b1.id, "invalid_status") is None
    assert update_business_lead_status(db, 999999, "contacted") is None

    # 5. Bulk status update
    bulk_res = bulk_update_lead_status(db, [b1.id, b2.id, 999999], "interested")
    assert bulk_res["updated_count"] >= 1
    assert bulk_res["total_requested"] == 3
    assert bulk_res["status"] == "interested"

    db.refresh(b1)
    db.refresh(b2)
    assert b1.lead_status == "interested"
    assert b2.lead_status == "interested"

    # 6. Status counts aggregation
    counts = get_lead_status_counts(db, city="Ahmedabad")
    assert counts["interested"] >= 2


def test_lead_status_strict_validation_and_types(client: TestClient, db: Session):
    b = Business(name="Validation Lead", city="Surat")
    db.add(b)
    db.commit()

    # Valid values accept cleanly
    valid_statuses = ["new", "contacted", "interested", "follow_up", "converted", "lost"]
    for st in valid_statuses:
        res = client.patch(f"/businesses/{b.id}/status", json={"status": st})
        assert res.status_code == 200
        assert res.json()["lead_status"] == st

    # Invalid status values must return 422, NEVER 500
    invalid_payloads = [
        {"status": "NEW"},
        {"status": "Contacted"},
        {"status": "abc"},
        {"status": "done"},
        {"status": "maybe"},
        {"status": "closed"},
        {"status": ""},
        {"status": "   "},
        {"status": None},
        {"status": 123},
        {"status": True},
        {"status": ["contacted"]},
        {"status": {"status": "contacted"}},
        {"status": "a" * 1000},
    ]

    for bad in invalid_payloads:
        res_bad = client.patch(f"/businesses/{b.id}/status", json=bad)
        assert res_bad.status_code == 422, f"Failed for payload: {bad}"

    # Nonexistent ID returns 404
    assert client.patch("/businesses/999999/status", json={"status": "contacted"}).status_code == 404


def test_bulk_lead_status_extensive_edge_cases(client: TestClient, db: Session):
    businesses = [Business(name=f"Bulk Lead {i}", city="Rajkot", lead_status="new") for i in range(15)]
    db.add_all(businesses)
    db.commit()

    ids = [b.id for b in businesses]
    target_ids = ids[:10]
    untargeted_ids = ids[10:]

    # 1. Bulk update first 10 leads to "interested" with duplicates and nonexistent IDs
    payload_ids = target_ids + [target_ids[0], target_ids[1], 999991, 999992]
    res = client.post(
        "/businesses/status/bulk",
        json={
            "business_ids": payload_ids,
            "status": "interested",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "interested"
    assert data["total_requested"] == len(set(payload_ids))
    assert data["updated_count"] == 10

    # Verify only targeted businesses changed
    for b in businesses:
        db.refresh(b)
        if b.id in target_ids:
            assert b.lead_status == "interested"
        else:
            assert b.lead_status == "new"

    # 2. Empty list
    res_empty = client.post(
        "/businesses/status/bulk",
        json={"business_ids": [], "status": "lost"},
    )
    assert res_empty.status_code == 200
    assert res_empty.json()["updated_count"] == 0

    # 3. Invalid status values in bulk return 422
    for bad in ["INVALID", "", "done", 123]:
        res_inv = client.post(
            "/businesses/status/bulk",
            json={"business_ids": target_ids, "status": bad},
        )
        assert res_inv.status_code == 422


def test_lead_status_multi_filter_combination(client: TestClient, db: Session):
    tag = create_tag(db, "Hot Lead")
    b1 = Business(
        name="Combo Biz 1",
        city="Vadodara",
        category="Dentist",
        website="https://combo1.test",
        phone="+91 99999 11111",
        email="combo1@test.com",
        is_favorite=True,
        lead_status="contacted",
    )
    b2 = Business(
        name="Combo Biz 2",
        city="Vadodara",
        category="Dentist",
        website=None,
        phone="+91 99999 22222",
        email="combo2@test.com",
        is_favorite=False,
        lead_status="interested",
    )
    b3 = Business(
        name="Combo Biz 3",
        city="Vadodara",
        category="Retail",
        website="https://combo3.test",
        phone=None,
        email=None,
        is_favorite=True,
        lead_status="contacted",
    )
    db.add_all([b1, b2, b3])
    db.commit()

    attach_tag_to_business(db, b1.id, tag_id=tag.id)

    # 1. Filter by lead_status=contacted
    res = client.get("/businesses?city=Vadodara&lead_status=contacted")
    assert res.status_code == 200
    names = [b["name"] for b in res.json()["data"]]
    assert "Combo Biz 1" in names
    assert "Combo Biz 3" in names
    assert "Combo Biz 2" not in names

    # 2. Combined: lead_status=contacted + category=Dentist + is_favorite=true
    res_combo = client.get("/businesses?city=Vadodara&lead_status=contacted&category=Dentist&is_favorite=true")
    assert res_combo.status_code == 200
    combo_data = res_combo.json()["data"]
    assert len(combo_data) == 1
    assert combo_data[0]["name"] == "Combo Biz 1"

    # 3. Combined with tags: lead_status=contacted + tags=Hot Lead
    res_tag = client.get(f"/businesses?city=Vadodara&lead_status=contacted&tags={tag.name}")
    assert res_tag.status_code == 200
    assert len(res_tag.json()["data"]) == 1
    assert res_tag.json()["data"][0]["name"] == "Combo Biz 1"


def test_lead_status_csv_export_and_privacy(client: TestClient, db: Session):
    b = Business(
        name="CSV Pipeline Business",
        city="Surat",
        category="Healthcare",
        website="https://csvpipe.test",
        phone="+91 88888 77777",
        email="lead@csvpipe.test",
        is_favorite=True,
        lead_status="converted",
    )
    db.add(b)
    db.commit()

    create_business_note(db, b.id, "SUPER_SECRET_INTERNAL_NOTE_12345")

    # 1. Export CSV
    res = client.get("/businesses/export/csv?city=Surat&lead_status=converted")
    assert res.status_code == 200
    csv_text = res.text

    # Header check
    lines = csv_text.splitlines()
    assert "Lead Status" in lines[0]

    # Human readable value check
    assert "Converted" in csv_text

    # Privacy check: internal CRM note MUST NEVER leak into CSV export
    assert "SUPER_SECRET_INTERNAL_NOTE_12345" not in csv_text


def test_lead_status_unauthenticated_security(unauth_client: TestClient):
    assert unauth_client.patch("/businesses/1/status", json={"status": "contacted"}).status_code == 401
    assert unauth_client.post("/businesses/status/bulk", json={"business_ids": [1], "status": "contacted"}).status_code == 401

    invalid_headers = {"x-session-token": "bad_token"}
    assert unauth_client.patch("/businesses/1/status", json={"status": "contacted"}, headers=invalid_headers).status_code == 401
    assert unauth_client.post("/businesses/status/bulk", json={"business_ids": [1], "status": "contacted"}, headers=invalid_headers).status_code == 401


def test_lead_status_invariance_full_lifecycle(client: TestClient, db: Session):
    tag = create_tag(db, "Enterprise Account")
    b = Business(
        name="Invariance Deep Test",
        website="https://invariance.test",
        phone="+91 90000 00001",
        email="enterprise@invariance.test",
        is_favorite=True,
        lead_status="new",
    )
    score_res = calculate_lead_score(b)
    b.lead_score = score_res.score
    b.lead_grade = score_res.grade
    b.lead_score_reasons = score_res.reasons
    db.add(b)
    db.commit()

    attach_tag_to_business(db, b.id, tag_id=tag.id)
    note = create_business_note(db, b.id, "Executive meeting scheduled.")

    init_score = b.lead_score
    init_grade = b.lead_grade
    init_reasons = list(b.lead_score_reasons)
    init_fav = b.is_favorite
    init_note_id = note.id
    init_note_content = note.content

    # Full forward & reverse transitions: new -> contacted -> interested -> follow_up -> converted -> lost -> interested -> new
    transitions = ["contacted", "interested", "follow_up", "converted", "lost", "interested", "new"]
    for next_st in transitions:
        res = client.patch(f"/businesses/{b.id}/status", json={"status": next_st})
        assert res.status_code == 200
        assert res.json()["lead_status"] == next_st

        db.refresh(b)
        assert b.lead_score == init_score
        assert b.lead_grade == init_grade
        assert b.lead_score_reasons == init_reasons
        assert b.is_favorite == init_fav
        assert len(b.tags) == 1
        assert b.tags[0].name == "Enterprise Account"
        assert len(b.notes) == 1
        assert b.notes[0].id == init_note_id
        assert b.notes[0].content == init_note_content
