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

from datetime import datetime, timezone
import re
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database.models import EmailTemplate, GmailOAuthCredential
from services.email_template_service import (
    DEFAULT_GRADE_TEMPLATES,
    DEFAULT_UNIVERSAL_TEMPLATE,
    get_default_grade_template,
    get_universal_master_template,
    seed_default_templates,
)
from services.template_engine import (
    ALLOWLISTED_TEMPLATE_VARIABLES,
    _VARIABLE_PATTERN,
    render_template,
)


def test_seed_default_templates_creates_universal_and_grades(db: Session):
    # Ensure initially empty
    db.query(EmailTemplate).delete()
    db.commit()

    created = seed_default_templates(db)
    assert len(created) == 5  # 1 universal master + 4 grade templates

    all_templates = db.query(EmailTemplate).all()
    assert len(all_templates) == 5

    names = {t.name for t in all_templates}
    assert DEFAULT_UNIVERSAL_TEMPLATE["name"] in names
    for item in DEFAULT_GRADE_TEMPLATES:
        assert item["name"] in names

    # Check all are active
    assert all(t.is_archived is False for t in all_templates)


def test_seed_default_templates_is_idempotent(db: Session):
    db.query(EmailTemplate).delete()
    db.commit()

    # First call: creates 5
    first_run = seed_default_templates(db)
    assert len(first_run) == 5

    # Second call: creates 0
    second_run = seed_default_templates(db)
    assert len(second_run) == 0

    # Total remains 5
    total = db.query(EmailTemplate).count()
    assert total == 5


def test_seed_preserves_existing_user_templates(db: Session):
    db.query(EmailTemplate).delete()
    db.commit()
    db.expunge_all()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Create a user custom template first
    user_tpl = EmailTemplate(
        name="Custom Marketing Pitch",
        description="My custom pitch",
        subject="Hello from Custom {{business_name}}",
        body="Custom body for {{contact_name}}",
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(user_tpl)
    db.commit()
    db.expunge_all()

    # Run seed
    created = seed_default_templates(db)
    assert len(created) == 5

    # Total templates is 6 (1 user + 5 default)
    total = db.query(EmailTemplate).count()
    assert total == 6

    # User template is untouched
    persisted_user = db.query(EmailTemplate).filter(EmailTemplate.name == "Custom Marketing Pitch").first()
    assert persisted_user is not None
    assert persisted_user.subject == "Hello from Custom {{business_name}}"


def test_templates_use_only_supported_variables():
    templates_to_check = [DEFAULT_UNIVERSAL_TEMPLATE] + DEFAULT_GRADE_TEMPLATES
    for item in templates_to_check:
        # Extract variables from subject and body
        subject_vars = _VARIABLE_PATTERN.findall(item["subject"])
        body_vars = _VARIABLE_PATTERN.findall(item["body"])

        for var in subject_vars + body_vars:
            assert var.lower() in ALLOWLISTED_TEMPLATE_VARIABLES, (
                f"Template '{item['name']}' uses non-allowlisted variable '{{{{{var}}}}}'"
            )


def test_universal_master_template_retrieval_and_rendering(db: Session):
    db.query(EmailTemplate).delete()
    db.commit()

    univ_tpl = get_universal_master_template(db)
    assert univ_tpl is not None
    assert univ_tpl.subject == "A free website mockup for {{business_name}}?"
    assert "Hi {{business_name}} team," in univ_tpl.body
    assert "Codebait" in univ_tpl.body
    assert "Jatin Ramani" in univ_tpl.body
    assert "7861035002" in univ_tpl.body
    assert "jatinrmn@gmail.com" in univ_tpl.body

    # Rendering with business data
    rendered_subject = render_template(univ_tpl.subject, {"business_name": "Apex Dental"})
    rendered_body = render_template(univ_tpl.body, {"business_name": "Apex Dental"})

    assert rendered_subject == "A free website mockup for Apex Dental?"
    assert "Hi Apex Dental team," in rendered_body
    assert "for Apex Dental —" in rendered_body
    assert "{{business_name}}" not in rendered_subject
    assert "{{business_name}}" not in rendered_body


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

    templates_to_test = [DEFAULT_UNIVERSAL_TEMPLATE] + DEFAULT_GRADE_TEMPLATES
    for item in templates_to_test:
        rendered_subject = render_template(item["subject"], sample_context, escape_html=False)
        rendered_body = render_template(item["body"], sample_context, escape_html=True)

        assert "Apex Dental Clinic" in rendered_subject or "Apex Dental Clinic" in rendered_body
        assert "{{business_name}}" not in rendered_subject


def test_get_default_grade_template_resolution(db: Session):
    db.query(EmailTemplate).delete()
    db.commit()

    seed_default_templates(db)

    for grade in ["A", "B", "C", "D"]:
        tpl = get_default_grade_template(db, grade)
        assert tpl is not None, f"Could not resolve template for Grade {grade}"
        assert f"Grade {grade}" in tpl.name
        assert tpl.is_archived is False


def test_gmail_test_send_resolves_universal_master_template(client: TestClient, db: Session):
    db.query(EmailTemplate).delete()
    db.query(GmailOAuthCredential).delete()
    db.commit()

    # Seed templates
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
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["sent"] == 1
    assert len(data["results"]) == 1

    # Verify subject matched the universal master template
    result = data["results"][0]
    assert "[TEST] A free website mockup for Test Business?" == result["subject"]


def test_templates_api_lists_seeded_templates(client: TestClient, db: Session):
    db.query(EmailTemplate).delete()
    db.commit()
    db.expunge_all()

    seed_default_templates(db)
    db.commit()

    resp = client.get("/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["total"] == 5
    assert len(data["items"]) == 5


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

    for item in [DEFAULT_UNIVERSAL_TEMPLATE] + DEFAULT_GRADE_TEMPLATES:
        # 1. No [TEST] in stored subject
        assert "[test]" not in item["subject"].lower(), f"Subject contains test tag: {item['subject']}"

        # 2. No fabricated claims or spam triggers
        body_lower = item["body"].lower()
        subject_lower = item["subject"].lower()
        for phrase in fabricated_phrases:
            assert phrase not in body_lower, f"Template '{item['name']}' body contains spammy/fabricated phrase '{phrase}'"
            assert phrase not in subject_lower, f"Template '{item['name']}' subject contains spammy/fabricated phrase '{phrase}'"

