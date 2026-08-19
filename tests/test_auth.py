import pytest
from fastapi.testclient import TestClient
from datetime import timedelta

from config import Environment, settings
from database.auth import hash_session_token, utcnow
from database.models import AdminSession
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


def test_login_with_valid_credentials_sets_opaque_persisted_cookie(unauth_client: TestClient, db):
    """Browser receives a random token while the database stores only its hash."""
    response = unauth_client.post("/auth/login", json={"secret": settings.admin_secret})
    assert response.status_code == 200
    assert response.json()["success"] is True
    token = response.cookies["leadfinder_session"]
    assert token and token != settings.admin_secret
    assert settings.admin_secret not in response.headers["set-cookie"]
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie
    assert "path=/" in set_cookie
    assert "domain=" not in set_cookie
    assert f"max-age={settings.SESSION_TTL_SECONDS}" in set_cookie
    session = db.get(AdminSession, hash_session_token(token))
    assert session is not None
    assert session.created_at < session.expires_at
    assert db.query(AdminSession).filter(AdminSession.token_hash == token).first() is None


def test_production_cookie_allows_cross_site_credentialed_requests(
    unauth_client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)

    login = unauth_client.post(
        "/auth/login", json={"secret": settings.admin_secret}
    )

    assert login.status_code == 200
    set_cookie = login.headers["set-cookie"].lower()
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie
    assert "httponly" in set_cookie
    assert "path=/" in set_cookie
    assert "domain=" not in set_cookie
    assert settings.admin_secret not in login.headers["set-cookie"]

    token = login.cookies["leadfinder_session"]
    cookie_header = {"Cookie": f"leadfinder_session={token}"}
    assert unauth_client.get("/auth/me", headers=cookie_header).status_code == 200

    logout = unauth_client.post("/auth/logout", headers=cookie_header)

    assert logout.status_code == 200
    clear_cookie = logout.headers["set-cookie"].lower()
    assert "samesite=none" in clear_cookie
    assert "secure" in clear_cookie
    assert "httponly" in clear_cookie
    assert "path=/" in clear_cookie
    assert unauth_client.get("/auth/me", headers=cookie_header).status_code == 401


def test_authenticated_request_with_bearer_token(unauth_client: TestClient):
    """Authorization: Bearer <secret> allows access to protected endpoints."""
    headers = {"Authorization": f"Bearer {settings.admin_secret}"}
    response = unauth_client.get("/businesses", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


def test_authenticated_request_with_session_cookie(unauth_client: TestClient):
    login = unauth_client.post("/auth/login", json={"secret": settings.admin_secret})
    assert login.status_code == 200
    response = unauth_client.get("/system")
    assert response.status_code == 200
    assert "pythonVersion" in response.json()


def test_admin_secret_is_rejected_as_a_browser_cookie(unauth_client: TestClient):
    unauth_client.cookies.set("leadfinder_session", settings.admin_secret)
    assert unauth_client.get("/system").status_code == 401


def test_expired_session_is_rejected_and_removed(unauth_client: TestClient, db):
    token = "expired-opaque-session"
    db.add(AdminSession(
        token_hash=hash_session_token(token),
        created_at=utcnow() - timedelta(days=2),
        expires_at=utcnow() - timedelta(days=1),
    ))
    db.commit()
    unauth_client.cookies.set("leadfinder_session", token)
    assert unauth_client.get("/system").status_code == 401
    db.expire_all()
    assert db.get(AdminSession, hash_session_token(token)) is None


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


def test_logout_clears_and_invalidates_session(unauth_client: TestClient):
    """A copied token cannot be reused after logout."""
    login = unauth_client.post("/auth/login", json={"secret": settings.admin_secret})
    token = login.cookies["leadfinder_session"]
    resp = unauth_client.post("/auth/logout")
    assert resp.status_code == 200
    assert resp.cookies.get("leadfinder_session") == "" or "leadfinder_session" not in resp.cookies
    unauth_client.cookies.set("leadfinder_session", token)
    assert unauth_client.get("/auth/me").status_code == 401


def test_secret_redaction_in_error_responses(unauth_client: TestClient):
    """Authentication secret never appears in error response envelopes."""
    response = unauth_client.get("/businesses", headers={"Authorization": "Bearer invalid_secret"})
    assert response.status_code == 401
    content = response.text
    assert settings.admin_secret not in content
    assert "invalid_secret" not in content
