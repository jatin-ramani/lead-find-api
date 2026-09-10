"""Tests for durable background continuous city scanning service."""

import pytest
from sqlalchemy import func

from database.models import (
    AdminSession,
    Business,
    EmailTemplate,
    GmailOAuthCredential,
    ScanJob,
    ScanSearchUnit,
)
from services.contact_validator import evaluate_lead_contacts
from services.scan_worker import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PAUSED,
    STATUS_RUNNING,
    _execute_search_unit,
    cancel_scan_job,
    clear_all_scanned_leads,
    get_scan_status_detail,
    pause_scan_job,
    recover_stale_scans_on_startup,
    resume_scan_job,
    start_continuous_scan,
)


def test_contact_validator_email_or_phone_rule():
    """Verify strict contact rule: storable ONLY if email OR phone is present."""
    # Has phone only -> True
    storable, email, phone = evaluate_lead_contacts(None, "+1-555-0199")
    assert storable is True
    assert phone == "+15550199"
    assert email is None

    # Has email only -> True
    storable, email, phone = evaluate_lead_contacts("contact@dental.com", None)
    assert storable is True
    assert email == "contact@dental.com"
    assert phone is None

    # Has both -> True
    storable, email, phone = evaluate_lead_contacts("dr@clinic.com", "9876543210")
    assert storable is True

    # Has neither -> False (SKIP!)
    storable, email, phone = evaluate_lead_contacts(None, None)
    assert storable is False

    # Invalid email / invalid phone -> False
    storable, email, phone = evaluate_lead_contacts("not-an-email", "123")
    assert storable is False


def test_start_continuous_scan_creates_job_and_search_units(db, monkeypatch):
    """Launching continuous scan creates ScanJob and corresponding ScanSearchUnits."""
    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(
        db=db,
        city="Ahmedabad",
        category_or_family="healthcare",
        radius_km=15.0,
    )

    assert job.id is not None
    assert job.city == "Ahmedabad"
    assert job.category == "Healthcare & Medical"
    assert job.scan_radius_km == 15
    assert job.total_cells > 0
    assert job.total_search_units > 0

    units = db.query(ScanSearchUnit).filter(ScanSearchUnit.scan_job_id == job.id).all()
    assert len(units) == job.total_search_units
    assert all(u.status == "pending" for u in units)


def test_execute_search_unit_stores_leads_with_contact_and_skips_no_contact(db, monkeypatch):
    """Search unit execution must store leads with contacts and skip places with no contact."""
    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(
        db=db,
        city="Ahmedabad",
        category_or_family="catering",
        radius_km=5.0,
    )

    unit = db.query(ScanSearchUnit).filter(ScanSearchUnit.scan_job_id == job.id).first()

    # Mock Geoapify returns: 1 with phone, 1 with email, 1 without any contact
    mock_features = [
        {
            "properties": {
                "name": "Cafe Alpha",
                "place_id": "cafe_alpha_1",
                "formatted": "123 Main St",
                "website": "https://cafealpha.com",
                "contact": {"phone": "+1-555-1111"},
            }
        },
        {
            "properties": {
                "name": "Bakery Beta",
                "place_id": "bakery_beta_1",
                "formatted": "456 Side Ave",
                "contact": {"email": "hello@bakerybeta.com"},
            }
        },
        {
            "properties": {
                "name": "Unnamed Food Stall (No Contact)",
                "place_id": "food_stall_no_contact",
                "formatted": "Corner St",
                "contact": {},
            }
        },
    ]

    monkeypatch.setattr("services.scan_worker.fetch_places_page", lambda **kwargs: mock_features)

    _execute_search_unit(db, job, unit)

    # Stored: 2, Skipped: 1
    assert unit.stored_count == 2
    assert unit.skipped_no_contact_count == 1
    assert unit.results_count == 3
    assert unit.status == "completed"

    assert job.businesses_stored == 2
    assert job.businesses_skipped_no_contact == 1

    # Verify rows in DB
    b1 = db.query(Business).filter(Business.place_id == "cafe_alpha_1").first()
    assert b1 is not None
    assert b1.phone == "+15551111"
    assert b1.status == "Has Website"

    b2 = db.query(Business).filter(Business.place_id == "bakery_beta_1").first()
    assert b2 is not None
    assert b2.email == "hello@bakerybeta.com"
    assert b2.status == "No Website"

    # No contact place must NOT be in DB
    no_contact_b = db.query(Business).filter(Business.place_id == "food_stall_no_contact").first()
    assert no_contact_b is None


def test_deduplication_and_intelligent_enrichment(db, monkeypatch):
    """If a duplicate place is found, enrich missing contact info without duplicate rows."""
    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(db=db, city="Ahmedabad", category_or_family="healthcare", radius_km=5.0)
    unit = db.query(ScanSearchUnit).filter(ScanSearchUnit.scan_job_id == job.id).first()

    # Create pre-existing business with only phone
    existing = Business(
        name="Smile Dental",
        phone="+15559999",
        email=None,
        website=None,
        city="Ahmedabad",
        category="healthcare.dentist",
        place_id="smile_dental_place",
        status="No Website",
    )
    db.add(existing)
    db.commit()

    # Search unit finds duplicate place with new email and website
    mock_features = [
        {
            "properties": {
                "name": "Smile Dental Care",
                "place_id": "smile_dental_place",
                "website": "https://smiledental.com",
                "contact": {"email": "contact@smiledental.com", "phone": "+1-555-9999"},
            }
        }
    ]

    monkeypatch.setattr("services.scan_worker.fetch_places_page", lambda **kwargs: mock_features)
    _execute_search_unit(db, job, unit)

    # Should count as duplicate and enrich existing record
    assert unit.duplicates_count == 1
    assert unit.stored_count == 0

    db.refresh(existing)
    assert existing.email == "contact@smiledental.com"
    assert existing.website == "https://smiledental.com"
    assert existing.status == "Has Website"


def test_pause_resume_cancel_controls(db, monkeypatch):
    """Verify pause, resume, and cancel state transitions."""
    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(db=db, city="Ahmedabad", category_or_family="service", radius_km=5.0)

    # Pause
    paused = pause_scan_job(db, job.id)
    assert paused.status == STATUS_PAUSED
    assert paused.paused_at is not None

    # Resume
    resumed = resume_scan_job(db, job.id)
    assert resumed.status == STATUS_RUNNING
    assert resumed.paused_at is None

    # Cancel
    cancelled = cancel_scan_job(db, job.id)
    assert cancelled.status == STATUS_CANCELLED


def test_clear_all_scanned_leads_safety(db, monkeypatch):
    """
    Verify clear_all_scanned_leads:
    1. Deletes businesses and associated activities.
    2. Strictly PRESERVES admin sessions, templates, automations, campaigns, and Gmail credentials.
    """
    from datetime import datetime, timezone, timedelta
    from database.models import EmailAutomation, EmailCampaign

    now = datetime.now(timezone.utc)

    # Insert template, session, credential, campaign, automation, business
    template = EmailTemplate(name="Test Pitch", subject="Pitch", body="<p>Body</p>")
    db.add(template)
    db.flush()

    campaign = EmailCampaign(
        name="Historical Campaign",
        template_id=template.id,
        status="completed",
        sent_count=25,
        recipient_count=25,
    )
    db.add(campaign)

    automation = EmailAutomation(
        name="New Lead Pitch",
        trigger_type="lead_created",
        subject_template="Hello",
        body_template="World",
    )
    db.add(automation)

    session = AdminSession(token_hash="hash_123", created_at=now, expires_at=now + timedelta(days=1))
    db.add(session)

    cred = GmailOAuthCredential(
        email_address="admin@agency.com",
        encrypted_access_token="enc_token",
        encrypted_refresh_token="enc_refresh",
        token_expiry=now + timedelta(days=1),
        scopes="https://mail.google.com/",
        daily_send_reset_date="2026-09-10",
    )
    db.add(cred)

    biz = Business(name="Biz to delete", city="Ahmedabad", phone="1234567890", status="No Website")
    db.add(biz)
    db.commit()

    # Clear leads with confirm=True
    deleted = clear_all_scanned_leads(db, confirmation=True)
    assert deleted >= 1

    # Businesses deleted
    assert db.query(Business).count() == 0

    # Critical entities preserved!
    assert db.query(EmailTemplate).filter(EmailTemplate.name == "Test Pitch").first() is not None
    assert db.query(EmailCampaign).filter(EmailCampaign.name == "Historical Campaign").first() is not None
    assert db.query(EmailAutomation).filter(EmailAutomation.name == "New Lead Pitch").first() is not None
    assert db.query(AdminSession).filter(AdminSession.token_hash == "hash_123").first() is not None
    assert db.query(GmailOAuthCredential).filter(GmailOAuthCredential.email_address == "admin@agency.com").first() is not None





def test_recover_stale_scans_on_startup(db, monkeypatch):
    """Scans left in 'Running' state are converted to 'Paused' on startup."""
    job = ScanJob(
        city="Ahmedabad",
        category="Healthcare",
        status=STATUS_RUNNING,
        progress=45,
    )
    db.add(job)
    db.commit()

    recovered = recover_stale_scans_on_startup(db)
    assert recovered == 1

    db.refresh(job)
    assert job.status == STATUS_PAUSED


def test_scanner_processes_more_than_500_leads_regression(db, monkeypatch):
    """
    REGRESSION TEST: Prove that the scanner does NOT terminate or cap at 500 results.
    A multi-cell scan with 700 places must process all search units and store > 500 leads.
    """
    import services.scan_worker as worker_mod

    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: worker_mod._run_scan_job_worker(job_id))

    # Generate 100 features per search unit across 7 units = 700 total places
    call_idx = {"count": 0}

    def mock_fetch(category, latitude, longitude, radius, limit=100):
        call_idx["count"] += 1
        return [
            {
                "properties": {
                    "name": f"Clinic {call_idx['count']}_{i}",
                    "place_id": f"place_{call_idx['count']}_{i}",
                    "contact": {"phone": f"+1555{call_idx['count']:02d}{i:04d}"},
                }
            }
            for i in range(100)
        ]

    monkeypatch.setattr("services.scan_worker.fetch_places_page", mock_fetch)

    job = start_continuous_scan(
        db=db,
        city="Ahmedabad",
        category_or_family="healthcare",
        radius_km=5.0,
    )

    db.expire_all()
    refreshed_job = db.query(ScanJob).filter(ScanJob.id == job.id).first()

    assert refreshed_job.status == "Completed"
    assert refreshed_job.businesses_found >= 700
    assert refreshed_job.businesses_stored >= 700
    assert refreshed_job.businesses_stored > 500
    assert db.query(Business).count() >= 700


def test_scanner_counter_invariance_and_safe_enrichment(db, monkeypatch):
    """
    Verify counter invariance:
    found == stored + skipped_no_contact + duplicates
    And verify multi-field enrichment without overwriting non-empty fields.
    """
    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(db=db, city="Mumbai", category_or_family="catering", radius_km=5.0)
    unit = db.query(ScanSearchUnit).filter(ScanSearchUnit.scan_job_id == job.id).first()

    # Create initial business with phone only
    b_initial = Business(
        name="Royal Diner",
        city="Mumbai",
        phone="+919876543210",
        email=None,
        website=None,
        status="No Website",
        place_id="royal_diner_pid",
    )
    db.add(b_initial)
    db.commit()

    # Search returns:
    # 1. Royal Diner duplicate with email and website (should enrich, duplicate +1)
    # 2. New place with valid phone (should store, stored +1)
    # 3. New place with valid email (should store, stored +1)
    # 4. Place with NO phone and NO email (should skip, skipped +1)
    # Total = 4
    features = [
        {
            "properties": {
                "name": "Royal Diner",
                "place_id": "royal_diner_pid",
                "website": "https://royaldiner.in",
                "contact": {"email": "info@royaldiner.in"},
            }
        },
        {
            "properties": {
                "name": "Green Salad Bar",
                "place_id": "salad_bar_pid",
                "contact": {"phone": "+919876543211"},
            }
        },
        {
            "properties": {
                "name": "Spice Route",
                "place_id": "spice_route_pid",
                "contact": {"email": "contact@spiceroute.in"},
            }
        },
        {
            "properties": {
                "name": "Street Kiosk",
                "place_id": "kiosk_pid",
                "contact": {},
            }
        },
    ]

    monkeypatch.setattr("services.scan_worker.fetch_places_page", lambda **kwargs: features)

    _execute_search_unit(db, job, unit)

    # Verify unit counts
    assert unit.results_count == 4
    assert unit.stored_count == 2
    assert unit.skipped_no_contact_count == 1
    assert unit.duplicates_count == 1
    # Strict counter invariance
    assert unit.results_count == unit.stored_count + unit.skipped_no_contact_count + unit.duplicates_count

    # Verify job counts
    assert job.businesses_found == 4
    assert job.businesses_stored == 2
    assert job.businesses_skipped_no_contact == 1
    assert job.businesses_duplicates == 1
    assert job.businesses_found == job.businesses_stored + job.businesses_skipped_no_contact + job.businesses_duplicates

    # Verify Royal Diner was enriched
    db.refresh(b_initial)
    assert b_initial.phone == "+919876543210" # Preserved!
    assert b_initial.email == "info@royaldiner.in" # Enriched!
    assert b_initial.website == "https://royaldiner.in" # Enriched!
    assert b_initial.status == "Has Website" # Updated!


def test_transient_retry_and_exponential_backoff(db, monkeypatch):
    """
    Transient failures (timeouts, 5xx) must retry with exponential backoff
    and only mark the unit permanently failed when the retry budget (3 attempts) is exhausted.
    """
    from datetime import datetime, timedelta, timezone
    from providers.exceptions import GeoapifyError
    import services.scan_worker as worker_mod

    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(db=db, city="Ahmedabad", category_or_family="catering", radius_km=5.0)
    unit = db.query(ScanSearchUnit).filter(ScanSearchUnit.scan_job_id == job.id).first()

    # Attempt 1: Transient 503 Server Error -> retry_wait with ~30s backoff
    monkeypatch.setattr(
        "services.scan_worker.fetch_places_page",
        lambda **kwargs: (_ for _ in ()).throw(GeoapifyError("Geoapify Error 503: Service Unavailable", status_code=503)),
    )

    _execute_search_unit(db, job, unit)
    assert unit.status == "retry_wait"
    assert unit.attempt_count == 1
    assert unit.next_attempt_at is not None
    next_at = unit.next_attempt_at if unit.next_attempt_at.tzinfo is not None else unit.next_attempt_at.replace(tzinfo=timezone.utc)
    assert next_at > datetime.now(timezone.utc) - timedelta(seconds=5)

    # Attempt 2: Transient 503 again -> retry_wait with ~60s backoff
    _execute_search_unit(db, job, unit)
    assert unit.status == "retry_wait"
    assert unit.attempt_count == 2

    # Attempt 3: Exhausted retry budget -> permanently failed
    _execute_search_unit(db, job, unit)
    assert unit.status == "failed"
    assert unit.attempt_count == 3
    assert "503" in unit.error_message


def test_geoapify_daily_quota_pause_and_multi_day_resumption(db, monkeypatch):
    """
    When Geoapify daily quota (3,000 req/day) is exhausted:
    1. Scan state is paused safely.
    2. Remaining units remain pending (NOT failed).
    3. No search units or discovered leads are lost.
    4. Resuming the scan continues from the remaining pending search units.
    """
    from providers.exceptions import GeoapifyError
    import services.scan_worker as worker_mod

    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    # Disable auto spawn so we can control worker steps
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(db=db, city="Ahmedabad", category_or_family="catering", radius_km=5.0)
    units = db.query(ScanSearchUnit).filter(ScanSearchUnit.scan_job_id == job.id).order_by(ScanSearchUnit.id.asc()).all()
    assert len(units) >= 2

    # Step 1: Unit 1 succeeds
    monkeypatch.setattr(
        "services.scan_worker.fetch_places_page",
        lambda **kwargs: [
            {
                "properties": {
                    "name": "Day 1 Bakery",
                    "place_id": "day1_bakery",
                    "contact": {"phone": "+15551111"},
                }
            }
        ],
    )
    _execute_search_unit(db, job, units[0])
    assert units[0].status == "completed"
    assert job.businesses_stored == 1

    # Step 2: Unit 2 hits daily quota (HTTP 402)
    monkeypatch.setattr(
        "services.scan_worker.fetch_places_page",
        lambda **kwargs: (_ for _ in ()).throw(GeoapifyError("Geoapify Error 402: Daily requests limit reached", status_code=402)),
    )
    _execute_search_unit(db, job, units[1])

    # Job paused safely, unit remains pending, no data lost
    assert job.status == STATUS_PAUSED
    assert "quota reached" in job.error_message.lower()
    assert units[1].status == "pending"
    assert units[0].status == "completed"
    assert job.businesses_stored == 1

    # Step 3: Quota resets on next day, admin resumes scan
    resumed_job = resume_scan_job(db, job.id)
    assert resumed_job.status == STATUS_RUNNING

    # Unit 2 now succeeds
    monkeypatch.setattr(
        "services.scan_worker.fetch_places_page",
        lambda **kwargs: [
            {
                "properties": {
                    "name": "Day 2 Cafe",
                    "place_id": "day2_cafe",
                    "contact": {"phone": "+15552222"},
                }
            }
        ],
    )
    _execute_search_unit(db, job, units[1])
    assert units[1].status == "completed"
    assert job.businesses_stored == 2


def test_progress_semantics_with_failed_units(db, monkeypatch):
    """
    Geographic coverage progress MUST only count successfully completed units.
    Permanently failed units must be tracked separately and not falsely claim 100% coverage.
    """
    monkeypatch.setattr("services.scan_worker.geocode_city", lambda city: (23.0225, 72.5714, "place_123"))
    monkeypatch.setattr("services.scan_worker._spawn_background_worker", lambda job_id: None)

    job = start_continuous_scan(db=db, city="Ahmedabad", category_or_family="catering", radius_km=5.0)
    units = db.query(ScanSearchUnit).filter(ScanSearchUnit.scan_job_id == job.id).all()
    total_units = len(units)
    assert total_units >= 2

    # Mark 1 unit as failed and the rest as completed
    units[0].status = "failed"
    units[0].error_message = "Permanent 400 error"
    for u in units[1:]:
        u.status = "completed"
    db.commit()

    from services.scan_worker import _recalculate_job_progress, get_scan_status_detail
    _recalculate_job_progress(db, job)

    expected_coverage = int(((total_units - 1) / total_units) * 100)
    assert job.coverage_progress == expected_coverage
    assert job.processed_progress == 100
    assert job.failed_search_units == 1
    assert job.completed_search_units == total_units - 1
    assert job.progress == expected_coverage
    assert job.progress < 100  # NEVER falsely shows 100% coverage when failures exist!

    detail = get_scan_status_detail(db, job.id)
    assert detail["coverage_progress"] == expected_coverage
    assert detail["processed_progress"] == 100
    assert detail["failed_search_units"] == 1

