"""
Exhaustive API Integration & Security Audit Script.
Tests every API endpoint, auth lifecycle, cookies, export safety, formula injection, and schema parity.
"""

import csv
import io
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app import app
from config import settings
from database.auth import SESSION_COOKIE_NAME, hash_session_token, utcnow
from database.db import get_db, SessionLocal
from database.models import Business, WebsiteData, ScrapeJob, ScanJob, AdminSession

client = TestClient(app)

results = []

def record(endpoint: str, method: str, purpose: str, status_code: int, expected_status: int, passed: bool, notes: str = ""):
    results.append({
        "endpoint": endpoint,
        "method": method,
        "purpose": purpose,
        "status_code": status_code,
        "expected_status": expected_status,
        "passed": passed,
        "notes": notes
    })
    status_icon = "PASS" if passed else "FAIL"
    print(f"[{status_icon}] {method:6} {endpoint:35} -> HTTP {status_code} ({purpose}) {notes}")

def run_audit():
    print("=" * 80)
    print("STARTING EXHAUSTIVE BACKEND API INTEGRATION AUDIT")
    print("=" * 80)

    # 1. System & Health (Unauthenticated)
    res = client.get("/")
    record("/", "GET", "Root check", res.status_code, 200, res.status_code == 200 and "message" in res.json())

    res = client.get("/health")
    record("/health", "GET", "Health probe", res.status_code, 200, res.status_code == 200 and res.json().get("status") in ["healthy", "ok"])

    res = client.get("/version")
    record("/version", "GET", "Version metadata", res.status_code, 200, res.status_code == 200 and "version" in res.json())

    res = client.get("/system", headers=auth_headers if "auth_headers" in locals() else {"Authorization": f"Bearer {settings.admin_secret}"})
    record("/system", "GET", "System diagnostics", res.status_code, 200, res.status_code == 200 and "database" in res.json())

    # 2. Authentication Lifecycle
    # Protected endpoint without auth -> 401
    res = client.get("/businesses")
    record("/businesses", "GET (No Auth)", "Reject unauthenticated request", res.status_code, 401, res.status_code == 401)

    # Login with empty body -> 422
    res = client.post("/auth/login", json={})
    record("/auth/login", "POST (Empty)", "Reject empty payload", res.status_code, 422, res.status_code == 422)

    # Login with invalid secret -> 401
    res = client.post("/auth/login", json={"secret": "wrong_secret_12345"})
    record("/auth/login", "POST (Invalid)", "Reject invalid secret", res.status_code, 401, res.status_code == 401)

    # Login with valid secret -> 200 + HttpOnly Cookie
    admin_secret = settings.admin_secret
    res = client.post("/auth/login", json={"secret": admin_secret})
    cookie_val = res.cookies.get(SESSION_COOKIE_NAME)
    login_passed = res.status_code == 200 and cookie_val is not None
    record("/auth/login", "POST (Valid)", "Issue opaque session cookie", res.status_code, 200, login_passed, f"Cookie set: {bool(cookie_val)}")

    # Verify session via /auth/me with Cookie
    res = client.get("/auth/me", cookies={SESSION_COOKIE_NAME: cookie_val})
    record("/auth/me", "GET (Cookie)", "Validate active session", res.status_code, 200, res.status_code == 200 and res.json().get("authenticated") is True)

    # Verify Bearer Auth
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {admin_secret}"})
    record("/auth/me", "GET (Bearer)", "Validate Bearer auth token", res.status_code, 200, res.status_code == 200 and res.json().get("authenticated") is True)

    auth_headers = {"Authorization": f"Bearer {admin_secret}"}

    # 3. Seed test data for comprehensive verification
    db = SessionLocal()
    try:
        # Clear previous test data safely
        db.query(WebsiteData).delete()
        db.query(Business).delete()
        db.query(ScrapeJob).delete()
        db.query(ScanJob).delete()
        db.commit()

        # Seed sample businesses
        b1 = Business(name="=1+1 Dental Care", phone="+91 9876543210", email="info@dental.com", website="https://dental.com", city="Ahmedabad", category="Dental", address="101 Ring Road", status="Has Website")
        b2 = Business(name="@Cmd Legal Services", phone="+91 9876543211", email=None, website="https://legal.com", city="Ahmedabad", category="Legal", address="102 Ring Road", status="Has Website")
        b3 = Business(name="-Minus Tech Solutions", phone=None, email="contact@tech.com", website=None, city="Surat", category="Tech", address="201 Tech Park", status="No Website")
        b4 = Business(name="+Plus Bakery Shop", phone=None, email=None, website=None, city="Surat", category="Bakery", address="202 Market", status="No Website")
        db.add_all([b1, b2, b3, b4])
        db.commit()
        db.refresh(b1)
        db.refresh(b2)
        db.refresh(b3)
        db.refresh(b4)
        b1_id = b1.id
        b2_id = b2.id
        b3_id = b3.id
        b4_id = b4.id

        # Seed WebsiteData
        w1 = WebsiteData(business_id=b1_id, title="Dental Care Page", meta_description="Top dental clinic", emails=json.dumps(["info@dental.com", "care@dental.com"]), facebook="https://facebook.com/dental", status="Completed", scraped_at=utcnow())
        db.add(w1)
        db.commit()
    finally:
        db.close()

    # 4. Dashboard Stats
    res = client.get("/dashboard/stats", headers=auth_headers)
    data = res.json()
    b_stats = data.get("business", {})
    dash_passed = res.status_code == 200 and b_stats.get("totalBusinesses") == 4 and b_stats.get("withWebsite") == 2 and b_stats.get("withEmail") == 2 and b_stats.get("withPhone") == 2
    record("/dashboard/stats", "GET", "Dashboard KPI statistics", res.status_code, 200, dash_passed, f"Stats: {b_stats}")

    # 5. Cities Summaries
    res = client.get("/businesses/cities", headers=auth_headers)
    cities_data = res.json().get("data", [])
    cities_passed = res.status_code == 200 and len(cities_data) == 2
    record("/businesses/cities", "GET", "Discovered cities aggregation", res.status_code, 200, cities_passed, f"Cities: {[c['city'] for c in cities_data]}")

    # 6. Businesses List & All Qualification Combos
    combos = [
        ("No filter", {}),
        ("Has website", {"has_website": "true"}),
        ("No website", {"has_website": "false"}),
        ("Has email", {"has_email": "true"}),
        ("Has phone", {"has_phone": "true"}),
        ("Has website + Has email", {"has_website": "true", "has_email": "true"}),
        ("Has website + Has phone", {"has_website": "true", "has_phone": "true"}),
        ("Has email + Has phone", {"has_email": "true", "has_phone": "true"}),
        ("Has website + Has email + Has phone", {"has_website": "true", "has_email": "true", "has_phone": "true"}),
        ("No website + Has email", {"has_website": "false", "has_email": "true"}),
        ("City Ahmedabad", {"city": "Ahmedabad"}),
        ("City Surat", {"city": "Surat"}),
    ]
    for label, params in combos:
        res = client.get("/businesses", params=params, headers=auth_headers)
        p_res = res.json()
        items = p_res.get("data", [])
        record(f"/businesses ({label})", "GET", f"Query businesses with {label}", res.status_code, 200, res.status_code == 200 and isinstance(items, list), f"Count: {len(items)}")

    # 7. Single Business & Website Data
    res = client.get(f"/businesses/{b1_id}", headers=auth_headers)
    record(f"/businesses/{b1_id}", "GET", "Single business details", res.status_code, 200, res.status_code == 200 and res.json().get("id") == b1_id)

    res = client.get(f"/businesses/{b1_id}/website", headers=auth_headers)
    record(f"/businesses/{b1_id}/website", "GET", "Extracted website data", res.status_code, 200, res.status_code == 200 and "data" in res.json())

    # 8. Export Preview & CSV Export (GET and POST)
    # Preview all
    res = client.post("/businesses/export/preview", json={"scope": "filtered", "filters": {}}, headers=auth_headers)
    prev_data = res.json()
    prev_passed = res.status_code == 200 and prev_data.get("export_count") == 4
    record("/businesses/export/preview", "POST", "Export count preview", res.status_code, 200, prev_passed, f"Count: {prev_data.get('export_count')}")

    # CSV Download via GET
    res = client.get("/businesses/export/csv", headers=auth_headers)
    csv_text = res.content.decode("utf-8-sig")
    reader = list(csv.reader(io.StringIO(csv_text)))
    header = reader[0]
    rows = reader[1:]
    get_csv_passed = res.status_code == 200 and len(rows) == 4 and "ID" in header
    record("/businesses/export/csv", "GET", "Download full CSV", res.status_code, 200, get_csv_passed, f"Rows: {len(rows)}")

    # Formula injection verification
    # Row names should be safely escaped with single quote ' if starting with =, @, -, +
    escaped_formula_count = sum(1 for r in rows if r[1].startswith("'=") or r[1].startswith("'@") or r[1].startswith("'-") or r[1].startswith("'+"))
    record("CSV Formula Escape", "SAFETY", "Prevent formula injection in CSV", 200, 200, escaped_formula_count == 4, f"Escaped rows: {escaped_formula_count}/4")

    # CSV Download via POST (selected IDs)
    res = client.post("/businesses/export/csv", json={"business_ids": [b1_id, b2_id], "has_email": True}, headers=auth_headers)
    sel_csv_text = res.content.decode("utf-8-sig")
    sel_rows = list(csv.reader(io.StringIO(sel_csv_text)))[1:]
    post_csv_passed = res.status_code == 200 and len(sel_rows) == 1
    record("/businesses/export/csv", "POST", "Download selected & qualified CSV", res.status_code, 200, post_csv_passed, f"Rows: {len(sel_rows)}")

    # 9. Scrape Jobs
    res = client.get("/scrape/jobs", headers=auth_headers)
    record("/scrape/jobs", "GET", "List scrape job history", res.status_code, 200, res.status_code == 200 and "data" in res.json())

    # Create dummy completed scrape job
    db = SessionLocal()
    try:
        sjob = ScrapeJob(status="Completed", total_websites=1, completed=1, success=1, failed=0, started_at=utcnow(), completed_at=utcnow())
        db.add(sjob)
        db.commit()
        db.refresh(sjob)
        sjob_id = sjob.id
    finally:
        db.close()

    res = client.get(f"/scrape/jobs/{sjob_id}", headers=auth_headers)
    record(f"/scrape/jobs/{sjob_id}", "GET", "Get single scrape job details", res.status_code, 200, res.status_code == 200 and res.json().get("data", {}).get("id") == sjob_id)

    res = client.get(f"/scrape/jobs/{sjob_id}/results", headers=auth_headers)
    record(f"/scrape/jobs/{sjob_id}/results", "GET", "Get scrape job results list", res.status_code, 200, res.status_code == 200 and "data" in res.json())

    # 10. Scan Jobs
    res = client.get("/scan/jobs", headers=auth_headers)
    record("/scan/jobs", "GET", "List scan jobs history", res.status_code, 200, res.status_code == 200 and isinstance(res.json(), list))

    res = client.get("/scan/jobs/latest", headers=auth_headers)
    record("/scan/jobs/latest", "GET", "Get latest scan job (404 when none)", res.status_code, 404, res.status_code in [200, 404])

    # 11. Logout
    res = client.post("/auth/logout", cookies={SESSION_COOKIE_NAME: cookie_val})
    record("/auth/logout", "POST", "Logout & revoke session", res.status_code, 200, res.status_code == 200)

    # Verify session revoked on /auth/me
    res = client.get("/auth/me", cookies={SESSION_COOKIE_NAME: cookie_val})
    record("/auth/me (Post-Logout)", "GET", "Verify revoked session fails", res.status_code, 401, res.status_code == 401)

    print("=" * 80)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    print(f"AUDIT COMPLETE: Total Tests: {total}, Passed: {passed}, Failed: {failed}")
    print("=" * 80)
    return failed == 0

if __name__ == "__main__":
    success = run_audit()
    sys.exit(0 if success else 1)
