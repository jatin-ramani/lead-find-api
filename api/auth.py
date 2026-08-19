import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from config import settings
from database.auth import (
    SESSION_COOKIE_NAME,
    hash_session_token,
    session_cookie_options,
    utcnow,
    verify_admin,
)
from database.db import get_db
from database.models import AdminSession
from errors import AppError, ErrorCode

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    secret: str


@router.post(
    "/login",
    summary="Authenticate admin session",
    description="Validates the administrator secret and issues an opaque database-backed session cookie.",
    status_code=status.HTTP_200_OK,
)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    if not payload.secret or not secrets.compare_digest(payload.secret.strip(), settings.admin_secret):
        raise AppError(
            message="Invalid authentication credentials",
            status_code=401,
            error=ErrorCode.UNAUTHORIZED,
        )

    now = utcnow()
    db.execute(delete(AdminSession).where(AdminSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    db.add(
        AdminSession(
            token_hash=hash_session_token(token),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.SESSION_TTL_SECONDS),
        )
    )
    db.commit()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_TTL_SECONDS,
        **session_cookie_options(),
    )
    return {"success": True, "message": "Authenticated successfully."}


@router.post(
    "/logout",
    summary="Invalidate admin session",
    description="Invalidates the current opaque session and clears its HttpOnly cookie.",
    status_code=status.HTTP_200_OK,
)
def logout(
    response: Response,
    leadfinder_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    if leadfinder_session:
        db.query(AdminSession).filter(
            AdminSession.token_hash == hash_session_token(leadfinder_session.strip())
        ).delete(synchronize_session=False)
        db.commit()

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        **session_cookie_options(),
    )
    return {"success": True, "message": "Logged out successfully."}


@router.get(
    "/me",
    summary="Verify admin session status",
    description="Returns 200 only when the Bearer credential or opaque session is valid.",
    dependencies=[Depends(verify_admin)],
    status_code=status.HTTP_200_OK,
)
def auth_me():
    return {"authenticated": True}
