import secrets
from typing import Optional
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from config import settings
from database.auth import SESSION_COOKIE_NAME, verify_admin
from errors import AppError, ErrorCode

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


class LoginRequest(BaseModel):
    secret: str


@router.post(
    "/login",
    summary="Authenticate admin session",
    description="Validates the admin secret key and sets an HttpOnly session cookie.",
    status_code=status.HTTP_200_OK,
)
def login(payload: LoginRequest, response: Response):
    if not payload.secret or not secrets.compare_digest(payload.secret.strip(), settings.admin_secret):
        raise AppError(
            message="Invalid authentication credentials",
            status_code=401,
            error=ErrorCode.UNAUTHORIZED,
        )

    # Set HttpOnly, SameSite=Lax cookie on response
    is_secure = settings.is_production
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=settings.admin_secret,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        max_age=86400 * 7,  # 7 days
        path="/",
    )

    return {
        "success": True,
        "message": "Authenticated successfully.",
    }


@router.post(
    "/logout",
    summary="Clear admin session",
    description="Clears the HttpOnly session cookie.",
    status_code=status.HTTP_200_OK,
)
def logout(response: Response):
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return {
        "success": True,
        "message": "Logged out successfully.",
    }


@router.get(
    "/me",
    summary="Verify admin session status",
    description="Returns 200 if active admin session is valid, 401 otherwise.",
    dependencies=[Depends(verify_admin)],
    status_code=status.HTTP_200_OK,
)
def auth_me():
    return {
        "authenticated": True,
    }
