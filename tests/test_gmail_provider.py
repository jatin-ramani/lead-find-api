"""
Unit and Integration Tests for GmailEmailProvider and Token Security.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from config import settings
from database.models import GmailOAuthCredential
from providers.email_provider import GmailEmailProvider, get_email_provider
from security.crypto import decrypt_token, encrypt_token, generate_oauth_state, verify_oauth_state


def test_crypto_token_encryption_and_decryption():
    secret_token = "ya29.a0AfH6SMA-test-access-token-12345"
    encrypted = encrypt_token(secret_token)
    assert encrypted != secret_token
    assert decrypt_token(encrypted) == secret_token


def test_oauth_state_generation_and_validation():
    state = generate_oauth_state()
    assert verify_oauth_state(state, max_age_seconds=600) is True

    # Tampered state
    tampered = state[:-4] + "abcd"
    assert verify_oauth_state(tampered) is False

    # Empty or malformed state
    assert verify_oauth_state("") is False
    assert verify_oauth_state("invalid.state") is False


def test_gmail_provider_no_connected_credential(db: Session):
    provider = GmailEmailProvider()
    result = provider.send_email(
        to_email="test@example.com",
        subject="Hello",
        html_content="<p>Test</p>",
        metadata={"db": db},
    )
    assert result.success is False
    assert "No connected Gmail account" in result.error


def test_gmail_provider_invalid_recipient(db: Session):
    provider = GmailEmailProvider()
    result = provider.send_email(
        to_email="invalid-email-address",
        subject="Hello",
        html_content="<p>Test</p>",
        metadata={"db": db},
    )
    assert result.success is False
    assert "Invalid recipient email" in result.error


def test_gmail_provider_successful_send(db: Session, monkeypatch):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cred = GmailOAuthCredential(
        email_address="sales@gmail.com",
        encrypted_access_token=encrypt_token("mock_access_token"),
        encrypted_refresh_token=encrypt_token("mock_refresh_token"),
        token_expiry=now_utc + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=5,
        daily_send_reset_date=today_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    provider = GmailEmailProvider()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "1894ab12cd34ef56"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = provider.send_email(
            to_email="client@example.com",
            subject="Strategic Proposal",
            html_content="<p>Hi there,</p>",
            text_content="Hi there,",
            metadata={"db": db},
        )

        assert result.success is True
        assert result.message_id == "1894ab12cd34ef56"
        assert mock_post.called

        # Verify Authorization header and URL
        call_args = mock_post.call_args
        assert call_args[0][0] == provider.GMAIL_SEND_URL
        assert call_args[1]["headers"]["Authorization"] == "Bearer mock_access_token"

        # Verify daily send count incremented atomically
        db.refresh(cred)
        assert cred.daily_send_count == 6


def test_gmail_provider_400_daily_quota_limit_enforced(db: Session):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cred = GmailOAuthCredential(
        email_address="sales@gmail.com",
        encrypted_access_token=encrypt_token("mock_access_token"),
        encrypted_refresh_token=encrypt_token("mock_refresh_token"),
        token_expiry=now_utc + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=400,  # Max limit reached
        daily_send_reset_date=today_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    provider = GmailEmailProvider()
    result = provider.send_email(
        to_email="client@example.com",
        subject="Proposal",
        html_content="<p>Hello</p>",
        metadata={"db": db},
    )

    assert result.success is False
    assert "Daily sending limit reached" in result.error
    assert cred.daily_send_count == 400


def test_gmail_provider_daily_quota_resets_on_new_utc_day(db: Session):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday_utc = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    cred = GmailOAuthCredential(
        email_address="sales@gmail.com",
        encrypted_access_token=encrypt_token("mock_access_token"),
        encrypted_refresh_token=encrypt_token("mock_refresh_token"),
        token_expiry=now_utc + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=400,  # Was at limit yesterday
        daily_send_reset_date=yesterday_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    provider = GmailEmailProvider()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "msg_new_day_123"}

    with patch("requests.post", return_value=mock_resp):
        result = provider.send_email(
            to_email="client@example.com",
            subject="Proposal",
            html_content="<p>Hello</p>",
            metadata={"db": db},
        )

        assert result.success is True
        db.refresh(cred)
        # Should reset to 0 then increment to 1
        assert cred.daily_send_count == 1
        assert cred.daily_send_reset_date == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_gmail_provider_auto_token_refresh(db: Session, monkeypatch):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "test-client-secret")

    # Token expired 10 seconds ago
    cred = GmailOAuthCredential(
        email_address="sales@gmail.com",
        encrypted_access_token=encrypt_token("old_expired_access_token"),
        encrypted_refresh_token=encrypt_token("valid_refresh_token"),
        token_expiry=now_utc - timedelta(seconds=10),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=0,
        daily_send_reset_date=today_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    provider = GmailEmailProvider()

    def mock_requests_post(url, *args, **kwargs):
        resp = MagicMock()
        if url == provider.GOOGLE_TOKEN_URL:
            resp.status_code = 200
            resp.json.return_value = {
                "access_token": "fresh_new_access_token",
                "expires_in": 3600,
            }
            return resp
        elif url == provider.GMAIL_SEND_URL:
            # Verify the refreshed token is used in Authorization header
            assert kwargs["headers"]["Authorization"] == "Bearer fresh_new_access_token"
            resp.status_code = 200
            resp.json.return_value = {"id": "gmail_sent_after_refresh"}
            return resp
        resp.status_code = 404
        return resp

    with patch("requests.post", side_effect=mock_requests_post):
        result = provider.send_email(
            to_email="client@example.com",
            subject="Proposal",
            html_content="<p>Hello</p>",
            metadata={"db": db},
        )

        assert result.success is True
        assert result.message_id == "gmail_sent_after_refresh"
        db.refresh(cred)
        assert decrypt_token(cred.encrypted_access_token) == "fresh_new_access_token"


def test_gmail_provider_token_revoked_deactivates_credential(db: Session, monkeypatch):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "test-client-secret")

    # Expired token with revoked refresh token
    cred = GmailOAuthCredential(
        email_address="sales@gmail.com",
        encrypted_access_token=encrypt_token("old_token"),
        encrypted_refresh_token=encrypt_token("revoked_refresh_token"),
        token_expiry=now_utc - timedelta(minutes=5),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=0,
        daily_send_reset_date=today_utc,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(cred)
    db.commit()

    provider = GmailEmailProvider()

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 400
    mock_token_resp.text = '{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'

    with patch("requests.post", return_value=mock_token_resp):
        result = provider.send_email(
            to_email="client@example.com",
            subject="Proposal",
            html_content="<p>Hello</p>",
            metadata={"db": db},
        )

        assert result.success is False
        assert "Gmail authorization expired or revoked" in result.error
        db.refresh(cred)
        assert cred.is_active is False
