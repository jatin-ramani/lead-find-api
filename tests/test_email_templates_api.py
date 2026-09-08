"""
Unit and API integration tests for Phase 5 Email Templates.

Covers:
- Template CRUD operations (Create, Read, Update, Delete)
- Validation rules (length, whitespace, empty values)
- Unicode, RTL, Emoji, and multiline support
- HTML entity escaping and injection resistance
- Safe deletion vs archiving rules (prevents corrupting referencing campaigns)
- Template preview rendering with allowlisted variables and sample/real data
- Admin authentication enforcement
"""

import pytest
from fastapi.testclient import TestClient

from database.models import Business, EmailCampaign, EmailTemplate


def test_create_template_success(client: TestClient):
    payload = {
        "name": "Initial Outreach Template",
        "description": "Standard intro for local dental practices",
        "subject": "Quick question for {{business_name}}",
        "body": "Hi {{contact_name}},\n\nWe saw {{business_name}} has no website listed and would love to help!\n\nBest,\nLead Finder",
    }
    resp = client.post("/templates", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["name"] == "Initial Outreach Template"
    assert data["data"]["subject"] == "Quick question for {{business_name}}"
    assert data["data"]["is_archived"] is False
    assert "id" in data["data"]


def test_create_template_validation_errors(client: TestClient):
    # Empty name
    resp = client.post(
        "/templates",
        json={"name": "   ", "subject": "Subject", "body": "Body"},
    )
    assert resp.status_code == 422

    # Empty subject
    resp = client.post(
        "/templates",
        json={"name": "Test", "subject": "   ", "body": "Body"},
    )
    assert resp.status_code == 422

    # Empty body
    resp = client.post(
        "/templates",
        json={"name": "Test", "subject": "Subject", "body": "  "},
    )
    assert resp.status_code == 422


def test_get_and_list_templates(client: TestClient):
    # Create 2 templates
    client.post(
        "/templates",
        json={"name": "Alpha Template", "subject": "Alpha Subject", "body": "Alpha Body"},
    )
    client.post(
        "/templates",
        json={"name": "Beta Template", "subject": "Beta Subject", "body": "Beta Body"},
    )

    # List all
    resp = client.get("/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["total"] >= 2
    assert len(data["items"]) >= 2

    # Search filter
    search_resp = client.get("/templates?search=Alpha")
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert any(item["name"] == "Alpha Template" for item in search_data["items"])
    assert not any(item["name"] == "Beta Template" for item in search_data["items"])


def test_update_and_archive_template(client: TestClient):
    # Create
    create_resp = client.post(
        "/templates",
        json={"name": "Update Me", "subject": "Original Subj", "body": "Original Body"},
    )
    tmpl_id = create_resp.json()["data"]["id"]

    # Update
    update_resp = client.patch(
        f"/templates/{tmpl_id}",
        json={"name": "Updated Name", "subject": "New Subj {{business_name}}"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["name"] == "Updated Name"
    assert update_resp.json()["data"]["subject"] == "New Subj {{business_name}}"

    # Toggle Archive
    archive_resp = client.post(
        f"/templates/{tmpl_id}/archive",
        json={"is_archived": True},
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["data"]["is_archived"] is True

    # List archived only
    list_archived = client.get("/templates?is_archived=true")
    assert list_archived.status_code == 200
    assert any(item["id"] == tmpl_id for item in list_archived.json()["items"])


def test_delete_template_success_and_conflict_protection(client: TestClient, db):
    # 1. Create template with no campaign references -> delete succeeds
    create_resp = client.post(
        "/templates",
        json={"name": "Orphan Template", "subject": "Subject", "body": "Body"},
    )
    tmpl_id = create_resp.json()["data"]["id"]

    del_resp = client.delete(f"/templates/{tmpl_id}")
    assert del_resp.status_code == 204

    # Verify 404 on subsequent get
    get_resp = client.get(f"/templates/{tmpl_id}")
    assert get_resp.status_code == 404

    # 2. Create template and reference it in a campaign -> delete should return 409 Conflict
    tmpl_resp = client.post(
        "/templates",
        json={"name": "Referenced Template", "subject": "Subject", "body": "Body"},
    )
    ref_tmpl_id = tmpl_resp.json()["data"]["id"]

    # Create campaign referencing this template
    client.post(
        "/campaigns",
        json={
            "name": "Campaign Using Template",
            "template_id": ref_tmpl_id,
        },
    )

    # Deleting referenced template must fail with 409
    del_conflict_resp = client.delete(f"/templates/{ref_tmpl_id}")
    assert del_conflict_resp.status_code == 409
    assert "Cannot delete template because it is referenced" in del_conflict_resp.json()["message"]


def test_preview_template_rendering(client: TestClient, db):
    # Create a real business in DB
    biz = Business(
        name="Sunnyvale Veterinary Clinic",
        phone="+1 408 555 1212",
        email="info@sunnyvalevet.example",
        website="https://sunnyvalevet.example",
        city="Sunnyvale",
        category="Veterinarian",
        lead_score=92,
        lead_grade="A",
        lead_status="interested",
    )
    db.add(biz)
    db.commit()
    db.refresh(biz)

    # Preview with business_id
    preview_payload = {
        "subject": "Proposal for {{business_name}}",
        "body": "Dear {{contact_name}},\n\nWe love what {{business_name}} is doing in {{lead_status}} stage! Score: {{lead_score}}",
        "business_id": biz.id,
    }
    resp = client.post("/templates/preview", json=preview_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["rendered_subject"] == "Proposal for Sunnyvale Veterinary Clinic"
    assert "Sunnyvale Veterinary Clinic" in data["rendered_body"]
    assert "interested" in data["rendered_body"]
    assert "92" in data["rendered_body"]


def test_template_security_unicode_and_html_injection(client: TestClient):
    # Unicode, emoji, RTL payload
    payload = {
        "name": "Unicode Test 🚀 مرحبا",
        "subject": "Special offer 🌟 {{business_name}}",
        "body": "مرحبا بك يا {{contact_name}}!\n\n<script>alert('xss')</script> <b>{{business_name}}</b>",
    }
    resp = client.post("/templates", json=payload)
    assert resp.status_code == 201

    # Preview rendering checks HTML escaping
    preview_resp = client.post(
        "/templates/preview",
        json={
            "subject": payload["subject"],
            "body": payload["body"],
            "custom_context": {
                "business_name": "<img src=x onerror=alert(1)>",
                "contact_name": "Dr. Smith & Co",
            },
        },
    )
    assert preview_resp.status_code == 200
    rendered_body = preview_resp.json()["rendered_body"]
    # Variable replacement must be HTML escaped
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered_body
    assert "Dr. Smith &amp; Co" in rendered_body


def test_template_variables_endpoint(client: TestClient):
    resp = client.get("/templates/variables")
    assert resp.status_code == 200
    variables = resp.json()
    assert isinstance(variables, list)
    keys = {v["key"] for v in variables}
    assert "business_name" in keys
    assert "email" in keys
    assert "lead_status" in keys
    assert "lead_score" in keys


def test_unauthenticated_requests_blocked(unauth_client: TestClient):
    # All /templates endpoints require verify_admin
    assert unauth_client.get("/templates").status_code == 401
    assert unauth_client.post("/templates", json={"name": "A", "subject": "B", "body": "C"}).status_code == 401
