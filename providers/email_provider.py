"""
Email Provider Abstraction for Lead Finder.

Supports:
- BaseEmailProvider interface
- MockEmailProvider (for safe development, unit tests, E2E tests, zero external network calls)
- GmailEmailProvider (production provider adapter using Gmail REST API & OAuth 2.0 with 400/day safety ceiling)
- ResendEmailProvider (legacy provider adapter)
"""

from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import re
from typing import Any, Dict, List, Optional
import uuid

import requests

from config import settings
from database.db import SessionLocal
from database.models import GmailOAuthCredential
from security.crypto import decrypt_token, encrypt_token
from services.template_engine import ensure_html_email, html_to_plain_text

logger = logging.getLogger(__name__)


@dataclass
class EmailSendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    is_transient: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BaseEmailProvider(ABC):
    """Abstract Base Class for all Email Providers."""

    @abstractmethod
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        """Send an email to a recipient."""
        raise NotImplementedError


class MockEmailProvider(BaseEmailProvider):
    """
    Mock Email Provider for local development, pytest, and Playwright suites.
    Records all attempted email sends in an in-memory outbox.
    """

    def __init__(self):
        self._outbox: List[Dict[str, Any]] = []

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        clean_html = ensure_html_email(html_content) if html_content else ""
        clean_text = text_content if text_content is not None else (html_to_plain_text(clean_html) if clean_html else "")
        if clean_text and ("<p" in clean_text or "<br" in clean_text or "<div" in clean_text or "<strong>" in clean_text or "<html" in clean_text):
            clean_text = html_to_plain_text(clean_text)

        message_id = f"mock_{uuid.uuid4().hex[:12]}"
        record = {
            "message_id": message_id,
            "to_email": to_email,
            "subject": subject,
            "html_content": clean_html,
            "text_content": clean_text,
            "metadata": metadata or {},
            "sent_at": datetime.now(timezone.utc),
        }
        self._outbox.append(record)
        logger.info(
            "MockEmailProvider recorded email to %s (subject: %s, ID: %s)",
            to_email,
            subject,
            message_id,
        )
        return EmailSendResult(
            success=True,
            message_id=message_id,
            error=None,
            is_transient=False,
        )

    def get_outbox(self) -> List[Dict[str, Any]]:
        return list(self._outbox)

    def clear(self) -> None:
        self._outbox.clear()


class GmailEmailProvider(BaseEmailProvider):
    """
    Production Email Provider adapter using Google Gmail REST API.
    - Sends via users/me/messages/send
    - From address is strictly derived from authenticated Gmail account
    - Auto-refreshes tokens before expiration
    - Enforces 400-email/day application safety ceiling (UTC calendar reset)
    """

    GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def _sanitize_error(self, text: str) -> str:
        """Redact secrets and sensitive tokens from error messages."""
        if not text:
            return ""
        sanitized = re.sub(r"ya29\.[a-zA-Z0-9_\-]+", "[REDACTED_ACCESS_TOKEN]", str(text))
        sanitized = re.sub(r"1//[a-zA-Z0-9_\-]+", "[REDACTED_REFRESH_TOKEN]", sanitized)
        client_secret = settings.gmail_client_secret
        if client_secret and len(client_secret) > 4:
            sanitized = sanitized.replace(client_secret, "[REDACTED_CLIENT_SECRET]")
        return sanitized

    def _get_active_credential(self, db) -> Optional[GmailOAuthCredential]:
        """Fetch the current active Gmail credential."""
        return (
            db.query(GmailOAuthCredential)
            .filter(GmailOAuthCredential.is_active.is_(True))
            .order_by(GmailOAuthCredential.id.desc())
            .first()
        )

    def _refresh_access_token(self, db, cred: GmailOAuthCredential) -> Optional[str]:
        """
        Refresh access token using refresh_token if expired or close to expiry (<60s).
        Returns the plaintext access token or None if refresh failed.
        """
        now_utc = datetime.now(timezone.utc)
        naive_now = now_utc.replace(tzinfo=None)
        
        # Check if token is still valid (with 60s buffer)
        if cred.token_expiry and cred.token_expiry > naive_now + timedelta(seconds=60):
            try:
                return decrypt_token(cred.encrypted_access_token)
            except Exception as exc:
                logger.error("Failed to decrypt current access token: %s", self._sanitize_error(str(exc)))

        # Refresh required
        refresh_token = ""
        try:
            refresh_token = decrypt_token(cred.encrypted_refresh_token)
        except Exception as exc:
            logger.error("Failed to decrypt refresh token: %s", self._sanitize_error(str(exc)))
            return None

        if not refresh_token:
            logger.error("No refresh token stored for Gmail credential %s", cred.email_address)
            return None

        payload = {
            "client_id": settings.gmail_client_id,
            "client_secret": settings.gmail_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            resp = requests.post(self.GOOGLE_TOKEN_URL, data=payload, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                new_access_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).replace(tzinfo=None)

                cred.encrypted_access_token = encrypt_token(new_access_token)
                cred.token_expiry = new_expiry
                cred.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
                return new_access_token
            else:
                logger.error(
                    "Google token refresh failed (%d): %s",
                    resp.status_code,
                    self._sanitize_error(resp.text),
                )
                if resp.status_code in (400, 401):
                    # Refresh token revoked or invalid
                    cred.is_active = False
                    db.commit()
                return None
        except Exception as exc:
            logger.error("Google token refresh exception: %s", self._sanitize_error(str(exc)))
            return None

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        # Validate recipient email format
        cleaned_to = (to_email or "").strip()
        if (
            not cleaned_to
            or "\r" in cleaned_to
            or "\n" in cleaned_to
            or "," in cleaned_to
            or ";" in cleaned_to
            or not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", cleaned_to)
        ):
            return EmailSendResult(
                success=False,
                error=f"Invalid recipient email address format: '{cleaned_to[:50]}'",
                is_transient=False,
            )

        # Sanitize subject to prevent header injection
        sanitized_subject = re.sub(r"[\r\n]+", " ", subject or "").strip()
        if not sanitized_subject:
            sanitized_subject = "(No Subject)"

        # Use passed DB session or create a scoped session
        provided_db = metadata.get("db") if metadata else None
        db = provided_db or SessionLocal()
        should_close = provided_db is None

        try:
            cred = self._get_active_credential(db)
            if not cred:
                return EmailSendResult(
                    success=False,
                    error="No connected Gmail account found. Please connect your Gmail account in Email Automation.",
                    is_transient=False,
                )

            # Check and reset daily quota for current UTC calendar day
            today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if cred.daily_send_reset_date != today_utc:
                cred.daily_send_count = 0
                cred.daily_send_reset_date = today_utc
                db.commit()

            # Enforce 400-email/day application hard ceiling
            quota_limit = settings.EMAIL_DAILY_QUOTA_LIMIT
            if cred.daily_send_count >= quota_limit:
                return EmailSendResult(
                    success=False,
                    error=f"Daily sending limit reached ({quota_limit}/{quota_limit} sent today). Quota resets at 00:00 UTC.",
                    is_transient=False,
                )

            # Get valid access token
            access_token = self._refresh_access_token(db, cred)
            if not access_token:
                return EmailSendResult(
                    success=False,
                    error="Gmail authorization expired or revoked. Please reconnect your account.",
                    is_transient=False,
                )

            # Build RFC 2822 / RFC 2046 MIME Message strictly using authenticated Gmail address as From
            clean_html = ensure_html_email(html_content) if html_content else ""
            clean_text = text_content if text_content is not None else (html_to_plain_text(clean_html) if clean_html else "")
            if clean_text and ("<p" in clean_text or "<br" in clean_text or "<div" in clean_text or "<strong>" in clean_text or "<html" in clean_text):
                clean_text = html_to_plain_text(clean_text)

            msg = MIMEMultipart("alternative")
            msg["To"] = cleaned_to
            msg["From"] = cred.email_address
            msg["Subject"] = sanitized_subject

            # RFC 2046 Section 5.1.4: plain text must be attached first, HTML attached last
            if clean_text:
                msg.attach(MIMEText(clean_text, "plain", "utf-8"))
            else:
                msg.attach(MIMEText("", "plain", "utf-8"))

            if clean_html:
                msg.attach(MIMEText(clean_html, "html", "utf-8"))

            raw_bytes = msg.as_bytes()
            raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

            # Execute Gmail REST API request
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            payload = {"raw": raw_b64}

            response = requests.post(
                self.GMAIL_SEND_URL,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )

            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("id") or f"gmail_{uuid.uuid4().hex[:8]}"

                # Increment daily send count atomically
                cred.daily_send_count += 1
                cred.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()

                logger.info("GmailEmailProvider delivered email to %s (ID: %s)", cleaned_to, msg_id)
                return EmailSendResult(
                    success=True,
                    message_id=msg_id,
                )

            # Error responses
            status_code = response.status_code
            is_transient = status_code in (429, 500, 502, 503, 504)
            error_data = response.text
            try:
                err_json = response.json()
                error_data = (
                    err_json.get("error", {}).get("message")
                    or err_json.get("message")
                    or str(err_json)
                )
            except Exception:
                pass

            sanitized_error = self._sanitize_error(error_data)
            logger.warning(
                "Gmail API error (%d): %s (transient: %s)",
                status_code,
                sanitized_error,
                is_transient,
            )
            return EmailSendResult(
                success=False,
                error=f"Gmail API error ({status_code}): {sanitized_error}",
                is_transient=is_transient,
            )

        except requests.Timeout:
            logger.warning("Gmail API timed out after %ds", self.timeout)
            return EmailSendResult(
                success=False,
                error="Network timeout connecting to Gmail API.",
                is_transient=True,
            )
        except Exception as exc:
            sanitized_msg = self._sanitize_error(str(exc))
            logger.warning("Gmail API network failure: %s", sanitized_msg)
            return EmailSendResult(
                success=False,
                error=f"Network error: {sanitized_msg}",
                is_transient=True,
            )
        finally:
            if should_close:
                db.close()


class ResendEmailProvider(BaseEmailProvider):
    """
    Legacy Email Provider adapter for Resend REST API.
    """

    RESEND_API_URL = "https://api.resend.com/emails"

    def __init__(
        self,
        api_key: Optional[str] = None,
        from_email: Optional[str] = None,
        timeout: int = 15,
    ):
        self.api_key = api_key or settings.resend_api_key
        self.from_email = from_email or settings.RESEND_FROM_EMAIL
        self.timeout = timeout

    def _sanitize_error(self, text: str) -> str:
        """Redact secrets/API keys from error messages."""
        if not text:
            return ""
        sanitized = re.sub(r"re_[a-zA-Z0-9_]{10,}", "[REDACTED_API_KEY]", str(text))
        if self.api_key and len(self.api_key) > 4:
            sanitized = sanitized.replace(self.api_key, "[REDACTED_API_KEY]")
        return sanitized

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        if not self.api_key:
            logger.error("ResendEmailProvider failed: RESEND_API_KEY is not configured")
            return EmailSendResult(
                success=False,
                error="Resend API key is not configured in application settings.",
                is_transient=False,
            )

        cleaned_to = (to_email or "").strip()
        if (
            not cleaned_to
            or "\r" in cleaned_to
            or "\n" in cleaned_to
            or "," in cleaned_to
            or ";" in cleaned_to
            or not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", cleaned_to)
        ):
            return EmailSendResult(
                success=False,
                error=f"Invalid recipient email address format: '{cleaned_to[:50]}'",
                is_transient=False,
            )

        sanitized_subject = re.sub(r"[\r\n]+", " ", subject or "").strip()
        if not sanitized_subject:
            sanitized_subject = "(No Subject)"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "from": self.from_email,
            "to": [cleaned_to],
            "subject": sanitized_subject,
            "html": html_content or "",
        }
        if text_content:
            payload["text"] = text_content

        try:
            response = requests.post(
                self.RESEND_API_URL,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            if response.status_code in (200, 201):
                data = response.json()
                msg_id = data.get("id") or f"resend_{uuid.uuid4().hex[:8]}"
                return EmailSendResult(
                    success=True,
                    message_id=msg_id,
                )

            status_code = response.status_code
            is_transient = status_code in (429, 500, 502, 503, 504)
            error_data = response.text
            try:
                err_json = response.json()
                error_data = err_json.get("message") or str(err_json)
            except Exception:
                pass

            sanitized_error = self._sanitize_error(error_data)
            return EmailSendResult(
                success=False,
                error=f"Resend error ({status_code}): {sanitized_error}",
                is_transient=is_transient,
            )

        except requests.Timeout:
            return EmailSendResult(
                success=False,
                error="Network timeout connecting to Resend API.",
                is_transient=True,
            )
        except Exception as exc:
            sanitized_msg = self._sanitize_error(str(exc))
            return EmailSendResult(
                success=False,
                error=f"Network error: {sanitized_msg}",
                is_transient=True,
            )


# Global singleton instance for mock outbox access
_mock_provider_instance = MockEmailProvider()
_gmail_provider_instance = GmailEmailProvider()
_resend_provider_instance = ResendEmailProvider()


def get_email_provider() -> BaseEmailProvider:
    """
    Factory function returning the configured Email Provider.
    Defaults to MockEmailProvider when EMAIL_PROVIDER == 'mock'.
    """
    if settings.EMAIL_PROVIDER == "gmail":
        return _gmail_provider_instance
    elif settings.EMAIL_PROVIDER == "resend":
        return _resend_provider_instance
    return _mock_provider_instance
