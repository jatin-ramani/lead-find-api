import pytest
from fastapi.testclient import TestClient
from config import settings
from errors import ErrorCode


def test_public_health_endpoint_without_credentials(unauth_client: TestClient):
    """GET /health must remain public and return 200 OK."""
    response = unauth_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "database" in data


def test_public_version_endpoint_without_credentials(unauth_client: TestClient):
    """GET /version must remain public and return 200 OK."""
    response = unauth_client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == settings.APP_NAME
    assert data["version"] == settings.APP_VERSION


def test_public_root_endpoint_without_credentials(unauth_client: TestClient):
    """GET / must remain public."""
    response = unauth_client.get("/")
    assert response.status_code == 200


def test_protected_system_endpoint_without_credentials(unauth_client: TestClient):
    """GET /system reveals runtime details and must require auth (401)."""
    response = unauth_client.get("/system")
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"] == ErrorCode.UNAUTHORIZED
    assert data["message"] == "Authentication required"
    assert "requestId" in data


def test_protected_businesses_endpoint_without_credentials(unauth_client: TestClient):
    """GET /businesses without credentials returns 401."""
    response = unauth_client.get("/businesses")
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"] == ErrorCode.UNAUTHORIZED


def test_protected_delete_business_without_credentials(unauth_client: TestClient):
    """DELETE /businesses/1 without credentials returns 401."""
    response = unauth_client.delete("/businesses/1")
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"] == ErrorCode.UNAUTHORIZED


def test_protected_scan_without_credentials(unauth_client: TestClient):
    """POST /scan without credentials returns 401."""
    response = unauth_client.post("/scan", json={"city": "Ahmedabad", "category": "catering"})
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"] == ErrorCode.UNAUTHORIZED


def test_protected_scrape_without_credentials(unauth_client: TestClient):
    """POST /scrape/all without credentials returns 401."""
    response = unauth_client.post("/scrape/all")
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"] == ErrorCode.UNAUTHORIZED


def test_login_with_invalid_credentials(unauth_client: TestClient):
    """POST /auth/login with wrong secret returns 401."""
    response = unauth_client.post("/auth/login", json={"secret": "wrong_secret_key_123"})
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"] == ErrorCode.UNAUTHORIZED
    assert data["message"] == "Invalid authentication credentials"


def test_login_with_empty_credentials(unauth_client: TestClient):
    """POST /auth/login with empty secret returns 401."""
    response = unauth_client.post("/auth/login", json={"secret": "   "})
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"] == ErrorCode.UNAUTHORIZED


def test_login_with_valid_credentials_sets_cookie(unauth_client: TestClient):
    """POST /auth/login with valid secret succeeds and sets session cookie."""
    response = unauth_client.post("/auth/login", json={"secret": settings.admin_secret})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "leadfinder_session" in response.cookies
    assert response.cookies["leadfinder_session"] == settings.admin_secret


def test_authenticated_request_with_bearer_token(unauth_client: TestClient):
    """Authorization: Bearer <secret> allows access to protected endpoints."""
    headers = {"Authorization": f"Bearer {settings.admin_secret}"}
    response = unauth_client.get("/businesses", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


def test_authenticated_request_with_session_cookie(unauth_client: TestClient):
    """leadfinder_session cookie allows access to protected endpoints."""
    unauth_client.cookies.set("leadfinder_session", settings.admin_secret)
    response = unauth_client.get("/system")
    assert response.status_code == 200
    data = response.json()
    assert "pythonVersion" in data
    assert "database" in data


def test_auth_me_endpoint(unauth_client: TestClient):
    """GET /auth/me verifies active authentication status."""
    # Unauthenticated
    resp_unauth = unauth_client.get("/auth/me")
    assert resp_unauth.status_code == 401

    # Authenticated
    headers = {"Authorization": f"Bearer {settings.admin_secret}"}
    resp_auth = unauth_client.get("/auth/me", headers=headers)
    assert resp_auth.status_code == 200
    assert resp_auth.json() == {"authenticated": True}


def test_logout_clears_cookie(unauth_client: TestClient):
    """POST /auth/logout clears the session cookie."""
    unauth_client.cookies.set("leadfinder_session", settings.admin_secret)
    resp = unauth_client.post("/auth/logout")
    assert resp.status_code == 200
    # Cookie value set to empty or deleted
    assert resp.cookies.get("leadfinder_session") == "" or "leadfinder_session" not in resp.cookies


def test_secret_redaction_in_error_responses(unauth_client: TestClient):
    """Authentication secret never appears in error response envelopes."""
    response = unauth_client.get("/businesses", headers={"Authorization": "Bearer invalid_secret"})
    assert response.status_code == 401
    content = response.text
    assert settings.admin_secret not in content
    assert "invalid_secret" not in content
