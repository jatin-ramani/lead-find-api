"""
Unit and integration tests for Default Grade Email Templates (Grade A, B, C, D).

Covers:
- Seed function creates the four deterministic default templates
- Idempotency (calling seed repeatedly never creates duplicates)
- Exactly one active default template per grade
- Template content contains only allowlisted variables
- Template rendering with sample lead data
- Helper get_default_grade_template resolution
- Test Email resolution of stored templates
- User template preservation (no overwriting or deletion)
"""

import re
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database.models import EmailTemplate, GmailOAuthCredential
from services.email_template_service import (
    DEFAULT_GRADE_TEMPLATES,
    get_default_grade_template,
    seed_default_templates,
)
from services.template_engine import (
    ALLOWLISTED_TEMPLATE_VARIABLES,
    _VARIABLE_PATTERN,
    render_template,
)


def test_seed_default_templates_creates_four_grades(db: Session):
    # Ensure initially empty
    db.query(EmailTemplate).delete()
    db.commit()

    created = seed_default_templates(db)
    assert len(created) == 4

    all_templates = db.query(EmailTemplate).all()
    assert len(all_templates) == 4

    names = {t.name for t in all_templates}
    expected_names = {item["name"] for item in DEFAULT_GRADE_TEMPLATES}
    assert names == expected_names

    # Check all are active
    assert all(t.is_archived is False for t in all_templates)


def test_seed_default_templates_is_idempotent(db: Session):
    db.query(EmailTemplate).delete()
    db.commit()

    # First call: creates 4
    first_run = seed_default_templates(db)
    assert len(first_run) == 4

    # Second call: creates 0
    second_run = seed_default_templates(db)
    assert len(second_run) == 0

    # Total remains 4
    total = db.query(EmailTemplate).count()
    assert total == 4


def test_seed_preserves_existing_user_templates(db: Session):
    db.query(EmailTemplate).delete()
    db.commit()

    # Create a user custom template first
    user_tpl = EmailTemplate(
        name="Custom Marketing Pitch",
        description="My custom pitch",
        subject="Hello from Custom {{business_name}}",
        body="Custom body for {{contact_name}}",
        is_archived=False,
    )
    db.add(user_tpl)
    db.commit()

    # Run seed
    created = seed_default_templates(db)
    assert len(created) == 4

    # Total templates is 5 (1 user + 4 default)
    total = db.query(EmailTemplate).count()
    assert total == 5

    # User template is untouched
    persisted_user = db.query(EmailTemplate).filter(EmailTemplate.name == "Custom Marketing Pitch").first()
    assert persisted_user is not None
    assert persisted_user.subject == "Hello from Custom {{business_name}}"


def test_templates_use_only_supported_variables():
    for item in DEFAULT_GRADE_TEMPLATES:
        # Extract variables from subject and body
        subject_vars = _VARIABLE_PATTERN.findall(item["subject"])
        body_vars = _VARIABLE_PATTERN.findall(item["body"])

        for var in subject_vars + body_vars:
            assert var.lower() in ALLOWLISTED_TEMPLATE_VARIABLES, (
                f"Template '{item['name']}' uses non-allowlisted variable '{{{{{var}}}}}'"
            )


def test_template_rendering_with_sample_data():
    sample_context = {
        "business_name": "Apex Dental Clinic",
        "contact_name": "Dr. Sarah Smith",
        "email": "sarah@apexdental.com",
        "phone": "+1 555-0199",
        "website": "https://apexdental.com",
        "lead_status": "New",
        "lead_score": "95",
    }

    for item in DEFAULT_GRADE_TEMPLATES:
        rendered_subject = render_template(item["subject"], sample_context, escape_html=False)
        rendered_body = render_template(item["body"], sample_context, escape_html=True)

        assert "Apex Dental Clinic" in rendered_subject or "Apex Dental Clinic" in rendered_body
        assert "Dr. Sarah Smith" in rendered_body
        # Ensure no unrendered {{...}} tags remained for provided variables
        assert "{{business_name}}" not in rendered_subject
        assert "{{contact_name}}" not in rendered_body


def test_get_default_grade_template_resolution(db: Session):
    db.query(EmailTemplate).delete()
    db.commit()

    seed_default_templates(db)

    for grade in ["A", "B", "C", "D"]:
        tpl = get_default_grade_template(db, grade)
        assert tpl is not None, f"Could not resolve template for Grade {grade}"
        assert f"Grade {grade}" in tpl.name
        assert tpl.is_archived is False


def test_gmail_test_send_resolves_seeded_grade_templates(client: TestClient, db: Session):
    db.query(EmailTemplate).delete()
    db.query(GmailOAuthCredential).delete()
    db.commit()

    # Seed the 4 templates
    seed_default_templates(db)

    # Add active mock Gmail credential
    from datetime import datetime, timedelta, timezone
    cred = GmailOAuthCredential(
        email_address="sender@example.com",
        encrypted_access_token="enc_access",
        encrypted_refresh_token="enc_refresh",
        token_expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.send",
        is_active=True,
        daily_send_count=0,
        daily_send_reset_date="2026-09-08",
    )
    db.add(cred)
    db.commit()

    resp = client.post(
        "/integrations/gmail/test-send",
        json={
            "recipient_email": "test-recipient@example.com",
            "template_grades": ["A", "B", "C", "D"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["sent"] == 4
    assert len(data["results"]) == 4

    # Verify subjects matched the seeded templates
    subjects = [r["subject"] for r in data["results"]]
    assert any("Introduction regarding" in s for s in subjects)  # Grade A
    assert any("Connecting with" in s for s in subjects)         # Grade B
    assert any("Quick question for" in s for s in subjects)      # Grade C
    assert any("Inquiry for" in s for s in subjects)             # Grade D


def test_templates_api_lists_seeded_templates(client: TestClient, db: Session):
    db.query(EmailTemplate).delete()
    db.commit()

    seed_default_templates(db)

    resp = client.get("/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["total"] == 4
    assert len(data["items"]) == 4


def test_default_templates_deliverability_and_honest_claims():
    """Verify default templates do not contain fabricated claims, fake reviews, or [TEST] tags."""
    fabricated_phrases = [
        "top-performing",
        "recently reviewed",
        "conducted a preliminary",
        "visibility review",
        "guarantee",
        "limited time",
        "act now",
        "[test]",
    ]

    for item in DEFAULT_GRADE_TEMPLATES:
        # 1. No [TEST] in stored subject
        assert "[test]" not in item["subject"].lower(), f"Subject contains test tag: {item['subject']}"

        # 2. No fabricated claims or spam triggers
        body_lower = item["body"].lower()
        subject_lower = item["subject"].lower()
        for phrase in fabricated_phrases:
            assert phrase not in body_lower, f"Template '{item['name']}' body contains spammy/fabricated phrase '{phrase}'"
            assert phrase not in subject_lower, f"Template '{item['name']}' subject contains spammy/fabricated phrase '{phrase}'"

        # 3. Conciseness check (word count between 30 and 150 words)
        words = re.findall(r"\b\w+\b", re.sub(r"<[^>]+>", " ", item["body"]))
        word_count = len(words)
        assert 30 <= word_count <= 150, f"Template '{item['name']}' word count ({word_count}) not in 30-150 range"

