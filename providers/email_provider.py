"""
Email Provider Abstraction for Lead Finder.

Supports:
- BaseEmailProvider interface
- MockEmailProvider (for safe development, unit tests, E2E tests, zero external network calls)
- ResendEmailProvider (production provider adapter using environment variables)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional
import uuid

import requests

from config import settings

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
        message_id = f"mock_{uuid.uuid4().hex[:12]}"
        record = {
            "message_id": message_id,
            "to_email": to_email,
            "subject": subject,
            "html_content": html_content,
            "text_content": text_content,
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


class ResendEmailProvider(BaseEmailProvider):
    """
    Production Email Provider adapter for Resend REST API.
    Reads API key securely from application settings.
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

        # Validate recipient email address (prevent CRLF header injection and recipient splitting)
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

            # Error responses
            status_code = response.status_code
            is_transient = status_code in (429, 500, 502, 503, 504)
            error_data = response.text
            try:
                err_json = response.json()
                error_data = err_json.get("message") or str(err_json)
            except Exception:
                pass

            sanitized_error = self._sanitize_error(error_data)
            logger.warning(
                "Resend API error (%d): %s (transient: %s)",
                status_code,
                sanitized_error,
                is_transient,
            )
            return EmailSendResult(
                success=False,
                error=f"Resend error ({status_code}): {sanitized_error}",
                is_transient=is_transient,
            )

        except requests.Timeout:
            logger.warning("Resend API timed out after %ds", self.timeout)
            return EmailSendResult(
                success=False,
                error="Network timeout connecting to Resend API.",
                is_transient=True,
            )
        except Exception as exc:
            sanitized_msg = self._sanitize_error(str(exc))
            logger.warning("Resend API network failure: %s", sanitized_msg)
            return EmailSendResult(
                success=False,
                error=f"Network error: {sanitized_msg}",
                is_transient=True,
            )


# Global singleton instance for mock outbox access
_mock_provider_instance = MockEmailProvider()


def get_email_provider() -> BaseEmailProvider:
    """
    Factory function returning the configured Email Provider.
    Defaults to MockEmailProvider when EMAIL_PROVIDER != 'resend'.
    """
    if settings.EMAIL_PROVIDER == "resend":
        return ResendEmailProvider()
    return _mock_provider_instance
