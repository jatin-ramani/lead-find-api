"""
Unit and Integration Tests for POST /integrations/gmail/test-send API Endpoint.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from config import settings
from database.models import (
    Business,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailTemplate,
    GmailOAuthCredential,
)
from security.crypto import encrypt_token


def test_gmail_test_send_unauthorized(unauth_client: TestClient):
    resp = unauth_client.post(
        "/integrations/gmail/test-send",
        json={"recipient_email": "test@example.com", "template_grades": ["A"]},
    )
    assert resp.status_code == 401


def test_gmail_test_send_invalid_recipient_email(client: TestClient):
    resp = client.post(
        "/integrations/gmail/test-send",
        json={"recipient_email": "invalid-email-string", "template_grades": ["A"]},
    )
    assert resp.status_code == 400
    assert "Invalid test recipient email" in resp.json()["message"]


def test_gmail_test_send_empty_template_grades(client: TestClient):
    resp = client.post(
        "/integrations/gmail/test-send",
        json={"recipient_email": "test@example.com", "template_grades": []},
    )
    assert resp.status_code == 400
    assert "At least one template grade must be selected" in resp.json()["message"]


def test_gmail_test_send_disconnected_when_provider_is_gmail(client: TestClient, monkeypatch, db: Session):
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "gmail")
    resp = client.post(
        "/integrations/gmail/test-send",
        json={"recipient_email": "test@example.com", "template_grades": ["A"]},
    )
    assert resp.status_code == 400
    assert "Gmail account is not connected" in resp.json()["message"]


def test_gmail_test_send_success_mock_provider(client: TestClient, db: Session):
    initial_biz_count = db.query(Business).count()
    initial_camp_count = db.query(EmailCampaign).count()
    initial_recip_count = db.query(EmailCampaignRecipient).count()

    resp = client.post(
        "/integrations/gmail/test-send",
        json={
            "recipient_email": "tester@example.com",
            "template_grades": ["Universal"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["recipient_email"] == "tester@example.com"
    assert data["total"] == 1
    assert data["sent"] == 1
    assert data["failed"] == 0
    assert data["skipped"] == 0

    assert len(data["results"]) == 1
    item = data["results"][0]
    assert item["status"] == "sent"
    assert item["message_id"] is not None
    assert "[TEST]" in item["subject"]
    assert "A free website mockup for Test Business?" in item["subject"]
    assert item["error"] is None

    # Verify no CRM database leads or campaigns were created
    assert db.query(Business).count() == initial_biz_count
    assert db.query(EmailCampaign).count() == initial_camp_count
    assert db.query(EmailCampaignRecipient).count() == initial_recip_count


def test_gmail_test_send_resolves_universal_master_template(client: TestClient, db: Session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stored_tpl = EmailTemplate(
        name="Universal Master Cold Email Template",
        description="Master cold outreach template for all qualified leads",
        subject="A free website mockup for {{business_name}}?",
        body="<p>Hi {{business_name}} team,</p><p>We're <strong>Codebait</strong>...</p><p>Best,<br><strong>Jatin Ramani</strong></p>",
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(stored_tpl)
    db.commit()

    resp = client.post(
        "/integrations/gmail/test-send",
        json={
            "recipient_email": "tester@example.com",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] == 1
    result_item = data["results"][0]
    assert result_item["grade"] == "Universal"
    assert "[TEST] A free website mockup for Test Business?" == result_item["subject"]


def test_gmail_test_send_with_real_gmail_provider(client: TestClient, db: Session, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "gmail")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "test-client-secret")

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cred = GmailOAuthCredential(
        email_address="verified.tester@gmail.com",
        encrypted_access_token=encrypt_token("access_tok"),
        encrypted_refresh_token=encrypt_token("refresh_tok"),
        token_expiry=now_utc + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=10,
        daily_send_reset_date=today_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "gmail_msg_test_999"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        resp = client.post(
            "/integrations/gmail/test-send",
            json={
                "recipient_email": "inbox@partner.com",
                "template_grades": ["A", "B"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] == 2
        assert data["failed"] == 0
        assert mock_post.call_count == 2

        db.refresh(cred)
        assert cred.daily_send_count == 12


def test_gmail_test_send_quota_exhaustion_stops_remainder(client: TestClient, db: Session, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "gmail")
    monkeypatch.setattr(settings, "EMAIL_DAILY_QUOTA_LIMIT", 400)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Already at 399 sends (only 1 remaining quota slot)
    cred = GmailOAuthCredential(
        email_address="verified.tester@gmail.com",
        encrypted_access_token=encrypt_token("access_tok"),
        encrypted_refresh_token=encrypt_token("refresh_tok"),
        token_expiry=now_utc + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=399,
        daily_send_reset_date=today_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "gmail_msg_400"}

    with patch("requests.post", return_value=mock_resp):
        # Request 3 templates (Grade A, B, C)
        resp = client.post(
            "/integrations/gmail/test-send",
            json={
                "recipient_email": "inbox@partner.com",
                "template_grades": ["A", "B", "C"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["sent"] == 1
        assert data["skipped"] == 2

        # Grade A should be sent
        assert data["results"][0]["grade"] == "A"
        assert data["results"][0]["status"] == "sent"

        # Grades B and C should be skipped due to daily quota
        assert data["results"][1]["grade"] == "B"
        assert data["results"][1]["status"] == "skipped"
        assert "Daily sending limit reached" in data["results"][1]["error"]

        assert data["results"][2]["grade"] == "C"
        assert data["results"][2]["status"] == "skipped"
        assert "Daily sending limit reached" in data["results"][2]["error"]

        db.refresh(cred)
        assert cred.daily_send_count == 400
