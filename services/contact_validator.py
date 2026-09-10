"""
Contact Information Normalization and Validation Service for Lead Finder Scanner.

Enforces:
1. Strict Email OR Phone storage rule:
   - If valid email exists → STORE
   - If valid phone exists → STORE
   - If both exist → STORE
   - If neither exists → SKIP (do NOT store in database)
2. Normalized clean formatting for storage and CRM deduplication.
"""

import re
from typing import Optional, Tuple

_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)

_INVALID_EMAIL_DOMAINS = frozenset({
    "example.com",
    "example.org",
    "test.com",
    "none.com",
    "dummy.com",
    "domain.com",
    "localhost",
})


def normalize_and_validate_email(email_str: Optional[str]) -> Optional[str]:
    """
    Validate and clean an email address.
    Returns lowercase normalized email if valid, else None.
    """
    if not email_str:
        return None

    cleaned = str(email_str).strip().lower()
    if not cleaned or len(cleaned) < 5 or len(cleaned) > 254:
        return None

    if not _EMAIL_REGEX.match(cleaned):
        return None

    # Check domain
    domain = cleaned.split("@")[-1]
    if domain in _INVALID_EMAIL_DOMAINS:
        return None

    return cleaned


def normalize_and_validate_phone(phone_str: Optional[str]) -> Optional[str]:
    """
    Validate and clean a telephone or mobile number.
    Preserves international '+' country prefix when present.
    Returns normalized string with digits/country code if valid, else None.
    """
    if not phone_str:
        return None

    raw = str(phone_str).strip()
    if not raw:
        return None

    # Preserve leading '+' if international format
    has_plus = raw.startswith("+")
    digits_only = re.sub(r"\D", "", raw)

    # Valid phone numbers usually have 7 to 15 digits
    if len(digits_only) < 7 or len(digits_only) > 15:
        return None

    # Filter out repeated placeholder digits (e.g. "0000000000", "1111111111")
    if len(set(digits_only)) <= 1:
        return None

    return f"+{digits_only}" if has_plus else digits_only


def evaluate_lead_contacts(
    raw_email: Optional[str],
    raw_phone: Optional[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Evaluate business contact details.

    Returns:
        Tuple of (is_storable, validated_email, validated_phone).
        - is_storable is True if at least ONE valid contact (email OR phone) exists.
        - is_storable is False if BOTH contacts are missing/invalid.
    """
    valid_email = normalize_and_validate_email(raw_email)
    valid_phone = normalize_and_validate_phone(raw_phone)

    is_storable = bool(valid_email or valid_phone)
    return is_storable, valid_email, valid_phone
