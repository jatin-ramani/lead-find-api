"""
Unit, Integration, Security, and Regression Tests for Business Notes System.
"""
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from database.models import Business, BusinessNote
from services.lead_scoring import calculate_lead_score
from services.note_service import (
    create_business_note,
    delete_business_note,
    get_business_notes_count,
    get_notes_for_business,
    update_business_note,
)
from services.tag_service import attach_tag_to_business, create_tag


def test_note_crud_service(db: Session):
    b = Business(name="Dental Clinic Care", city="Ahmedabad")
    db.add(b)
    db.commit()

    # 1. Create note
    note = create_business_note(db, b.id, "Follow up next Monday.")
    assert note is not None
    assert note.business_id == b.id
    assert note.content == "Follow up next Monday."
    assert note.id is not None
    assert get_business_notes_count(db, b.id) == 1

    # 2. List notes ordered newest first
    note2 = create_business_note(db, b.id, "Second note added.")
    notes = get_notes_for_business(db, b.id)
    assert len(notes) == 2
    assert notes[0].id == note2.id
    assert notes[1].id == note.id

    # 3. Update note
    updated = update_business_note(db, note.id, "Follow up next Tuesday.")
    assert updated is not None
    assert updated.content == "Follow up next Tuesday."

    # 4. Delete note
    deleted = delete_business_note(db, note.id)
    assert deleted is True
    assert get_business_notes_count(db, b.id) == 1

    # 5. Non-existent note operations
    assert update_business_note(db, 999999, "abc") is None
    assert delete_business_note(db, 999999) is False
    assert create_business_note(db, 999999, "abc") is None


def test_notes_api_endpoints(client: TestClient, db: Session):
    b = Business(name="Alpha Corp", city="Surat")
    db.add(b)
    db.commit()

    # 1. Create note (POST)
    res_create = client.post(
        f"/businesses/{b.id}/notes",
        json={"content": "Called the owner. Interested in website redesign."},
    )
    assert res_create.status_code == 201
    created_data = res_create.json()
    assert created_data["content"] == "Called the owner. Interested in website redesign."
    assert created_data["business_id"] == b.id
    note_id = created_data["id"]

    # 2. List notes (GET)
    res_list = client.get(f"/businesses/{b.id}/notes")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert list_data["success"] is True
    assert list_data["total"] == 1
    assert list_data["data"][0]["id"] == note_id

    # 3. Edit note (PATCH)
    res_patch = client.patch(
        f"/businesses/notes/{note_id}",
        json={"content": "Owner said they already have an agency. Follow up in 3 months."},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["content"] == "Owner said they already have an agency. Follow up in 3 months."

    # 4. Delete note (DELETE)
    res_del = client.delete(f"/businesses/notes/{note_id}")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] == 1

    # Verify empty list now
    res_list_empty = client.get(f"/businesses/{b.id}/notes")
    assert res_list_empty.json()["total"] == 0

    # 5. 404 handling
    assert client.get("/businesses/999999/notes").status_code == 200  # empty list
    assert client.post("/businesses/999999/notes", json={"content": "test"}).status_code == 404
    assert client.patch("/businesses/notes/999999", json={"content": "test"}).status_code == 404
    assert client.delete("/businesses/notes/999999").status_code == 404


def test_note_content_validation_and_xss_safety(client: TestClient, db: Session):
    b = Business(name="Beta Enterprise")
    db.add(b)
    db.commit()

    # 1. Empty content rejected (422)
    res_empty = client.post(f"/businesses/{b.id}/notes", json={"content": ""})
    assert res_empty.status_code == 422

    # 2. Whitespace-only rejected (422)
    res_ws = client.post(f"/businesses/{b.id}/notes", json={"content": "   \n\t  "})
    assert res_ws.status_code == 422

    # 3. Minimum length (1 char allowed)
    res_1 = client.post(f"/businesses/{b.id}/notes", json={"content": "X"})
    assert res_1.status_code == 201
    assert res_1.json()["content"] == "X"

    # 4. Maximum length limit (5000 characters allowed, 5001 rejected)
    valid_long = "A" * 5000
    res_5000 = client.post(f"/businesses/{b.id}/notes", json={"content": valid_long})
    assert res_5000.status_code == 201

    too_long = "A" * 5001
    res_5001 = client.post(f"/businesses/{b.id}/notes", json={"content": too_long})
    assert res_5001.status_code == 422

    # 5. Multilingual & Unicode support (Hindi, Gujarati, Arabic, emojis)
    unicode_note = "વેબસાઇટ રીડીઝાઇન માટે વાત થઇ. बात अच्छी रही! مرحبا بكم 👍 🚀"
    res_unicode = client.post(f"/businesses/{b.id}/notes", json={"content": unicode_note})
    assert res_unicode.status_code == 201
    assert res_unicode.json()["content"] == unicode_note

    # 6. Multiline text preserved
    multiline = "Line 1: Met client\n\nLine 2: Budget is ₹50,000\nLine 3: Send proposal"
    res_multi = client.post(f"/businesses/{b.id}/notes", json={"content": multiline})
    assert res_multi.status_code == 201
    assert res_multi.json()["content"] == multiline

    # 7. XSS payloads stored safely as plain text strings
    xss_payload = "<script>alert('xss')</script><img src=x onerror=alert(1)><b>Bold</b>javascript:alert(1)"
    res_xss = client.post(f"/businesses/{b.id}/notes", json={"content": xss_payload})
    assert res_xss.status_code == 201
    assert res_xss.json()["content"] == xss_payload

    # 8. SQL-like injection strings stored safely as plain text
    sql_payload = "'; DROP TABLE business_notes; SELECT * FROM users WHERE '1'='1"
    res_sql = client.post(f"/businesses/{b.id}/notes", json={"content": sql_payload})
    assert res_sql.status_code == 201
    assert res_sql.json()["content"] == sql_payload


def test_note_cascade_deletion(client: TestClient, db: Session):
    b = Business(name="To Be Deleted Biz")
    db.add(b)
    db.commit()

    create_business_note(db, b.id, "Note 1")
    create_business_note(db, b.id, "Note 2")
    create_business_note(db, b.id, "Note 3")
    assert get_business_notes_count(db, b.id) == 3

    # Delete business
    res_del_biz = client.delete(f"/businesses/{b.id}")
    assert res_del_biz.status_code == 200

    # Notes should be deleted via cascade with 0 orphans
    remaining_notes = db.query(BusinessNote).filter(BusinessNote.business_id == b.id).all()
    assert len(remaining_notes) == 0


def test_note_operations_preserve_lead_score_tags_favorites(client: TestClient, db: Session):
    tag = create_tag(db, "High Potential")
    b = Business(
        name="Invariant Tech",
        website=None,
        phone="+91 98989 89898",
        email="contact@invariant.test",
        is_favorite=True,
    )
    score_res = calculate_lead_score(b)
    b.lead_score = score_res.score
    b.lead_grade = score_res.grade
    b.lead_score_reasons = score_res.reasons
    db.add(b)
    db.commit()

    attach_tag_to_business(db, b.id, tag_id=tag.id)

    initial_score = b.lead_score
    initial_grade = b.lead_grade
    initial_reasons = list(b.lead_score_reasons)
    initial_fav = b.is_favorite

    # Add Note
    res_n = client.post(f"/businesses/{b.id}/notes", json={"content": "Important discussion held."})
    assert res_n.status_code == 201
    note_id = res_n.json()["id"]

    db.refresh(b)
    assert b.lead_score == initial_score
    assert b.lead_grade == initial_grade
    assert b.lead_score_reasons == initial_reasons
    assert b.is_favorite == initial_fav
    assert len(b.tags) == 1

    # Edit Note
    client.patch(f"/businesses/notes/{note_id}", json={"content": "Updated discussion summary."})
    db.refresh(b)
    assert b.lead_score == initial_score
    assert b.lead_grade == initial_grade
    assert b.lead_score_reasons == initial_reasons
    assert b.is_favorite == initial_fav
    assert len(b.tags) == 1

    # Delete Note
    client.delete(f"/businesses/notes/{note_id}")
    db.refresh(b)
    assert b.lead_score == initial_score
    assert b.lead_grade == initial_grade
    assert b.lead_score_reasons == initial_reasons
    assert b.is_favorite == initial_fav
    assert len(b.tags) == 1


def test_unauthenticated_notes_endpoints_rejected(unauth_client: TestClient):
    assert unauth_client.get("/businesses/1/notes").status_code == 401
    assert unauth_client.post("/businesses/1/notes", json={"content": "Secret note"}).status_code == 401
    assert unauth_client.patch("/businesses/notes/1", json={"content": "Update"}).status_code == 401
    assert unauth_client.delete("/businesses/notes/1").status_code == 401

    # Invalid token
    invalid_headers = {"x-session-token": "invalid_token_9999"}
    assert unauth_client.get("/businesses/1/notes", headers=invalid_headers).status_code == 401
    assert unauth_client.post("/businesses/1/notes", json={"content": "Test"}, headers=invalid_headers).status_code == 401
    assert unauth_client.patch("/businesses/notes/1", json={"content": "Test"}, headers=invalid_headers).status_code == 401
    assert unauth_client.delete("/businesses/notes/1", headers=invalid_headers).status_code == 401


def test_notes_never_leaked_in_public_or_export_apis(client: TestClient, db: Session):
    secret_note_text = "TOP_SECRET_INTERNAL_CRM_NOTE_12345"
    b = Business(name="Private Lead", city="Ahmedabad")
    db.add(b)
    db.commit()

    create_business_note(db, b.id, secret_note_text)

    # 1. GET /businesses list does not contain note content
    res_list = client.get("/businesses")
    assert res_list.status_code == 200
    assert secret_note_text not in res_list.text

    # 2. GET /businesses/cities summary does not contain note content
    res_cities = client.get("/businesses/cities")
    assert res_cities.status_code == 200
    assert secret_note_text not in res_cities.text

    # 3. CSV export (all & filtered) does not contain note content
    res_csv = client.get("/businesses/export/csv")
    assert res_csv.status_code == 200
    assert secret_note_text not in res_csv.text

    res_filt_csv = client.get("/businesses/export/csv?city=Ahmedabad")
    assert res_filt_csv.status_code == 200
    assert secret_note_text not in res_filt_csv.text

    # 5. Selected CSV export does not contain note content
    res_sel_csv = client.post("/businesses/export/csv", json={"business_ids": [b.id]})
    assert res_sel_csv.status_code == 200
    assert secret_note_text not in res_sel_csv.text


def test_notes_index_and_database_schema(db: Session):
    inspector = inspect(db.bind)
    columns = [col["name"] for col in inspector.get_columns("business_notes")]
    assert "id" in columns
    assert "business_id" in columns
    assert "content" in columns
    assert "created_at" in columns
    assert "updated_at" in columns

    indexes = inspector.get_indexes("business_notes")
    index_cols = [idx["column_names"] for idx in indexes]
    assert ["business_id"] in index_cols
