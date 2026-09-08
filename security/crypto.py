"""
Cryptographic Utilities for Token Security and OAuth State Verification.

Provides Fernet-based symmetric encryption for OAuth access and refresh tokens,
and HMAC-SHA256 based tamper-evident state tokens for CSRF prevention in OAuth flows.
"""

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import secrets
import time
from typing import Tuple

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import settings

# Static salt for PBKDF2 key derivation from ADMIN_SECRET_KEY
_KDF_SALT = b"leadfinder_gmail_oauth_token_vault_salt_2026"


def _derive_fernet_key() -> bytes:
    """
    Derive a 32-byte Fernet key from GMAIL_TOKEN_ENCRYPTION_KEY (if provided)
    or ADMIN_SECRET_KEY via PBKDF2HMAC.
    """
    custom_key = settings.gmail_token_encryption_key
    if custom_key and len(custom_key) >= 16:
        secret_bytes = custom_key.encode("utf-8")
    else:
        secret_bytes = settings.admin_secret.encode("utf-8")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=100_000,
    )
    derived = kdf.derive(secret_bytes)
    return base64.urlsafe_b64encode(derived)


def encrypt_token(plaintext: str) -> str:
    """
    Encrypt a plaintext token string with Fernet.
    Returns the ciphertext as a UTF-8 string.
    """
    if not plaintext:
        return ""
    key = _derive_fernet_key()
    fernet = Fernet(key)
    encrypted_bytes = fernet.encrypt(plaintext.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    """
    Decrypt a Fernet-encrypted ciphertext string.
    Returns the decrypted plaintext.
    """
    if not ciphertext:
        return ""
    key = _derive_fernet_key()
    fernet = Fernet(key)
    decrypted_bytes = fernet.decrypt(ciphertext.encode("utf-8"))
    return decrypted_bytes.decode("utf-8")


def generate_oauth_state() -> str:
    """
    Generate a tamper-evident, time-bound OAuth state token for CSRF protection.
    Format: base64url(nonce.timestamp.hmac_signature)
    """
    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time()))
    payload = f"{nonce}.{timestamp}"

    sig = hmac.new(
        key=settings.admin_secret.encode("utf-8"),
        msg=payload.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    raw_state = f"{payload}.{sig}"
    return base64.urlsafe_b64encode(raw_state.encode("utf-8")).decode("utf-8").rstrip("=")


def verify_oauth_state(state: str, max_age_seconds: int = 600) -> bool:
    """
    Verify an OAuth state token.
    Checks HMAC signature and ensures the token was generated within max_age_seconds (default 10 min).
    """
    if not state or not isinstance(state, str):
        return False

    try:
        # Restore base64 padding
        padded = state + "=" * (-len(state) % 4)
        raw_state = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")

        parts = raw_state.split(".")
        if len(parts) != 3:
            return False

        nonce, timestamp_str, sig = parts
        timestamp = int(timestamp_str)
        now = int(time.time())

        # Check expiration
        if abs(now - timestamp) > max_age_seconds:
            return False

        payload = f"{nonce}.{timestamp_str}"
        expected_sig = hmac.new(
            key=settings.admin_secret.encode("utf-8"),
            msg=payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False
