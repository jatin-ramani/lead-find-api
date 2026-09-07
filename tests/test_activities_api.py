"""
Comprehensive backend tests for Phase 2, Feature 1: Lead Activity / History System.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from database.models import Business, BusinessActivity, BusinessNote, Tag
from services.activity_service import (
    ACTIVITY_BUSINESS_CREATED,
    ACTIVITY_FAVORITE_ADDED,
    ACTIVITY_FAVORITE_REMOVED,
    ACTIVITY_NOTE_CREATED,
    ACTIVITY_NOTE_DELETED,
    ACTIVITY_NOTE_UPDATED,
    ACTIVITY_STATUS_CHANGED,
    ACTIVITY_TAG_ADDED,
    ACTIVITY_TAG_REMOVED,
    create_activity,
    get_business_activities,
    get_business_activity_count,
)
from services.favorite_service import (
    bulk_set_favorites,
    set_business_favorite,
    toggle_business_favorite,
)
from services.lead_status_service import (
    bulk_update_lead_status,
    update_business_lead_status,
)
from services.note_service import (
    create_business_note,
    delete_business_note,
    update_business_note,
)
from services.tag_service import (
    attach_tag_to_business,
    bulk_attach_tag,
    bulk_remove_tag,
    create_tag,
    remove_tag_from_business,
)
from database.crud import save_business


@pytest.fixture
def sample_business(db: Session) -> Business:
    """Create a sample business for testing."""
    biz = Business(
        name="Activity Test Clinic",
        city="Ahmedabad",
        category="Dentist",
        lead_status="new",
        is_favorite=False,
        lead_score=75,
        lead_grade="B",
    )
    db.add(biz)
    db.commit()
    db.refresh(biz)
    return biz


# ============================================================================
# 1. Model & Service Unit Tests
# ============================================================================

def test_activity_model_creation_and_fields(db: Session, sample_business: Business):
    """Test manual activity creation and verify model attributes."""
    act = create_activity(
        db=db,
        business_id=sample_business.id,
        activity_type=ACTIVITY_STATUS_CHANGED,
        title="Status changed to Contacted",
        description="New → Contacted",
        metadata={"old_status": "new", "new_status": "contacted"},
    )
    assert act is not None
    assert act.id is not None
    assert act.business_id == sample_business.id
    assert act.activity_type == ACTIVITY_STATUS_CHANGED
    assert act.title == "Status changed to Contacted"
    assert act.description == "New → Contacted"
    assert "old_status" in act.metadata_json


def test_activity_cascade_delete_with_business(db: Session, sample_business: Business):
    """Deleting a business must cascade-delete all its activity records."""
    create_activity(
        db=db,
        business_id=sample_business.id,
        activity_type=ACTIVITY_STATUS_CHANGED,
        title="Test Activity 1",
    )
    create_activity(
        db=db,
        business_id=sample_business.id,
        activity_type=ACTIVITY_NOTE_CREATED,
        title="Test Activity 2",
    )

    count_before = db.query(BusinessActivity).filter(BusinessActivity.business_id == sample_business.id).count()
    assert count_before == 2

    # Delete parent business
    db.delete(sample_business)
    db.commit()

    count_after = db.query(BusinessActivity).filter(BusinessActivity.business_id == sample_business.id).count()
    assert count_after == 0


def test_activity_chronological_ordering_and_pagination(db: Session, sample_business: Business):
    """Verify newest-first ordering and pagination slicing."""
    for i in range(1, 26):
        create_activity(
            db=db,
            business_id=sample_business.id,
            activity_type=ACTIVITY_NOTE_CREATED,
            title=f"Activity {i}",
        )

    total = get_business_activity_count(db, sample_business.id)
    assert total == 25

    # Page 1 (items 1..20)
    page_1 = get_business_activities(db, sample_business.id, page=1, page_size=20)
    assert len(page_1) == 20
    assert page_1[0].title == "Activity 25"  # newest first
    assert page_1[19].title == "Activity 6"

    # Page 2 (items 21..25)
    page_2 = get_business_activities(db, sample_business.id, page=2, page_size=20)
    assert len(page_2) == 5
    assert page_2[0].title == "Activity 5"
    assert page_2[4].title == "Activity 1"


# ============================================================================
# 2. Mutation Integration & Idempotency Tests
# ============================================================================

def test_lead_status_mutation_records_activity(db: Session, sample_business: Business):
    """Changing lead status records status_changed activity; repeating same status records nothing."""
    update_business_lead_status(db, sample_business.id, "contacted")
    activities = get_business_activities(db, sample_business.id)
    assert len(activities) == 1
    assert activities[0].activity_type == ACTIVITY_STATUS_CHANGED
    assert "New → Contacted" in activities[0].description

    # Idempotent call with same status must NOT create duplicate activity
    update_business_lead_status(db, sample_business.id, "contacted")
    activities_after = get_business_activities(db, sample_business.id)
    assert len(activities_after) == 1


def test_bulk_lead_status_mutation_records_activity(db: Session):
    """Bulk status updates record activities only for businesses whose status actually changed."""
    b1 = Business(name="Bulk Biz 1", city="Surat", lead_status="new")
    b2 = Business(name="Bulk Biz 2", city="Surat", lead_status="contacted")
    db.add_all([b1, b2])
    db.commit()
    db.refresh(b1)
    db.refresh(b2)

    res = bulk_update_lead_status(db, [b1.id, b2.id], "contacted")
    assert res["updated_count"] == 1  # Only b1 changed

    act_b1 = get_business_activities(db, b1.id)
    assert len(act_b1) == 1
    assert act_b1[0].activity_type == ACTIVITY_STATUS_CHANGED

    act_b2 = get_business_activities(db, b2.id)
    assert len(act_b2) == 0


def test_favorite_mutation_records_activity(db: Session, sample_business: Business):
    """Adding/removing favorite records favorite_added/favorite_removed; idempotent calls do not duplicate."""
    set_business_favorite(db, sample_business.id, True)
    acts = get_business_activities(db, sample_business.id)
    assert len(acts) == 1
    assert acts[0].activity_type == ACTIVITY_FAVORITE_ADDED

    # Idempotent call
    set_business_favorite(db, sample_business.id, True)
    assert len(get_business_activities(db, sample_business.id)) == 1

    # Remove favorite
    set_business_favorite(db, sample_business.id, False)
    acts_after = get_business_activities(db, sample_business.id)
    assert len(acts_after) == 2
    assert acts_after[0].activity_type == ACTIVITY_FAVORITE_REMOVED


def test_tag_mutation_records_activity(db: Session, sample_business: Business):
    """Attaching and removing tags records tag_added and tag_removed activities."""
    tag = create_tag(db, "VIP Client")
    attach_tag_to_business(db, sample_business.id, tag_id=tag.id)

    acts = get_business_activities(db, sample_business.id)
    assert len(acts) == 1
    assert acts[0].activity_type == ACTIVITY_TAG_ADDED
    assert "VIP Client" in acts[0].description

    # Idempotent attach
    attach_tag_to_business(db, sample_business.id, tag_id=tag.id)
    assert len(get_business_activities(db, sample_business.id)) == 1

    # Remove tag
    remove_tag_from_business(db, sample_business.id, tag_id=tag.id)
    acts_after = get_business_activities(db, sample_business.id)
    assert len(acts_after) == 2
    assert acts_after[0].activity_type == ACTIVITY_TAG_REMOVED


def test_bulk_tag_mutations_record_activity(db: Session):
    """Bulk tag attach and remove record batched activities."""
    b1 = Business(name="Tag Biz 1", city="Rajkot")
    b2 = Business(name="Tag Biz 2", city="Rajkot")
    db.add_all([b1, b2])
    db.commit()
    db.refresh(b1)
    db.refresh(b2)

    tag = create_tag(db, "Urgent Followup")
    bulk_attach_tag(db, [b1.id, b2.id], tag_id=tag.id)

    assert len(get_business_activities(db, b1.id)) == 1
    assert len(get_business_activities(db, b2.id)) == 1
    assert get_business_activities(db, b1.id)[0].activity_type == ACTIVITY_TAG_ADDED

    # Bulk remove
    bulk_remove_tag(db, [b1.id, b2.id], tag_id=tag.id)
    assert len(get_business_activities(db, b1.id)) == 2
    assert get_business_activities(db, b1.id)[0].activity_type == ACTIVITY_TAG_REMOVED


def test_note_mutations_record_activity(db: Session, sample_business: Business):
    """Note creation, update, and deletion record distinct activities; unchanged update records nothing."""
    note = create_business_note(db, sample_business.id, "Initial discussion note")
    assert note is not None

    acts = get_business_activities(db, sample_business.id)
    assert len(acts) == 1
    assert acts[0].activity_type == ACTIVITY_NOTE_CREATED

    # Update note with new content
    update_business_note(db, note.id, "Updated discussion note")
    acts_after_update = get_business_activities(db, sample_business.id)
    assert len(acts_after_update) == 2
    assert acts_after_update[0].activity_type == ACTIVITY_NOTE_UPDATED

    # Update with same content must NOT record new activity
    update_business_note(db, note.id, "Updated discussion note")
    assert len(get_business_activities(db, sample_business.id)) == 2

    # Delete note
    delete_business_note(db, note.id)
    acts_after_delete = get_business_activities(db, sample_business.id)
    assert len(acts_after_delete) == 3
    assert acts_after_delete[0].activity_type == ACTIVITY_NOTE_DELETED


def test_business_creation_records_activity(db: Session):
    """When a new business is created via save_business, business_created activity is logged."""
    created = save_business(
        db=db,
        name="New Discovery Clinic",
        phone="9876543210",
        email="info@newclinic.com",
        website="https://newclinic.com",
        city="Vadodara",
        category="Dentist",
        address="123 Care Street",
        status="Operational",
        place_id="unique_geo_place_12345",
    )
    assert created is True

    biz = db.query(Business).filter(Business.place_id == "unique_geo_place_12345").first()
    assert biz is not None

    acts = get_business_activities(db, biz.id)
    assert len(acts) == 1
    assert acts[0].activity_type == ACTIVITY_BUSINESS_CREATED
    assert acts[0].title == "Business created"


# ============================================================================
# 3. Security, Unicode, XSS & Invariance Tests
# ============================================================================

def test_unicode_and_xss_safety_in_activities(db: Session, sample_business: Business):
    """Verify Unicode scripts and XSS payloads in notes/tags are preserved safely in activity records."""
    xss_payload = "<script>alert('xss')</script> 🔥 ગુજરાતી हिंदी"
    create_activity(
        db=db,
        business_id=sample_business.id,
        activity_type=ACTIVITY_NOTE_CREATED,
        title=f"Note: {xss_payload}",
        description=xss_payload,
        metadata={"payload": xss_payload},
    )

    acts = get_business_activities(db, sample_business.id)
    assert len(acts) == 1
    assert "<script>alert('xss')</script>" in acts[0].description
    assert "ગુજરાતી" in acts[0].description
    assert "🔥" in acts[0].description


def test_activity_invariance_does_not_modify_crm_data(db: Session, sample_business: Business):
    """Activity creation must never mutate lead_score, lead_grade, tags, is_favorite, or notes."""
    orig_score = sample_business.lead_score
    orig_grade = sample_business.lead_grade
    orig_fav = sample_business.is_favorite

    create_activity(
        db=db,
        business_id=sample_business.id,
        activity_type="audit_log",
        title="Invariance Check",
        description="Testing CRM integrity",
    )

    db.refresh(sample_business)
    assert sample_business.lead_score == orig_score
    assert sample_business.lead_grade == orig_grade
    assert sample_business.is_favorite == orig_fav


# ============================================================================
# 4. HTTP API Endpoint Tests
# ============================================================================

def test_get_activities_unauthenticated_returns_401(unauth_client, sample_business: Business):
    """Unauthenticated access to activity history must return 401."""
    resp = unauth_client.get(f"/businesses/{sample_business.id}/activities")
    assert resp.status_code == 401


def test_get_activities_authenticated_success(client, sample_business: Business, db: Session):
    """Authenticated request returns structured, paginated activities."""
    create_activity(
        db=db,
        business_id=sample_business.id,
        activity_type=ACTIVITY_STATUS_CHANGED,
        title="Status Changed",
        description="New → Contacted",
        metadata={"old": "new", "new": "contacted"},
    )

    resp = client.get(f"/businesses/{sample_business.id}/activities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["total"] == 1
    assert data["page"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["activity_type"] == ACTIVITY_STATUS_CHANGED
    assert data["items"][0]["title"] == "Status Changed"
    assert data["items"][0]["metadata"]["new"] == "contacted"


def test_get_activities_nonexistent_business_returns_404(client):
    """Requesting activities for non-existent business returns 404."""
    resp = client.get("/businesses/999999/activities")
    assert resp.status_code == 404
    data = resp.json()
    assert data["success"] is False
    assert "not found" in data["message"].lower()


def test_get_activities_type_filter(client, sample_business: Business, db: Session):
    """Optional activity_type query filter returns only matching records."""
    create_activity(db=db, business_id=sample_business.id, activity_type=ACTIVITY_STATUS_CHANGED, title="Status")
    create_activity(db=db, business_id=sample_business.id, activity_type=ACTIVITY_NOTE_CREATED, title="Note")

    resp = client.get(f"/businesses/{sample_business.id}/activities?activity_type=note_created")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["activity_type"] == ACTIVITY_NOTE_CREATED


def test_get_activities_invalid_type_returns_422(client, sample_business: Business):
    """Passing an unknown activity_type returns 422 Validation Error."""
    resp = client.get(f"/businesses/{sample_business.id}/activities?activity_type=completely_unknown_action")
    assert resp.status_code == 422
    data = resp.json()
    assert data["success"] is False
    assert "invalid activity_type" in data["message"].lower()


def test_note_activity_never_leaks_note_content(db: Session, sample_business: Business):
    """Verifies that note_created and note_updated metadata only contains note_id and never raw note text."""
    secret_text = "CONFIDENTIAL: Client budget is 50 lakhs, password is supersecret123"
    note = create_business_note(db, sample_business.id, secret_text)
    assert note is not None

    acts = get_business_activities(db, sample_business.id, activity_type=ACTIVITY_NOTE_CREATED)
    assert len(acts) == 1
    assert acts[0].metadata_json is not None
    import json
    meta = json.loads(acts[0].metadata_json)
    assert "note_id" in meta
    assert "supersecret123" not in acts[0].metadata_json
    assert secret_text not in acts[0].metadata_json

    updated_secret = "CONFIDENTIAL: Revised budget is 75 lakhs"
    update_business_note(db, note.id, updated_secret)
    acts_updated = get_business_activities(db, sample_business.id, activity_type=ACTIVITY_NOTE_UPDATED)
    assert len(acts_updated) == 1
    assert "75 lakhs" not in acts_updated[0].metadata_json


def test_lead_score_and_scrape_activity_events(db: Session, sample_business: Business):
    """Website scraping saves website data and triggers observational lead_score_changed and scrape_completed."""
    from database.crud import save_website_data
    from services.activity_service import ACTIVITY_LEAD_SCORE_CHANGED, ACTIVITY_SCRAPE_COMPLETED

    orig_score = sample_business.lead_score
    orig_grade = sample_business.lead_grade

    save_website_data(
        db=db,
        business_id=sample_business.id,
        title="Dr. Test Clinic Portal",
        meta_description="Official Dental Clinic",
        emails=["contact@clinicportal.com"],
        facebook="https://facebook.com/testclinic",
        status="Completed",
    )

    acts = get_business_activities(db, sample_business.id)
    act_types = [a.activity_type for a in acts]
    assert ACTIVITY_SCRAPE_COMPLETED in act_types
    # Score changed due to website data
    db.refresh(sample_business)
    if sample_business.lead_score != orig_score or sample_business.lead_grade != orig_grade:
        assert ACTIVITY_LEAD_SCORE_CHANGED in act_types


def test_activity_security_injection_payloads(db: Session, sample_business: Business):
    """Activity titles and descriptions handle SQL injection, Excel formulas, and multi-script Unicode safely."""
    payloads = [
        "'; DROP TABLE businesses; --",
        "=CMD|' /C calc'!A0",
        "@SUM(1+1)*cmd|' /C calc'!A0",
        "+1234567890-cmd|' /C calc'!A0",
        "-1234567890-cmd|' /C calc'!A0",
        "العربية 123",
        "हिन्दी भाषा",
        "ગુજરાતી ટેસ્ટ",
        "🚀🔥💎✨🎉",
        "A" * 500,
    ]
    for p in payloads:
        act = create_activity(
            db=db,
            business_id=sample_business.id,
            activity_type=ACTIVITY_BUSINESS_CREATED,
            title=p[:200],
            description=p,
            metadata={"payload": p},
        )
        assert act is not None
        assert act.description == p

    acts = get_business_activities(db, sample_business.id, page=1, page_size=50)
    assert len(acts) >= len(payloads)


def test_activity_deterministic_id_tiebreaking(db: Session, sample_business: Business):
    """When activities have identical timestamps, id DESC provides deterministic ordering."""
    fixed_time = datetime.now(timezone.utc)
    acts = []
    for i in range(1, 6):
        a = BusinessActivity(
            business_id=sample_business.id,
            activity_type=ACTIVITY_STATUS_CHANGED,
            title=f"Same Time Activity {i}",
            created_at=fixed_time,
        )
        db.add(a)
        acts.append(a)
    db.commit()

    retrieved = get_business_activities(db, sample_business.id, page=1, page_size=10)
    # The last inserted (highest ID) should be first
    assert retrieved[0].id > retrieved[1].id > retrieved[2].id


def test_activity_transaction_rollback_atomicity(db: Session, sample_business: Business):
    """If a transaction is rolled back, created activities are also rolled back cleanly."""
    count_before = get_business_activity_count(db, sample_business.id)
    create_activity(
        db=db,
        business_id=sample_business.id,
        activity_type=ACTIVITY_STATUS_CHANGED,
        title="Uncommitted Status",
        commit=False,
    )
    db.rollback()
    count_after = get_business_activity_count(db, sample_business.id)
    assert count_after == count_before


