"""
Integration Tests for Gmail OAuth Integration Endpoints.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import app
from config import settings
from database.models import GmailOAuthCredential
from security.crypto import encrypt_token, generate_oauth_state


def test_gmail_status_unauthorized(unauth_client: TestClient):
    resp = unauth_client.get("/integrations/gmail/status")
    assert resp.status_code == 401


def test_gmail_status_disconnected(client: TestClient, db: Session):
    resp = client.get("/integrations/gmail/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["is_connected"] is False
    assert data["email_address"] is None
    assert data["daily_quota_limit"] == 400


def test_gmail_status_connected(client: TestClient, db: Session):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cred = GmailOAuthCredential(
        email_address="alex.outreach@gmail.com",
        encrypted_access_token=encrypt_token("access_token_123"),
        encrypted_refresh_token=encrypt_token("refresh_token_123"),
        token_expiry=now_utc + timedelta(hours=2),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=45,
        daily_send_reset_date=today_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    resp = client.get("/integrations/gmail/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_connected"] is True
    assert data["email_address"] == "alex.outreach@gmail.com"
    assert data["daily_send_count"] == 45
    assert data["daily_quota_remaining"] == 355


def test_gmail_auth_url_generation(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "test-client-secret")

    resp = client.get("/integrations/gmail/auth-url")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "https://accounts.google.com/o/oauth2/v2/auth" in data["auth_url"]
    assert "client_id=test-client-id.apps.googleusercontent.com" in data["auth_url"]
    assert "gmail.send" in data["auth_url"]
    assert "state=" in data["auth_url"]
    assert "access_type=offline" in data["auth_url"]


def test_gmail_callback_csrf_validation_failure(unauth_client: TestClient):
    resp = unauth_client.get("/integrations/gmail/callback?code=mock_code&state=invalid_csrf_state")
    assert resp.status_code == 400
    assert "CSRF validation failed" in resp.json()["message"]


def test_gmail_callback_google_error_param(unauth_client: TestClient):
    resp = unauth_client.get("/integrations/gmail/callback?error=access_denied")
    assert resp.status_code == 400
    assert "Google Authorization Failed" in resp.text


def test_gmail_callback_success(unauth_client: TestClient, db: Session, monkeypatch):
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "test-client-secret")

    state = generate_oauth_state()

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "access_token": "ya29.mock_oauth_access_token",
        "refresh_token": "1//mock_oauth_refresh_token",
        "expires_in": 3600,
    }

    mock_userinfo_resp = MagicMock()
    mock_userinfo_resp.status_code = 200
    mock_userinfo_resp.json.return_value = {
        "email": "verified.user@gmail.com",
    }

    def mock_requests_post(url, *args, **kwargs):
        if "oauth2.googleapis.com/token" in url:
            return mock_token_resp
        return MagicMock(status_code=404)

    def mock_requests_get(url, *args, **kwargs):
        if "googleapis.com/oauth2/v2/userinfo" in url:
            return mock_userinfo_resp
        return MagicMock(status_code=404)

    with patch("requests.post", side_effect=mock_requests_post), patch("requests.get", side_effect=mock_requests_get):
        resp = unauth_client.get(f"/integrations/gmail/callback?code=mock_auth_code_123&state={state}")
        assert resp.status_code == 200
        assert "Gmail Connected Successfully" in resp.text
        assert "verified.user@gmail.com" in resp.text

        # Verify persisted in database
        cred = db.query(GmailOAuthCredential).filter(GmailOAuthCredential.email_address == "verified.user@gmail.com").first()
        assert cred is not None
        assert cred.is_active is True


def test_gmail_disconnect(client: TestClient, db: Session):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cred = GmailOAuthCredential(
        email_address="active.user@gmail.com",
        encrypted_access_token=encrypt_token("tok"),
        encrypted_refresh_token=encrypt_token("ref"),
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

    resp = client.post("/integrations/gmail/disconnect")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    db.refresh(cred)
    assert cred.is_active is False
