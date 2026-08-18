import secrets
"""
Authentication dependency for protecting administrative API routes.

Verifies credentials from:
1. `Authorization: Bearer <token>` header
2. `leadfinder_session` HttpOnly cookie

Rejects missing or invalid credentials with a standard 401 UNAUTHORIZED envelope.
"""

from typing import Optional
from fastapi import Cookie, Header
from config import settings
from errors import AppError, ErrorCode

SESSION_COOKIE_NAME = "leadfinder_session"


def verify_admin(
    authorization: Optional[str] = Header(None),
    leadfinder_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
) -> None:
    """
    FastAPI dependency enforcing administrative authentication.

    Raises AppError(401, ErrorCode.UNAUTHORIZED) if credentials are missing or invalid.
    """
    token: Optional[str] = None

    if authorization:
        parts = authorization.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()

    if not token and leadfinder_session:
        token = leadfinder_session.strip()

    if not token or not secrets.compare_digest(token, settings.admin_secret):
        raise AppError(
            message="Authentication required",
            status_code=401,
            error=ErrorCode.UNAUTHORIZED,
        )
