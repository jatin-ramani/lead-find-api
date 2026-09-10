"""
Gmail OAuth 2.0 Integration API for Lead Finder.

Provides endpoints for OAuth authentication URL generation, OAuth callback exchange,
status monitoring, and account disconnection.
"""

from datetime import datetime, timedelta, timezone
import logging
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
import requests
from sqlalchemy import or_
from sqlalchemy.orm import Session

from config import settings
from database.auth import verify_admin
from database.db import get_db
from database.models import EmailTemplate, GmailOAuthCredential
from providers.ai_provider import get_ai_provider
from security.crypto import decrypt_token, encrypt_token, generate_oauth_state, verify_oauth_state

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/integrations/gmail",
    tags=["Integrations"],
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _get_redirect_uri(request: Request) -> str:
    """Resolve redirect URI from settings or current request URL."""
    if settings.GMAIL_REDIRECT_URI:
        return settings.GMAIL_REDIRECT_URI
    # Fallback to current host
    scheme = request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}/integrations/gmail/callback"


@router.get(
    "/status",
    summary="Get Gmail Connection Status",
    description="Returns current Gmail connection status and daily send quota usage.",
)
def get_gmail_status(
    db: Session = Depends(get_db),
    _admin: None = Depends(verify_admin),
) -> Dict[str, Any]:
    cred = (
        db.query(GmailOAuthCredential)
        .filter(GmailOAuthCredential.is_active.is_(True))
        .order_by(GmailOAuthCredential.id.desc())
        .first()
    )

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_sent = 0
    if cred:
        if cred.daily_send_reset_date == today_utc:
            daily_sent = cred.daily_send_count
        else:
            daily_sent = 0

    return {
        "success": True,
        "is_configured": settings.has_gmail_credentials,
        "is_connected": bool(cred and cred.is_active),
        "email_address": cred.email_address if cred and cred.is_active else None,
        "daily_send_count": daily_sent,
        "daily_quota_limit": settings.EMAIL_DAILY_QUOTA_LIMIT,
        "daily_quota_remaining": max(0, settings.EMAIL_DAILY_QUOTA_LIMIT - daily_sent),
        "token_expiry": cred.token_expiry.isoformat() if cred and cred.token_expiry else None,
    }


@router.get(
    "/auth-url",
    summary="Get Google OAuth Authorization URL",
    description="Generates a secure, CSRF-protected Google OAuth consent URL.",
)
def get_gmail_auth_url(
    request: Request,
    _admin: None = Depends(verify_admin),
) -> Dict[str, Any]:
    if not settings.has_gmail_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth credentials (GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET) are not configured.",
        )

    redirect_uri = _get_redirect_uri(request)
    state = generate_oauth_state()

    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {
        "success": True,
        "auth_url": auth_url,
        "state": state,
        "redirect_uri": redirect_uri,
    }


def _get_frontend_redirect_url(params: Optional[Dict[str, str]] = None) -> str:
    """Build redirect URL to frontend /automations page with safe parameters."""
    base = settings.frontend_base_url.rstrip("/")
    url = f"{base}/automations"
    if params:
        query_string = urllib.parse.urlencode(params)
        url = f"{url}?{query_string}"
    return url


@router.get(
    "/callback",
    summary="Google OAuth Callback Handler",
    description="Exchanges Google authorization code for access & refresh tokens, validates state, and persists encrypted credentials.",
)
def gmail_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    # 1. Handle OAuth error response from Google
    if error:
        logger.warning("Google OAuth callback error parameter: %s", error)
        safe_msg = "Google authorization was denied or cancelled."
        return RedirectResponse(
            url=_get_frontend_redirect_url({"gmail_error": safe_msg}),
            status_code=status.HTTP_302_FOUND,
        )

    # 2. Validate code and state presence
    if not code or not state:
        logger.warning("OAuth callback missing code or state")
        return RedirectResponse(
            url=_get_frontend_redirect_url({"gmail_error": "Missing authorization code or state parameter."}),
            status_code=status.HTTP_302_FOUND,
        )

    # 3. Validate state token (CSRF prevention)
    if not verify_oauth_state(state):
        logger.warning("OAuth state verification failed in callback")
        return RedirectResponse(
            url=_get_frontend_redirect_url({"gmail_error": "Invalid or expired OAuth state token (CSRF validation failed)."}),
            status_code=status.HTTP_302_FOUND,
        )

    redirect_uri = _get_redirect_uri(request)

    # 4. Exchange code for tokens
    payload = {
        "code": code,
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=15)
        if resp.status_code != 200:
            logger.error("Token exchange failed (%d): %s", resp.status_code, resp.text)
            return RedirectResponse(
                url=_get_frontend_redirect_url({"gmail_error": "Failed to exchange authorization code with Google."}),
                status_code=status.HTTP_302_FOUND,
            )
        token_data = resp.json()
    except requests.RequestException as e:
        logger.error("Token exchange network error: %s", str(e))
        return RedirectResponse(
            url=_get_frontend_redirect_url({"gmail_error": "Network error communicating with Google OAuth servers."}),
            status_code=status.HTTP_302_FOUND,
        )

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token:
        logger.error("Token response missing access_token")
        return RedirectResponse(
            url=_get_frontend_redirect_url({"gmail_error": "Google OAuth response did not contain an access token."}),
            status_code=status.HTTP_302_FOUND,
        )

    # 5. Query user profile to determine authenticated email
    try:
        userinfo_resp = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if userinfo_resp.status_code != 200:
            logger.error("Failed to retrieve user profile from Google: %d", userinfo_resp.status_code)
            return RedirectResponse(
                url=_get_frontend_redirect_url({"gmail_error": "Failed to retrieve authorized user email from Google."}),
                status_code=status.HTTP_302_FOUND,
            )
        userinfo = userinfo_resp.json()
        email_address = userinfo.get("email")
    except requests.RequestException as e:
        logger.error("Network error retrieving user profile: %s", str(e))
        return RedirectResponse(
            url=_get_frontend_redirect_url({"gmail_error": "Network error retrieving user profile from Google."}),
            status_code=status.HTTP_302_FOUND,
        )

    if not email_address:
        logger.error("Email address not found in Google profile response")
        return RedirectResponse(
            url=_get_frontend_redirect_url({"gmail_error": "Could not resolve email address from Google profile."}),
            status_code=status.HTTP_302_FOUND,
        )

    # 6. Encrypt and persist tokens in database
    now_utc = datetime.now(timezone.utc)
    naive_now = now_utc.replace(tzinfo=None)
    token_expiry = naive_now + timedelta(seconds=int(expires_in))
    today_utc = now_utc.strftime("%Y-%m-%d")

    # Deactivate existing active credentials
    db.query(GmailOAuthCredential).update({"is_active": False})

    # Find existing credential for this email or create new
    cred = (
        db.query(GmailOAuthCredential)
        .filter(GmailOAuthCredential.email_address == email_address)
        .first()
    )

    encrypted_access = encrypt_token(access_token)
    encrypted_refresh = encrypt_token(refresh_token) if refresh_token else (cred.encrypted_refresh_token if cred else "")

    if not encrypted_refresh:
        logger.warning("No refresh token received during OAuth callback for %s", email_address)

    if cred:
        cred.encrypted_access_token = encrypted_access
        if refresh_token:
            cred.encrypted_refresh_token = encrypted_refresh
        cred.token_expiry = token_expiry
        cred.scopes = " ".join(SCOPES)
        cred.is_active = True
        cred.daily_send_reset_date = today_utc
        cred.updated_at = naive_now
    else:
        cred = GmailOAuthCredential(
            email_address=email_address,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            token_expiry=token_expiry,
            scopes=" ".join(SCOPES),
            is_active=True,
            daily_send_count=0,
            daily_send_reset_date=today_utc,
            created_at=naive_now,
            updated_at=naive_now,
        )
        db.add(cred)

    db.commit()
    logger.info("Successfully linked Gmail account: %s", email_address)

    # 7. Redirect browser to Frontend Automations page
    target_url = _get_frontend_redirect_url({
        "gmail_connected": "true",
        "email": email_address,
    })
    return RedirectResponse(url=target_url, status_code=status.HTTP_302_FOUND)


@router.post(
    "/disconnect",
    summary="Disconnect Gmail Account",
    description="Deactivates connected Gmail OAuth credentials.",
)
def disconnect_gmail(
    db: Session = Depends(get_db),
    _admin: None = Depends(verify_admin),
) -> Dict[str, Any]:
    db.query(GmailOAuthCredential).update({"is_active": False})
    db.commit()
    logger.info("Gmail account disconnected by administrator.")
    return {
        "success": True,
        "message": "Gmail account disconnected successfully.",
    }


import re
from pydantic import BaseModel, Field
from typing import List
from providers.email_provider import get_email_provider
from services.template_engine import render_template


class GmailTestSendRequest(BaseModel):
    recipient_email: str = Field(..., description="Destination email address for test sending")
    template_grades: Optional[List[str]] = Field(default=None, description="Optional list of grades (legacy/backwards compatibility)")


class GmailTestItemResult(BaseModel):
    grade: str
    status: str  # "sent" | "failed" | "skipped"
    subject: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    sent_at: Optional[str] = None


class GmailTestSendResponse(BaseModel):
    success: bool
    recipient_email: str
    total: int
    sent: int
    failed: int
    skipped: int
    results: List[GmailTestItemResult]


@router.post(
    "/test-send",
    response_model=GmailTestSendResponse,
    summary="Send Test Email(s) via Gmail Provider",
    description="Sends test emails using the Universal Master Cold Email template to an entered recipient email using safe sample data.",
)
def gmail_test_send(
    payload: GmailTestSendRequest,
    db: Session = Depends(get_db),
    _admin: None = Depends(verify_admin),
) -> GmailTestSendResponse:
    from services.email_template_service import get_universal_master_template
    from services.template_engine import html_to_plain_text

    # 1. Validate entered email address format
    cleaned_email = (payload.recipient_email or "").strip()
    if (
        not cleaned_email
        or "\r" in cleaned_email
        or "\n" in cleaned_email
        or "," in cleaned_email
        or ";" in cleaned_email
        or not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", cleaned_email)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid test recipient email address format: '{cleaned_email[:50]}'",
        )

    if payload.template_grades is not None and len(payload.template_grades) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one template grade must be selected.",
        )

    # 2. Check provider connection if configured for Gmail
    if settings.EMAIL_PROVIDER == "gmail":
        active_cred = (
            db.query(GmailOAuthCredential)
            .filter(GmailOAuthCredential.is_active.is_(True))
            .first()
        )
        if not active_cred:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gmail account is not connected. Please connect your Gmail account first.",
            )

    provider = get_email_provider()

    # Load Universal Master Cold Email from server-side database/default
    master_tpl = get_universal_master_template(db)
    raw_subject = master_tpl.subject
    raw_body = master_tpl.body

    results: List[GmailTestItemResult] = []
    sent_count = 0
    failed_count = 0
    skipped_count = 0
    quota_exhausted = False

    # Determine targets (default to single universal test send)
    target_grades = payload.template_grades if (payload.template_grades and len(payload.template_grades) > 0) else ["Universal"]

    for grade_raw in target_grades:
        grade = grade_raw.strip()
        if not grade:
            continue

        # If daily quota already exhausted during previous template in this loop, skip remainder
        if quota_exhausted:
            results.append(
                GmailTestItemResult(
                    grade=grade,
                    status="skipped",
                    subject=f"[TEST] {raw_subject}",
                    message_id=None,
                    error=f"Daily sending limit reached ({settings.EMAIL_DAILY_QUOTA_LIMIT}/{settings.EMAIL_DAILY_QUOTA_LIMIT} sent today). Quota resets at 00:00 UTC.",
                    sent_at=None,
                )
            )
            skipped_count += 1
            continue

        # Assemble safe sample data
        context = {
            "business_name": "Test Business",
            "contact_name": "Test Contact",
            "email": cleaned_email,
            "phone": "+91 9876543210",
            "website": "https://example.com",
            "lead_status": "new",
            "lead_score": "85",
            "follow_up_title": "Test Follow-up",
            "follow_up_due_at": "Tomorrow at 10:00 AM",
        }

        rendered_subject = render_template(raw_subject, context, escape_html=False)
        rendered_html = render_template(raw_body, context, escape_html=True)
        plain_text = html_to_plain_text(rendered_html)

        prefix = f"[TEST - Grade {grade}]" if grade in {"A", "B", "C", "D"} else "[TEST]"
        test_subject = f"{prefix} {rendered_subject}"

        now_str = datetime.now(timezone.utc).isoformat()

        # Dispatch via configured provider with db session attached
        send_result = provider.send_email(
            to_email=cleaned_email,
            subject=test_subject,
            html_content=rendered_html,
            text_content=plain_text,
            metadata={"db": db, "is_test": True, "grade": grade},
        )

        if send_result.success:
            results.append(
                GmailTestItemResult(
                    grade=grade,
                    status="sent",
                    subject=test_subject,
                    message_id=send_result.message_id,
                    error=None,
                    sent_at=now_str,
                )
            )
            sent_count += 1
        else:
            if send_result.error and "Daily sending limit reached" in send_result.error:
                quota_exhausted = True
                results.append(
                    GmailTestItemResult(
                        grade=grade,
                        status="skipped",
                        subject=test_subject,
                        message_id=None,
                        error=send_result.error,
                        sent_at=None,
                    )
                )
                skipped_count += 1
            else:
                results.append(
                    GmailTestItemResult(
                        grade=grade,
                        status="failed",
                        subject=test_subject,
                        message_id=None,
                        error=send_result.error,
                        sent_at=None,
                    )
                )
                failed_count += 1

    return GmailTestSendResponse(
        success=sent_count > 0 or (failed_count == 0 and skipped_count == 0),
        recipient_email=cleaned_email,
        total=len(results),
        sent=sent_count,
        failed=failed_count,
        skipped=skipped_count,
        results=results,
    )

