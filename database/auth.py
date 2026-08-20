"""Opaque, database-backed administrative sessions."""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import Cookie, Depends, Header
from sqlalchemy.orm import Session

from config import settings
from database.db import get_db
from database.models import AdminSession
from errors import AppError, ErrorCode

SESSION_COOKIE_NAME = "leadfinder_session"


def session_cookie_options() -> dict[str, object]:
    """Cookie attributes shared by login and logout responses."""

    return {
        "httponly": True,
        "samesite": "none" if settings.is_production else "lax",
        "secure": settings.is_production,
        "path": "/",
    }


def hash_session_token(token: str) -> str:
    """Return the non-reversible database representation of a session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    # SQLAlchemy's SQLite DateTime round-trips as a naive value. Keeping all
    # persisted values naive UTC makes comparisons identical on both databases.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def verify_admin(
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="x-session-token"),
    leadfinder_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> None:
    """Allow the API secret as Bearer auth, header-based session token, or live opaque cookie."""
    if authorization:
        parts = authorization.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            bearer_val = parts[1].strip()
            # 1. Check if bearer matches static admin secret
            if secrets.compare_digest(bearer_val, settings.admin_secret):
                return
            # 2. Check if bearer contains an active opaque session token
            token_hash = hash_session_token(bearer_val)
            session = db.get(AdminSession, token_hash)
            now = utcnow()
            if session is not None and session.expires_at > now:
                return

    # 3. Check X-Session-Token header
    if x_session_token:
        token_hash = hash_session_token(x_session_token.strip())
        session = db.get(AdminSession, token_hash)
        now = utcnow()
        if session is not None and session.expires_at > now:
            return
        if session is not None:
            db.delete(session)
            db.commit()

    # 4. Check leadfinder_session cookie
    if leadfinder_session:
        token_hash = hash_session_token(leadfinder_session.strip())
        session = db.get(AdminSession, token_hash)
        now = utcnow()
        if session is not None and session.expires_at > now:
            return
        if session is not None:
            db.delete(session)
            db.commit()

    raise AppError(
        message="Authentication required",
        status_code=401,
        error=ErrorCode.UNAUTHORIZED,
    )
