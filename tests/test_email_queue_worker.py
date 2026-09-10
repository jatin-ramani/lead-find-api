"""
Comprehensive Test Suite for Durable Background Email Queue Worker,
Rate Limiting, Retries, Backoff, Quota Protection, and Recovery.
"""

from datetime import datetime, timedelta, timezone
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from config import settings
from database.models import (
    Business,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailTemplate,
)
from providers.email_provider import EmailSendResult
from services.city_automation_service import (
    cancel_city_automation,
    get_city_automation_report,
    start_city_automation,
)
from services.email_queue_worker import (
    DEFAULT_MAX_RETRIES,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_ERRORS,
    STATUS_PAUSED,
    STATUS_RUNNING,
    RECIPIENT_CANCELLED,
    RECIPIENT_FAILED,
    RECIPIENT_PENDING,
    RECIPIENT_PROCESSING,
    RECIPIENT_SENT,
    calculate_backoff_seconds,
    claim_next_recipient,
    get_throttle_interval_seconds,
    process_campaign_queue,
    recover_stale_processing_recipients,
)


@pytest.fixture
def sample_businesses(db: Session):
    """Seed test businesses with varying grades in Austin."""
    db.query(EmailCampaignRecipient).delete()
    db.query(EmailCampaign).delete()
    db.query(Business).filter(Business.city == "Austin").delete()
    db.commit()

    businesses = [
        Business(
            name=f"Austin Business {i}",
            email=f"biz{i}@austin.test",
            city="Austin",
            category="Plumber",
            lead_grade=grade,
            lead_score=score,
            place_id=f"place_austin_{i}",
        )
        for i, (grade, score) in enumerate([("A", 90), ("B", 75), ("C", 50), ("D", 20)], start=1)
    ]
    for b in businesses:
        db.add(b)
    db.commit()
    for b in businesses:
        db.refresh(b)
    return businesses


@pytest.fixture
def default_templates_dict():
    return {
        "A": {"subject": "Intro for {{business_name}}", "body": "Hello {{contact_name}}", "name": "Grade A"},
        "B": {"subject": "Connecting with {{business_name}}", "body": "Hello {{contact_name}}", "name": "Grade B"},
        "C": {"subject": "Quick question for {{business_name}}", "body": "Hello {{contact_name}}", "name": "Grade C"},
        "D": {"subject": "Inquiry for {{business_name}}", "body": "Hello {{contact_name}}", "name": "Grade D"},
    }


def test_throttle_interval_calculation(monkeypatch):
    """Verify rate limit spacing corresponds to EMAIL_SENDS_PER_MINUTE."""
    monkeypatch.setattr(settings, "EMAIL_SENDS_PER_MINUTE", 20)
    assert get_throttle_interval_seconds() == 3.0

    monkeypatch.setattr(settings, "EMAIL_SENDS_PER_MINUTE", 60)
    assert get_throttle_interval_seconds() == 1.0

    monkeypatch.setattr(settings, "EMAIL_SENDS_PER_MINUTE", 10)
    assert get_throttle_interval_seconds() == 6.0


def test_exponential_backoff_calculation():
    """Verify exponential backoff calculation increases with attempt count."""
    d1 = calculate_backoff_seconds(1, base_seconds=4.0)
    d2 = calculate_backoff_seconds(2, base_seconds=4.0)
    d3 = calculate_backoff_seconds(3, base_seconds=4.0)

    assert 4.0 <= d1 <= 6.0
    assert 8.0 <= d2 <= 10.0
    assert 16.0 <= d3 <= 18.0


def test_automation_start_creates_persistent_queue_and_returns_immediately(client: TestClient, sample_businesses, default_templates_dict):
    """Verify API returns immediately with queued recipients and status running."""
    payload = {
        "city": "Austin",
        "name": "Austin Automated Run",
        "templates": {
            k: {"subject": v["subject"], "body": v["body"], "name": v["name"]}
            for k, v in default_templates_dict.items()
        },
    }
    response = client.post("/automations/start-city-automation", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    report = data["data"]
    assert report["city"] == "Austin"
    assert report["recipient_count"] == 4
    assert report["status"] == STATUS_RUNNING
    assert "id" in report


def test_successful_queue_processing_transitions_to_sent(db: Session, sample_businesses, default_templates_dict):
    """Verify queue processing sends emails and marks items SENT."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    mock_provider = MagicMock()
    mock_provider.send_email.return_value = EmailSendResult(
        success=True,
        message_id="msg_test_123",
    )

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider):
        result = process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    assert result["success"] is True

    # Check report
    rep = get_city_automation_report(db, campaign_id)
    assert rep["status"] == STATUS_COMPLETED
    assert rep["sent_count"] == 4
    assert rep["failed_count"] == 0
    assert rep["pending_count"] == 0
    assert rep["percentage"] == 100

    # Verify recipients in database
    recipients = db.query(EmailCampaignRecipient).filter(EmailCampaignRecipient.campaign_id == campaign_id).all()
    assert len(recipients) == 4
    for r in recipients:
        assert r.status == RECIPIENT_SENT
        assert r.provider_message_id == "msg_test_123"
        assert r.sent_at is not None


def test_temporary_rate_limit_429_retries_with_backoff(db: Session, sample_businesses, default_templates_dict):
    """Verify 429 transient rate limit error schedules retry with exponential backoff."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    calls = 0

    def mock_send(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return EmailSendResult(success=False, error="429 Rate limit exceeded: UserRateLimitExceeded")
        return EmailSendResult(success=True, message_id=f"msg_retry_{calls}")

    mock_provider = MagicMock()
    mock_provider.send_email.side_effect = mock_send

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider), \
         patch("services.email_queue_worker.calculate_backoff_seconds", return_value=0.0):
        result = process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    assert result["success"] is True

    rep = get_city_automation_report(db, campaign_id)
    assert rep["status"] == STATUS_COMPLETED
    assert rep["sent_count"] == 4
    assert rep["failed_count"] == 0


def test_permanent_error_becomes_failed_and_campaign_completes_with_errors(db: Session, sample_businesses, default_templates_dict):
    """Verify non-retryable error marks item FAILED and finishes as completed_with_errors."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    def mock_send(to_email, *args, **kwargs):
        if "biz1@" in to_email:
            return EmailSendResult(success=False, error="550 5.1.1 User unknown")
        return EmailSendResult(success=True, message_id="msg_ok")

    mock_provider = MagicMock()
    mock_provider.send_email.side_effect = mock_send

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider):
        process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    rep = get_city_automation_report(db, campaign_id)
    assert rep["status"] == STATUS_COMPLETED_WITH_ERRORS
    assert rep["sent_count"] == 3
    assert rep["failed_count"] == 1


def test_daily_quota_exhaustion_pauses_campaign_and_preserves_pending(db: Session, sample_businesses, default_templates_dict):
    """Verify hitting the daily quota pauses the campaign without failing pending items."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    def mock_send(*args, **kwargs):
        return EmailSendResult(success=False, error="Daily sending limit reached (400 emails).")

    mock_provider = MagicMock()
    mock_provider.send_email.side_effect = mock_send

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider):
        process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    rep = get_city_automation_report(db, campaign_id)
    assert rep["status"] == STATUS_PAUSED
    assert "Daily sending limit reached" in (rep["paused_reason"] or "")
    assert rep["sent_count"] == 0
    assert rep["failed_count"] == 0
    assert rep["pending_count"] == 4


def test_stale_processing_recovery_on_worker_restart(db: Session, sample_businesses, default_templates_dict):
    """Verify recipients stuck in PROCESSING older than timeout recover to PENDING."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    rec = db.query(EmailCampaignRecipient).filter(EmailCampaignRecipient.campaign_id == campaign_id).first()
    assert rec is not None
    rec.status = RECIPIENT_PROCESSING
    rec.attempted_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    rec.attempt_count = 1
    db.commit()

    recovered = recover_stale_processing_recipients(db, timeout_minutes=5)
    assert recovered >= 1

    db.refresh(rec)
    assert rec.status == RECIPIENT_PENDING
    assert "Recovered from interrupted processing" in (rec.error_message or "")


def test_concurrent_claiming_prevents_duplicate_sends(db: Session, sample_businesses, default_templates_dict):
    """Verify claiming an item transitions it to PROCESSING atomically so second worker cannot claim it."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    claimed1 = claim_next_recipient(db, campaign_id)
    assert claimed1 is not None
    assert claimed1.status == RECIPIENT_PROCESSING

    claimed2 = claim_next_recipient(db, campaign_id)
    assert claimed2 is not None
    assert claimed2.id != claimed1.id
    assert claimed2.status == RECIPIENT_PROCESSING


def test_cancellation_stops_dispatch_of_pending_recipients(db: Session, sample_businesses, default_templates_dict):
    """Verify cancelled campaign stops worker from sending remaining pending recipients."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    # Cancel immediately
    cancel_city_automation(db, campaign_id)

    mock_provider = MagicMock()
    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider):
        process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    # Provider should never have been called
    assert mock_provider.send_email.call_count == 0

    rep = get_city_automation_report(db, campaign_id)
    assert rep["status"] == STATUS_CANCELLED
    assert rep["sent_count"] == 0
    assert rep["cancelled_count"] == 4


def test_process_restart_while_campaign_is_running(db: Session, sample_businesses, default_templates_dict):
    """Verify simulate process death while RUNNING: database preserves pending/sent and resume resumes."""
    report = start_city_automation(
        db=db,
        city="Austin",
        templates=default_templates_dict,
        execute_now=False,
    )
    campaign_id = report["id"]

    # Send 1 email then simulate sudden process termination
    send_count = 0
    def mock_send_one(*args, **kwargs):
        nonlocal send_count
        send_count += 1
        return EmailSendResult(success=True, message_id=f"msg_{send_count}")

    mock_provider = MagicMock()
    mock_provider.send_email.side_effect = mock_send_one

    # Claim and send 1 item
    rec = claim_next_recipient(db, campaign_id)
    assert rec is not None
    rec.status = RECIPIENT_SENT
    rec.sent_at = datetime.now(timezone.utc)
    rec.provider_message_id = "msg_1"
    camp = db.query(EmailCampaign).filter(EmailCampaign.id == campaign_id).first()
    camp.sent_count = 1
    camp.status = STATUS_RUNNING
    db.commit()

    # Leave one item in processing (as if crashed mid-send)
    rec2 = claim_next_recipient(db, campaign_id)
    assert rec2 is not None
    rec2.status = RECIPIENT_PROCESSING
    rec2.attempted_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.commit()

    # Verify campaign is RUNNING and not prematurely COMPLETED
    rep_before = get_city_automation_report(db, campaign_id)
    assert rep_before["status"] == STATUS_RUNNING
    assert rep_before["sent_count"] == 1
    assert rep_before["pending_count"] == 2
    assert rep_before["processing_count"] == 1

    # SIMULATE STARTUP: recover stale + resume
    recover_stale_processing_recipients(db, timeout_minutes=5)
    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider):
        process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    # All 4 should now be SENT
    rep_after = get_city_automation_report(db, campaign_id)
    assert rep_after["status"] == STATUS_COMPLETED
    assert rep_after["sent_count"] == 4
    assert rep_after["failed_count"] == 0
    assert rep_after["pending_count"] == 0


def test_multiple_active_campaigns_do_not_corrupt_each_other(db: Session, default_templates_dict):
    """Verify two active campaigns for different cities process independently without state collision."""
    # Seed 2 businesses in Dallas and 2 in Houston
    b_dallas = [
        Business(name="Dallas 1", email="d1@test.com", city="Dallas", lead_grade="A", place_id="p_d1"),
        Business(name="Dallas 2", email="d2@test.com", city="Dallas", lead_grade="B", place_id="p_d2"),
    ]
    b_houston = [
        Business(name="Houston 1", email="h1@test.com", city="Houston", lead_grade="C", place_id="p_h1"),
        Business(name="Houston 2", email="h2@test.com", city="Houston", lead_grade="D", place_id="p_h2"),
    ]
    for b in b_dallas + b_houston:
        db.add(b)
    db.commit()

    rep1 = start_city_automation(db, city="Dallas", templates=default_templates_dict, execute_now=False)
    rep2 = start_city_automation(db, city="Houston", templates=default_templates_dict, execute_now=False)

    camp1_id = rep1["id"]
    camp2_id = rep2["id"]

    mock_provider = MagicMock()
    mock_provider.send_email.return_value = EmailSendResult(success=True, message_id="msg_ok")

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider):
        process_campaign_queue(camp1_id, sleep_fn=lambda _: None)
        process_campaign_queue(camp2_id, sleep_fn=lambda _: None)

    r1 = get_city_automation_report(db, camp1_id)
    r2 = get_city_automation_report(db, camp2_id)

    assert r1["status"] == STATUS_COMPLETED
    assert r1["sent_count"] == 2
    assert r1["city"] == "Dallas"

    assert r2["status"] == STATUS_COMPLETED
    assert r2["sent_count"] == 2
    assert r2["city"] == "Houston"


def test_duplicate_worker_invocation_is_safely_ignored(db: Session, sample_businesses, default_templates_dict):
    """Verify _ACTIVE_CAMPAIGNS prevents multiple workers from executing the same campaign."""
    report = start_city_automation(db, city="Austin", templates=default_templates_dict, execute_now=False)
    campaign_id = report["id"]

    from services.email_queue_worker import _ACTIVE_CAMPAIGNS, _ACTIVE_CAMPAIGNS_LOCK

    with _ACTIVE_CAMPAIGNS_LOCK:
        _ACTIVE_CAMPAIGNS.add(campaign_id)

    try:
        # Invoking process_campaign_queue while registered in _ACTIVE_CAMPAIGNS should return immediately
        result = process_campaign_queue(campaign_id, sleep_fn=lambda _: None)
        assert result["success"] is True
        assert result.get("already_running") is True
    finally:
        with _ACTIVE_CAMPAIGNS_LOCK:
            _ACTIVE_CAMPAIGNS.discard(campaign_id)


def test_daily_quota_pause_survives_restart_and_resumes_cleanly(db: Session, sample_businesses, default_templates_dict):
    """Verify a paused campaign remains paused after restart, and resume finishes the remaining queue."""
    report = start_city_automation(db, city="Austin", templates=default_templates_dict, execute_now=False)
    campaign_id = report["id"]

    # 1. First run hits daily quota
    mock_quota_provider = MagicMock()
    mock_quota_provider.send_email.return_value = EmailSendResult(success=False, error="Daily sending limit reached")

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_quota_provider):
        process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    rep = get_city_automation_report(db, campaign_id)
    assert rep["status"] == STATUS_PAUSED
    assert rep["pending_count"] == 4

    # 2. Simulate server restart: recovery runs
    recover_stale_processing_recipients(db)
    rep_reboot = get_city_automation_report(db, campaign_id)
    assert rep_reboot["status"] == STATUS_PAUSED  # Stays safely paused

    # 3. Next day / user resumes
    from services.city_automation_service import resume_city_automation
    resume_rep = resume_city_automation(db, campaign_id)
    assert resume_rep["status"] == STATUS_RUNNING

    # 4. Now quota is available, send succeeds
    mock_ok_provider = MagicMock()
    mock_ok_provider.send_email.return_value = EmailSendResult(success=True, message_id="msg_resumed")

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_ok_provider):
        process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    final_rep = get_city_automation_report(db, campaign_id)
    assert final_rep["status"] == STATUS_COMPLETED
    assert final_rep["sent_count"] == 4
    assert final_rep["pending_count"] == 0
    assert final_rep["remaining_count"] == 0
    assert len(final_rep["remaining_recipients"]) == 0
    assert len(final_rep["recipient_logs"]) == 4


def test_sent_recipients_excluded_from_remaining_list_and_preserved_in_history(db: Session, sample_businesses, default_templates_dict):
    """
    Verify:
    1. Initially all 4 recipients are in remaining_recipients (remaining_count = 4).
    2. After 2 are sent, remaining_recipients contains only the 2 unsent leads (remaining_count = 2).
    3. Sent recipients leave remaining_recipients but remain in recipient_logs and DB.
    4. Campaign scope: Leads sent in Campaign A remain eligible for a future Campaign B.
    """
    # 1. Start campaign A
    rep_a = start_city_automation(db, city="Austin", templates=default_templates_dict, execute_now=False)
    camp_a_id = rep_a["id"]

    initial_rep = get_city_automation_report(db, camp_a_id)
    assert initial_rep["recipient_count"] == 4
    assert initial_rep["sent_count"] == 0
    assert initial_rep["remaining_count"] == 4
    assert len(initial_rep["remaining_recipients"]) == 4
    assert len(initial_rep["recipient_logs"]) == 4

    # 2. Claim and send 2 recipients
    rec1 = claim_next_recipient(db, camp_a_id)
    assert rec1 is not None
    rec1.status = RECIPIENT_SENT
    rec1.sent_at = datetime.now(timezone.utc)
    rec1.provider_message_id = "msg_sent_1"

    rec2 = claim_next_recipient(db, camp_a_id)
    assert rec2 is not None
    rec2.status = RECIPIENT_SENT
    rec2.sent_at = datetime.now(timezone.utc)
    rec2.provider_message_id = "msg_sent_2"

    db.commit()

    # 3. Verify report after 2 sends
    mid_rep = get_city_automation_report(db, camp_a_id)
    assert mid_rep["sent_count"] == 2
    assert mid_rep["remaining_count"] == 2
    assert len(mid_rep["remaining_recipients"]) == 2

    # Verify the 2 remaining are strictly the unsent ones
    remaining_ids = {r["id"] for r in mid_rep["remaining_recipients"]}
    assert rec1.id not in remaining_ids
    assert rec2.id not in remaining_ids

    # Verify history / logs still contains ALL 4 recipients
    assert len(mid_rep["recipient_logs"]) == 4
    log_ids = {r["id"] for r in mid_rep["recipient_logs"]}
    assert rec1.id in log_ids
    assert rec2.id in log_ids

    # 4. Campaign Scope Isolation: Start a separate Campaign B for Austin
    rep_b = start_city_automation(db, city="Austin", templates=default_templates_dict, execute_now=False)
    camp_b_id = rep_b["id"]
    assert camp_b_id != camp_a_id
    rep_b_report = get_city_automation_report(db, camp_b_id)
    # Campaign B has its own independent 4 recipients
    assert rep_b_report["recipient_count"] == 4
    assert rep_b_report["remaining_count"] == 4
    assert len(rep_b_report["remaining_recipients"]) == 4


def test_all_grades_receive_same_universal_master_cold_email(db: Session, sample_businesses):
    """
    Verify that every eligible lead (Grades A, B, C, D) receives the exact same universal master cold email,
    with personalized business_name and safe fallback without unresolved variables.
    """
    report = start_city_automation(db, city="Austin", execute_now=False)
    campaign_id = report["id"]

    sent_emails: list = []

    def mock_send(to_email, subject, html_content, text_content, metadata=None):
        sent_emails.append({
            "to": to_email,
            "subject": subject,
            "body": text_content,
            "grade": metadata.get("lead_grade"),
            "business_id": metadata.get("business_id"),
        })
        return EmailSendResult(success=True, message_id=f"msg_{len(sent_emails)}")

    mock_provider = MagicMock()
    mock_provider.send_email.side_effect = mock_send

    with patch("services.email_queue_worker.get_email_provider", return_value=mock_provider):
        process_campaign_queue(campaign_id, sleep_fn=lambda _: None)

    rep = get_city_automation_report(db, campaign_id)
    assert rep["status"] == STATUS_COMPLETED
    assert rep["sent_count"] == 4
    assert len(sent_emails) == 4

    # Check grades of recipients sent
    grades_sent = {e["grade"] for e in sent_emails}
    assert grades_sent == {"A", "B", "C", "D"}

    # Verify ALL 4 leads received the master mockup offer template
    for email_item in sent_emails:
        biz = db.query(Business).filter(Business.id == email_item["business_id"]).first()
        assert biz is not None

        # Subject uses universal pattern
        assert email_item["subject"] == f"A free website mockup for {biz.name}?"

        # Body contains Codebait master copy
        assert f"Hi {biz.name} team," in email_item["body"]
        assert "We're Codebait, a web design studio" in email_item["body"]
        assert f"free, no-obligation website mockup for {biz.name}" in email_item["body"]
        assert "Jatin Ramani" in email_item["body"]
        assert "7861035002" in email_item["body"]
        assert "jatinrmn@gmail.com" in email_item["body"]

        # Ensure NO unresolved variables remain
        assert "{{" not in email_item["subject"]
        assert "}}" not in email_item["subject"]
        assert "{{" not in email_item["body"]
        assert "}}" not in email_item["body"]



