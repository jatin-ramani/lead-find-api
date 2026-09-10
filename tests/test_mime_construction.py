"""
MIME Construction and HTML Delivery Regression Tests for Lead Finder.

Verifies:
- Gmail MIME message is RFC 2046 multipart/alternative
- text/plain part exists and has charset=utf-8
- text/html part exists and has charset=utf-8
- text/html part contains valid <p> and <strong> tags
- text/plain part does NOT contain HTML tags
- Template variables are interpolated cleanly with zero leftover {{...}}
- HTML template tags are preserved (<p> is not &lt;p&gt;)
- User variable values are safely HTML escaped (<Test & Business> -> &lt;Test &amp; Business&gt;)
- Plain text fallback self-heals into valid semantic HTML
"""

import base64
from datetime import datetime, timedelta, timezone
import email
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from database.models import GmailOAuthCredential
from providers.email_provider import GmailEmailProvider, MockEmailProvider
from security.crypto import encrypt_token
from services.email_template_service import DEFAULT_UNIVERSAL_TEMPLATE
from services.template_engine import (
    ensure_html_email,
    html_to_plain_text,
    render_template,
)


def _parse_mime_from_b64(raw_b64: str) -> Message:
    """Decode raw URL-safe base64 string and parse into email.message.Message."""
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("utf-8"))
    return email.message_from_bytes(raw_bytes)


def _setup_test_credential(db: Session) -> GmailOAuthCredential:
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cred = db.query(GmailOAuthCredential).filter(GmailOAuthCredential.email_address == "sales@gmail.com").first()
    if not cred:
        cred = GmailOAuthCredential(
            email_address="sales@gmail.com",
            encrypted_access_token=encrypt_token("mock_access_token"),
            encrypted_refresh_token=encrypt_token("mock_refresh_token"),
            token_expiry=now_utc + timedelta(hours=1),
            scopes="https://www.googleapis.com/auth/gmail.send",
            is_active=True,
            daily_send_count=0,
            daily_send_reset_date=today_utc,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.add(cred)
        db.commit()
        db.refresh(cred)
    return cred


def test_mime_multipart_alternative_structure(db: Session):
    """Verify GmailEmailProvider constructs a compliant multipart/alternative MIME message."""
    _setup_test_credential(db)
    provider = GmailEmailProvider()

    context = {
        "business_name": "Apex Dental Clinic",
        "contact_name": "Dr. Sarah Smith",
        "city": "Surat",
    }

    raw_html = DEFAULT_UNIVERSAL_TEMPLATE["body"]
    rendered_html = render_template(raw_html, context, escape_html=True)
    plain_text = html_to_plain_text(rendered_html)

    captured_payload = {}

    def mock_post(url, headers, json, timeout):
        captured_payload.update(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "gmail_msg_12345"}
        return mock_resp

    with patch("requests.post", side_effect=mock_post):
        result = provider.send_email(
            to_email="recipient@example.com",
            subject="Quick idea for Apex Dental Clinic",
            html_content=rendered_html,
            text_content=plain_text,
            metadata={"db": db},
        )

        assert result.success is True
        assert result.message_id == "gmail_msg_12345"

    assert "raw" in captured_payload
    parsed_msg = _parse_mime_from_b64(captured_payload["raw"])

    # 1. Root is multipart/alternative
    assert parsed_msg.is_multipart()
    assert parsed_msg.get_content_type() == "multipart/alternative"

    # 2. Extract parts
    parts = parsed_msg.get_payload()
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"

    plain_part = parts[0]
    html_part = parts[1]

    # RFC 2046: Plain text first, HTML second
    assert plain_part.get_content_type() == "text/plain"
    assert plain_part.get_content_charset() == "utf-8"
    assert html_part.get_content_type() == "text/html"
    assert html_part.get_content_charset() == "utf-8"

    plain_body = plain_part.get_payload(decode=True).decode("utf-8")
    html_body = html_part.get_payload(decode=True).decode("utf-8")

    # 3. HTML part assertions
    assert "<p>" in html_body
    assert "</p>" in html_body
    assert "<strong>look more credible, capture more leads and turn visitors into customers.</strong>" in html_body
    assert "<strong>Jatin Ramani</strong>" in html_body
    assert "Apex Dental Clinic" in html_body
    assert "Dr. Sarah Smith" in html_body
    assert "Surat" in html_body
    assert "{{Business Name}}" not in html_body
    assert "{{Contact Name}}" not in html_body
    assert "{{City}}" not in html_body

    # 4. Plain part assertions
    assert "<p>" not in plain_body
    assert "</p>" not in plain_body
    assert "<strong>" not in plain_body
    assert "</strong>" not in plain_body
    assert "Apex Dental Clinic" in plain_body
    assert "Dr. Sarah Smith" in plain_body
    assert "Surat" in plain_body
    assert "look more credible, capture more leads and turn visitors into customers." in plain_body
    assert "Jatin Ramani" in plain_body
    assert "{{Business Name}}" not in plain_body
    assert "{{Contact Name}}" not in plain_body
    assert "{{City}}" not in plain_body


def test_html_tags_not_double_escaped_while_variables_are_escaped(db: Session):
    """Verify HTML template tags remain literal HTML while user variable values are safely escaped."""
    _setup_test_credential(db)
    provider = GmailEmailProvider()

    context = {
        "business_name": "<Dangerous & Special Co>",
        "contact_name": "John <Doe>",
    }

    raw_html = "<p>Hello <strong>{{business_name}}</strong>!</p>"
    rendered_html = render_template(raw_html, context, escape_html=True)
    plain_text = html_to_plain_text(rendered_html)

    captured_payload = {}

    def mock_post(url, headers, json, timeout):
        captured_payload.update(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "gmail_msg_xss_test"}
        return mock_resp

    with patch("requests.post", side_effect=mock_post):
        result = provider.send_email(
            to_email="test@example.com",
            subject="Test Special Chars",
            html_content=rendered_html,
            text_content=plain_text,
            metadata={"db": db},
        )
        assert result.success is True

    parsed_msg = _parse_mime_from_b64(captured_payload["raw"])
    parts = parsed_msg.get_payload()
    html_body = parts[1].get_payload(decode=True).decode("utf-8")
    plain_body = parts[0].get_payload(decode=True).decode("utf-8")

    # Template HTML tags must be actual tags, NOT escaped
    assert "<p>Hello <strong>&lt;Dangerous &amp; Special Co&gt;</strong>!</p>" == html_body
    assert "&lt;p&gt;" not in html_body
    assert "&lt;strong&gt;" not in html_body

    # Plain text must NOT contain HTML tags and should unescape the entities for plain reading
    assert "<p>" not in plain_body
    assert "<strong>" not in plain_body
    assert "Hello <Dangerous & Special Co>!" == plain_body


def test_plain_text_input_automatically_converts_to_semantic_html(db: Session):
    """Verify that if plain text is passed to provider, it self-heals into valid semantic HTML."""
    _setup_test_credential(db)
    provider = GmailEmailProvider()

    raw_plain_text = (
        "Hi Acme Corp team,\n\n"
        "A strong website can completely change how a potential customer sees a business.\n\n"
        "We're Codebait, a web design studio.\n\n"
        "Best,\n"
        "Jatin Ramani"
    )

    captured_payload = {}

    def mock_post(url, headers, json, timeout):
        captured_payload.update(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "gmail_plain_test"}
        return mock_resp

    with patch("requests.post", side_effect=mock_post):
        result = provider.send_email(
            to_email="test@example.com",
            subject="Plain Text Auto-Heal",
            html_content=raw_plain_text,  # passed plain text as html_content
            text_content=None,
            metadata={"db": db},
        )
        assert result.success is True

    parsed_msg = _parse_mime_from_b64(captured_payload["raw"])
    parts = parsed_msg.get_payload()
    html_body = parts[1].get_payload(decode=True).decode("utf-8")
    plain_body = parts[0].get_payload(decode=True).decode("utf-8")

    # HTML part must have <p> paragraphs
    assert "<p>Hi Acme Corp team,</p>" in html_body
    assert "<strong>Codebait</strong>" in html_body
    assert "<strong>Jatin Ramani</strong>" in html_body

    # Plain text part must not have tags
    assert "<p>" not in plain_body
    assert "<strong>" not in plain_body
    assert "Hi Acme Corp team," in plain_body


def test_mock_email_provider_also_populates_clean_html_and_text():
    """Verify MockEmailProvider records clean HTML and plain text in its outbox."""
    provider = MockEmailProvider()

    raw_plain = "Hello world\n\nSecond paragraph"
    result = provider.send_email(
        to_email="mock@example.com",
        subject="Mock Test",
        html_content=raw_plain,
        text_content=None,
    )
    assert result.success is True

    outbox = provider.get_outbox()
    assert len(outbox) == 1
    record = outbox[0]

    assert "<p>Hello world</p>" in record["html_content"]
    assert "<p>Second paragraph</p>" in record["html_content"]
    assert "<p>" not in record["text_content"]
    assert "Hello world\n\nSecond paragraph" == record["text_content"]
