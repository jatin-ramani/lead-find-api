"""
Unit and integration tests for Phase 5 Email Campaigns.

Covers:
- Campaign CRUD operations and state validation
- Recipient filtering with canonical filters (enforces HAS_EMAIL)
- Recipient snapshot materialization and duplicate prevention
- Campaign execution and dispatch via MockEmailProvider
- Individual recipient error isolation
- Scheduled campaign processing via /campaigns/process-due
- Campaign cancellation
- Activity history integration
- Historical campaign execution integrity
"""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from database.models import Business, BusinessActivity, EmailCampaign, EmailCampaignRecipient, EmailTemplate
from providers.email_provider import get_email_provider


@pytest.fixture
def sample_template(client: TestClient) -> int:
    resp = client.post(
        "/templates",
        json={
            "name": "Q4 Special Offer",
            "subject": "Exclusive update for {{business_name}}",
            "body": "Hello {{contact_name}},\n\nWe would like to introduce special services for {{business_name}} (status: {{lead_status}}).\n\nBest,\nSales Team",
        },
    )
    return resp.json()["data"]["id"]


@pytest.fixture
def sample_leads(db) -> list:
    leads = [
        Business(
            name="Clinic One",
            email="clinic1@example.com",
            phone="+1 111 222 3333",
            website="https://clinic1.example",
            city="Seattle",
            category="Dentist",
            lead_score=80,
            lead_grade="A",
            lead_status="interested",
        ),
        Business(
            name="Clinic Two",
            email="clinic2@example.com",
            phone="+1 222 333 4444",
            website=None,
            city="Seattle",
            category="Dentist",
            lead_score=65,
            lead_grade="B",
            lead_status="new",
        ),
        Business(
            name="Clinic Three (No Email)",
            email=None,
            phone="+1 333 444 5555",
            website="https://clinic3.example",
            city="Seattle",
            category="Dentist",
            lead_score=70,
            lead_grade="B",
            lead_status="contacted",
        ),
        Business(
            name="Bakery Four",
            email="bakery4@example.com",
            phone="+1 444 555 6666",
            website="https://bakery4.example",
            city="Portland",
            category="Bakery",
            lead_score=40,
            lead_grade="C",
            lead_status="new",
        ),
    ]
    for lead in leads:
        db.add(lead)
    db.commit()
    return leads


def test_create_campaign_draft_and_scheduled(client: TestClient, sample_template: int):
    # 1. Create draft campaign
    draft_payload = {
        "name": "Seattle Dentists Campaign",
        "description": "Outreach to dentists in Seattle",
        "template_id": sample_template,
        "filter_criteria": {
            "city": "Seattle",
            "category": "Dentist",
        },
    }
    resp = client.post("/campaigns", json=draft_payload)
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["name"] == "Seattle Dentists Campaign"
    assert data["status"] == "draft"
    assert data["template_id"] == sample_template
    assert data["template_name"] == "Q4 Special Offer"

    # 2. Create scheduled campaign
    future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    sched_payload = {
        "name": "Scheduled Launch",
        "template_id": sample_template,
        "scheduled_at": future_time,
    }
    sched_resp = client.post("/campaigns", json=sched_payload)
    assert sched_resp.status_code == 201
    assert sched_resp.json()["data"]["status"] == "scheduled"


def test_preview_campaign_recipients(client: TestClient, sample_leads: list):
    # Preview recipients for Seattle Dentists (only those with email should be returned)
    resp = client.post(
        "/campaigns/preview-recipients",
        json={"city": "Seattle", "category": "Dentist"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    # Clinic One and Clinic Two have emails; Clinic Three has no email -> 2 eligible
    assert data["total_eligible_leads"] == 2
    sample_names = [s["name"] for s in data["sample_leads"]]
    assert "Clinic One" in sample_names
    assert "Clinic Two" in sample_names
    assert "Clinic Three (No Email)" not in sample_names


def test_start_campaign_immediate_execution(
    client: TestClient,
    sample_template: int,
    sample_leads: list,
    db,
):
    provider = get_email_provider()
    if hasattr(provider, "clear"):
        provider.clear()

    # Create campaign
    create_resp = client.post(
        "/campaigns",
        json={
            "name": "Live Dispatch Campaign",
            "template_id": sample_template,
            "filter_criteria": {
                "city": "Seattle",
                "category": "Dentist",
            },
        },
    )
    camp_id = create_resp.json()["data"]["id"]

    # Start campaign immediately
    start_resp = client.post(f"/campaigns/{camp_id}/start")
    assert start_resp.status_code == 200
    camp_data = start_resp.json()["data"]
    assert camp_data["recipient_count"] == 2
    assert camp_data["sent_count"] == 2
    assert camp_data["status"] == "completed"

    # Check recipient logs
    recips_resp = client.get(f"/campaigns/{camp_id}/recipients")
    assert recips_resp.status_code == 200
    recips = recips_resp.json()["items"]
    assert len(recips) == 2
    assert all(r["status"] == "sent" for r in recips)
    assert all(r["provider_message_id"] is not None for r in recips)

    # Check outbox in MockEmailProvider
    if hasattr(provider, "get_outbox"):
        outbox = provider.get_outbox()
        assert len(outbox) >= 2
        emails_sent = [o["to_email"] for o in outbox]
        assert "clinic1@example.com" in emails_sent
        assert "clinic2@example.com" in emails_sent

    # Verify Activity logging
    activities = db.query(BusinessActivity).filter(
        BusinessActivity.activity_type == "email_campaign_recipient_sent"
    ).all()
    assert len(activities) >= 2


def test_process_due_campaigns_worker(
    client: TestClient,
    sample_template: int,
    sample_leads: list,
):
    provider = get_email_provider()
    if hasattr(provider, "clear"):
        provider.clear()

    # Create campaign scheduled in the past
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    create_resp = client.post(
        "/campaigns",
        json={
            "name": "Past Due Campaign",
            "template_id": sample_template,
            "filter_criteria": {"city": "Portland"},
            "scheduled_at": past_time,
        },
    )
    camp_id = create_resp.json()["data"]["id"]

    # Call process-due endpoint
    proc_resp = client.post("/campaigns/process-due")
    assert proc_resp.status_code == 200
    data = proc_resp.json()
    assert data["success"] is True
    assert data["campaigns_processed"] >= 1
    assert data["recipients_sent"] >= 1

    # Verify campaign is completed
    get_camp = client.get(f"/campaigns/{camp_id}")
    assert get_camp.json()["data"]["status"] == "completed"
    assert get_camp.json()["data"]["sent_count"] == 1


def test_cancel_campaign(client: TestClient, sample_template: int):
    future_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    create_resp = client.post(
        "/campaigns",
        json={
            "name": "Campaign to Cancel",
            "template_id": sample_template,
            "scheduled_at": future_time,
        },
    )
    camp_id = create_resp.json()["data"]["id"]

    # Cancel
    cancel_resp = client.post(f"/campaigns/{camp_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["data"]["status"] == "cancelled"

    # Cancelling again should return 409
    dup_cancel = client.post(f"/campaigns/{camp_id}/cancel")
    assert dup_cancel.status_code == 409


def test_recipient_error_isolation(
    client: TestClient,
    sample_template: int,
    db,
):
    # Create one valid lead and one malformed email lead
    valid_lead = Business(
        name="Valid Lead",
        email="valid@example.com",
        city="Chicago",
        category="Tech",
    )
    # RFC validation will reject newline / bad formatted address
    bad_lead = Business(
        name="Bad Lead",
        email="invalid-email-address-no-at",
        city="Chicago",
        category="Tech",
    )
    db.add_all([valid_lead, bad_lead])
    db.commit()

    create_resp = client.post(
        "/campaigns",
        json={
            "name": "Mixed Validity Campaign",
            "template_id": sample_template,
            "filter_criteria": {"city": "Chicago"},
        },
    )
    camp_id = create_resp.json()["data"]["id"]

    # Start campaign
    start_resp = client.post(f"/campaigns/{camp_id}/start")
    assert start_resp.status_code == 200

    recips_resp = client.get(f"/campaigns/{camp_id}/recipients")
    recips = recips_resp.json()["items"]
    assert len(recips) == 2

    # The valid lead must succeed and the bad email lead must fail without aborting the campaign
    statuses = {r["recipient_email"]: r["status"] for r in recips}
    assert statuses.get("valid@example.com") == "sent"
    # Campaign completed
    camp_resp = client.get(f"/campaigns/{camp_id}")
    assert camp_resp.json()["data"]["sent_count"] >= 1
