"""
Comprehensive tests for CRM Follow-ups API, lifecycle transitions, overdue logic,
activity integration, security/auth, validation, and cascade deletion.
"""

import json
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.models import Business, BusinessFollowUp, BusinessActivity
from services.follow_up_service import (
    cancel_follow_up,
    complete_follow_up,
    create_follow_up,
    delete_follow_up,
    get_follow_up,
    list_follow_ups,
    update_follow_up,
)


@pytest.fixture
def sample_business(db: Session) -> Business:
    """Create a sample business for follow-up testing."""
    biz = Business(
        name="Apex Plumbing Services",
        city="Chicago",
        category="Plumbing",
        phone="+13125550199",
        email="contact@apexplumbing.com",
        website="https://apexplumbing.com",
    )
    db.add(biz)
    db.commit()
    db.refresh(biz)
    return biz


# ============================================================================
# 1. Authentication & Security Quality Gates
# ============================================================================

def test_follow_ups_require_authentication(unauth_client, sample_business):
    """All follow-up endpoints must require valid admin authentication (401)."""
    biz_id = sample_business.id

    # Business scoped
    assert unauth_client.get(f"/businesses/{biz_id}/follow-ups").status_code == 401
    assert unauth_client.post(f"/businesses/{biz_id}/follow-ups", json={"title": "Call owner"}).status_code == 401

    # Global
    assert unauth_client.get("/follow-ups").status_code == 401
    assert unauth_client.get("/follow-ups/1").status_code == 401
    assert unauth_client.patch("/follow-ups/1", json={"title": "Updated"}).status_code == 401
    assert unauth_client.post("/follow-ups/1/complete").status_code == 401
    assert unauth_client.post("/follow-ups/1/cancel").status_code == 401
    assert unauth_client.delete("/follow-ups/1").status_code == 401


# ============================================================================
# 2. Validation & Error Handling (422, 404)
# ============================================================================

def test_create_follow_up_validation_errors(client, sample_business):
    """Invalid payloads must return structured 422 validation errors."""
    biz_id = sample_business.id

    # Missing title
    res = client.post(f"/businesses/{biz_id}/follow-ups", json={})
    assert res.status_code == 422

    # Empty / whitespace-only title
    res = client.post(f"/businesses/{biz_id}/follow-ups", json={"title": "   "})
    assert res.status_code == 422
    data = res.json()
    assert data["success"] is False

    # Title exceeds 255 chars
    res = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "A" * 256},
    )
    assert res.status_code == 422

    # Invalid priority
    res = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Valid title", "priority": "urgent"},
    )
    assert res.status_code == 422


def test_follow_up_invalid_query_filters(client, sample_business):
    """Invalid query filter parameters must return 422."""
    biz_id = sample_business.id

    # Invalid status filter
    res = client.get(f"/businesses/{biz_id}/follow-ups?status=invalid_status")
    assert res.status_code == 422

    # Invalid priority filter
    res = client.get(f"/businesses/{biz_id}/follow-ups?priority=extreme")
    assert res.status_code == 422

    # Global endpoint invalid filters
    res = client.get("/follow-ups?status=wrong")
    assert res.status_code == 422
    res = client.get("/follow-ups?priority=wrong")
    assert res.status_code == 422


def test_follow_up_not_found_404s(client):
    """Missing businesses or follow-ups must return clean 404 NOT_FOUND responses."""
    # Non-existent business
    res = client.get("/businesses/999999/follow-ups")
    assert res.status_code == 404
    assert res.json()["error"] == "NOT_FOUND"

    res = client.post("/businesses/999999/follow-ups", json={"title": "Call"})
    assert res.status_code == 404

    # Non-existent follow-up
    res = client.get("/follow-ups/999999")
    assert res.status_code == 404

    res = client.patch("/follow-ups/999999", json={"title": "Updated"})
    assert res.status_code == 404

    res = client.post("/follow-ups/999999/complete")
    assert res.status_code == 404

    res = client.post("/follow-ups/999999/cancel")
    assert res.status_code == 404

    res = client.delete("/follow-ups/999999")
    assert res.status_code == 404


# ============================================================================
# 3. Complete CRUD & Lifecycle Transitions
# ============================================================================

def test_follow_up_crud_and_lifecycle(client, sample_business, db: Session):
    """Verify create, get, update, complete, cancel, and delete workflows."""
    biz_id = sample_business.id
    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    # 1. Create
    res = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={
            "title": "Send proposal deck",
            "description": "Include pricing tiers and timeline.",
            "due_at": due,
            "priority": "high",
        },
    )
    assert res.status_code == 201
    created_data = res.json()["data"]
    follow_up_id = created_data["id"]
    assert created_data["title"] == "Send proposal deck"
    assert created_data["description"] == "Include pricing tiers and timeline."
    assert created_data["priority"] == "high"
    assert created_data["status"] == "pending"
    assert created_data["is_overdue"] is False
    assert created_data["completed_at"] is None

    # Verify Activity generated
    act = (
        db.query(BusinessActivity)
        .filter(
            BusinessActivity.business_id == biz_id,
            BusinessActivity.activity_type == "follow_up_created",
        )
        .first()
    )
    assert act is not None
    meta = json.loads(act.metadata_json) if act.metadata_json else {}
    assert meta["follow_up_id"] == follow_up_id
    assert meta["title"] == "Send proposal deck"
    assert meta["priority"] == "high"

    # 2. Get single
    res = client.get(f"/follow-ups/{follow_up_id}")
    assert res.status_code == 200
    assert res.json()["data"]["id"] == follow_up_id

    # 3. Update
    res = client.patch(
        f"/follow-ups/{follow_up_id}",
        json={
            "title": "Send updated proposal deck v2",
            "priority": "low",
        },
    )
    assert res.status_code == 200
    updated_data = res.json()["data"]
    assert updated_data["title"] == "Send updated proposal deck v2"
    assert updated_data["priority"] == "low"
    assert updated_data["description"] == "Include pricing tiers and timeline."

    # Verify update activity
    act_update = (
        db.query(BusinessActivity)
        .filter(
            BusinessActivity.business_id == biz_id,
            BusinessActivity.activity_type == "follow_up_updated",
        )
        .first()
    )
    assert act_update is not None
    meta_update = json.loads(act_update.metadata_json) if act_update.metadata_json else {}
    assert "title" in meta_update["changes"]
    assert "priority" in meta_update["changes"]

    # 4. Complete
    res = client.post(f"/follow-ups/{follow_up_id}/complete")
    assert res.status_code == 200
    completed_data = res.json()["data"]
    assert completed_data["status"] == "completed"
    assert completed_data["completed_at"] is not None
    assert completed_data["is_overdue"] is False

    act_comp = (
        db.query(BusinessActivity)
        .filter(
            BusinessActivity.business_id == biz_id,
            BusinessActivity.activity_type == "follow_up_completed",
        )
        .first()
    )
    assert act_comp is not None

    # 5. Cancel
    res = client.post(f"/follow-ups/{follow_up_id}/cancel")
    assert res.status_code == 200
    cancelled_data = res.json()["data"]
    assert cancelled_data["status"] == "cancelled"

    act_canc = (
        db.query(BusinessActivity)
        .filter(
            BusinessActivity.business_id == biz_id,
            BusinessActivity.activity_type == "follow_up_cancelled",
        )
        .first()
    )
    assert act_canc is not None

    # 6. Delete
    res = client.delete(f"/follow-ups/{follow_up_id}")
    assert res.status_code == 200
    assert res.json()["success"] is True

    act_del = (
        db.query(BusinessActivity)
        .filter(
            BusinessActivity.business_id == biz_id,
            BusinessActivity.activity_type == "follow_up_deleted",
        )
        .first()
    )
    assert act_del is not None

    # Confirm record is gone
    res = client.get(f"/follow-ups/{follow_up_id}")
    assert res.status_code == 404


# ============================================================================
# 4. Overdue Logic & Timezone Handling
# ============================================================================

def test_overdue_determination_rules(client, sample_business):
    """is_overdue must be True strictly for pending tasks whose due_at is in the past."""
    biz_id = sample_business.id
    past_due = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    future_due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    # Past due pending -> Overdue
    res_past = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Past task", "due_at": past_due},
    )
    past_id = res_past.json()["data"]["id"]
    assert res_past.json()["data"]["is_overdue"] is True

    # Future due pending -> Not overdue
    res_fut = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Future task", "due_at": future_due},
    )
    fut_id = res_fut.json()["data"]["id"]
    assert res_fut.json()["data"]["is_overdue"] is False

    # No due date -> Not overdue
    res_none = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "No date task"},
    )
    assert res_none.json()["data"]["is_overdue"] is False

    # Filter overdue=true
    res_overdue_filter = client.get(f"/businesses/{biz_id}/follow-ups?overdue=true")
    assert res_overdue_filter.status_code == 200
    overdue_items = res_overdue_filter.json()["items"]
    assert len(overdue_items) == 1
    assert overdue_items[0]["id"] == past_id

    # Once completed, past task is no longer overdue
    client.post(f"/follow-ups/{past_id}/complete")
    res_get_completed = client.get(f"/follow-ups/{past_id}")
    assert res_get_completed.json()["data"]["is_overdue"] is False

    # Filter overdue=true now returns 0
    res_overdue_filter2 = client.get(f"/businesses/{biz_id}/follow-ups?overdue=true")
    assert len(res_overdue_filter2.json()["items"]) == 0


# ============================================================================
# 5. Filtering, Pagination, and Ordering
# ============================================================================

def test_follow_up_filtering_and_pagination(client, sample_business):
    """Test filtering by status, priority, business_id, and server-side pagination."""
    biz_id = sample_business.id

    # Seed 6 follow-ups
    for i in range(1, 7):
        prio = "high" if i <= 2 else ("medium" if i <= 4 else "low")
        client.post(
            f"/businesses/{biz_id}/follow-ups",
            json={
                "title": f"Task {i}",
                "priority": prio,
                "due_at": (datetime.now(timezone.utc) + timedelta(days=i)).isoformat(),
            },
        )

    # List page 1 with page_size=3
    res = client.get(f"/businesses/{biz_id}/follow-ups?page=1&page_size=3")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 6
    assert data["page"] == 1
    assert data["page_size"] == 3
    assert data["total_pages"] == 2
    assert len(data["items"]) == 3

    # Priority filter: high
    res_high = client.get(f"/businesses/{biz_id}/follow-ups?priority=high")
    assert res_high.json()["total"] == 2
    for item in res_high.json()["items"]:
        assert item["priority"] == "high"

    # Global list filter by business_id
    res_global = client.get(f"/follow-ups?business_id={biz_id}")
    assert res_global.json()["total"] == 6


# ============================================================================
# 6. Idempotency & No-op Mutations
# ============================================================================

def test_no_op_update_generates_no_activity(client, sample_business, db: Session):
    """Updating a follow-up with unchanged values should not generate spurious activity entries."""
    biz_id = sample_business.id

    res = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Follow up with CEO", "priority": "medium"},
    )
    follow_up_id = res.json()["data"]["id"]

    initial_activity_count = (
        db.query(BusinessActivity)
        .filter(BusinessActivity.business_id == biz_id)
        .count()
    )

    # Patch with exact same title and priority
    client.patch(
        f"/follow-ups/{follow_up_id}",
        json={"title": "Follow up with CEO", "priority": "medium"},
    )

    new_activity_count = (
        db.query(BusinessActivity)
        .filter(BusinessActivity.business_id == biz_id)
        .count()
    )
    assert new_activity_count == initial_activity_count


# ============================================================================
# 7. Cascade Deletion Integrity
# ============================================================================

def test_business_deletion_cascades_follow_ups(client, sample_business, db: Session):
    """Deleting a business must cascade-delete all associated follow-ups."""
    biz_id = sample_business.id

    client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Follow up 1"},
    )
    client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Follow up 2"},
    )

    count_before = db.query(BusinessFollowUp).filter(BusinessFollowUp.business_id == biz_id).count()
    assert count_before == 2

    # Delete business
    res = client.delete(f"/businesses/{biz_id}")
    assert res.status_code == 200

    # Follow-ups should be cascaded
    count_after = db.query(BusinessFollowUp).filter(BusinessFollowUp.business_id == biz_id).count()
    assert count_after == 0


# ============================================================================
# 8. Unicode, XSS, and Special Character Safety
# ============================================================================

def test_follow_up_unicode_and_xss_safety(client, sample_business):
    """Titles and descriptions with Unicode, emojis, and HTML/XSS payloads must be stored safely."""
    biz_id = sample_business.id
    xss_title = "<script>alert('xss')</script> & 🚀 Follow-up 🎯 — 中文 / العربية"
    xss_desc = "<b>Important</b> <div>notes</div> &amp; quotes ' \" `"

    res = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={
            "title": xss_title,
            "description": xss_desc,
            "priority": "high",
        },
    )
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["title"] == xss_title
    assert data["description"] == xss_desc


# ============================================================================
# 9. Duplicate Completion / Cancellation Idempotency
# ============================================================================

def test_duplicate_completion_and_cancellation_no_extra_activity(client, sample_business, db: Session):
    """Completing or cancelling an already completed/cancelled follow-up must not generate duplicate activity."""
    biz_id = sample_business.id

    # Create follow-up
    res = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Client follow-up call"},
    )
    fu_id = res.json()["data"]["id"]

    # 1. Complete first time
    res_comp1 = client.post(f"/follow-ups/{fu_id}/complete")
    assert res_comp1.status_code == 200
    activities_after_comp1 = (
        db.query(BusinessActivity)
        .filter(BusinessActivity.business_id == biz_id, BusinessActivity.activity_type == "follow_up_completed")
        .count()
    )
    assert activities_after_comp1 == 1

    # Complete second time (duplicate)
    res_comp2 = client.post(f"/follow-ups/{fu_id}/complete")
    assert res_comp2.status_code == 200
    activities_after_comp2 = (
        db.query(BusinessActivity)
        .filter(BusinessActivity.business_id == biz_id, BusinessActivity.activity_type == "follow_up_completed")
        .count()
    )
    assert activities_after_comp2 == 1  # No duplicate activity!

    # 2. Test Cancellation Idempotency
    res_fu2 = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Cancelled proposal follow-up"},
    )
    fu2_id = res_fu2.json()["data"]["id"]

    # Cancel first time
    client.post(f"/follow-ups/{fu2_id}/cancel")
    activities_after_canc1 = (
        db.query(BusinessActivity)
        .filter(BusinessActivity.business_id == biz_id, BusinessActivity.activity_type == "follow_up_cancelled")
        .count()
    )
    assert activities_after_canc1 == 1

    # Cancel second time (duplicate)
    client.post(f"/follow-ups/{fu2_id}/cancel")
    activities_after_canc2 = (
        db.query(BusinessActivity)
        .filter(BusinessActivity.business_id == biz_id, BusinessActivity.activity_type == "follow_up_cancelled")
        .count()
    )
    assert activities_after_canc2 == 1  # No duplicate activity!


# ============================================================================
# 10. Cross-Business Isolation
# ============================================================================

def test_cross_business_isolation_guarantee(client, sample_business, db: Session):
    """Follow-ups belonging to Business A must never appear in Business B follow-ups query."""
    biz_a_id = sample_business.id

    biz_b = Business(
        name="Beacon Electric Inc",
        city="Chicago",
        category="Electrician",
    )
    db.add(biz_b)
    db.commit()
    db.refresh(biz_b)
    biz_b_id = biz_b.id

    # Add follow-up to Business A
    client.post(
        f"/businesses/{biz_a_id}/follow-ups",
        json={"title": "Follow up with Business A"},
    )

    # Query Business B follow-ups
    res_b = client.get(f"/businesses/{biz_b_id}/follow-ups")
    assert res_b.status_code == 200
    assert res_b.json()["total"] == 0
    assert len(res_b.json()["items"]) == 0

    # Add follow-up to Business B
    client.post(
        f"/businesses/{biz_b_id}/follow-ups",
        json={"title": "Follow up with Business B"},
    )

    # Verify both isolated
    res_a = client.get(f"/businesses/{biz_a_id}/follow-ups")
    assert res_a.json()["total"] == 1
    assert res_a.json()["items"][0]["title"] == "Follow up with Business A"

    res_b2 = client.get(f"/businesses/{biz_b_id}/follow-ups")
    assert res_b2.json()["total"] == 1
    assert res_b2.json()["items"][0]["title"] == "Follow up with Business B"


# ============================================================================
# 11. Multilingual Support (Hindi, Gujarati, Arabic, Emoji)
# ============================================================================

def test_multilingual_unicode_support(client, sample_business):
    """Verify full support for Hindi, Gujarati, Arabic, and emojis in CRM follow-ups."""
    biz_id = sample_business.id

    multilingual_cases = [
        {"title": "ग्राहक को कॉल करें 🚀", "desc": "प्रस्ताव पर चर्चा करनी है 📋"},  # Hindi
        {"title": "ગ્રાહક સાથે મીટિંગ 🎯", "desc": "કોન્ટ્રાક્ટ ફાઇનલ કરવાનો છે ✍️"},  # Gujarati
        {"title": "متابعة مع العميل 💼", "desc": "إرسال عرض الأسعار المحدث 📑"},  # Arabic
    ]

    for case in multilingual_cases:
        res = client.post(
            f"/businesses/{biz_id}/follow-ups",
            json={"title": case["title"], "description": case["desc"]},
        )
        assert res.status_code == 201
        data = res.json()["data"]
        assert data["title"] == case["title"]
        assert data["description"] == case["desc"]

        # Fetch directly
        res_get = client.get(f"/follow-ups/{data['id']}")
        assert res_get.status_code == 200
        assert res_get.json()["data"]["title"] == case["title"]


# ============================================================================
# 12. Deleted Follow-ups Cannot Be Mutated
# ============================================================================

def test_deleted_follow_up_cannot_be_mutated(client, sample_business):
    """Once deleted, a follow-up cannot be updated, completed, cancelled, or re-deleted."""
    biz_id = sample_business.id
    res = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Temporary task"},
    )
    fu_id = res.json()["data"]["id"]

    # Delete
    assert client.delete(f"/follow-ups/{fu_id}").status_code == 200

    # Subsequent mutations must return 404
    assert client.get(f"/follow-ups/{fu_id}").status_code == 404
    assert client.patch(f"/follow-ups/{fu_id}", json={"title": "Zombie"}).status_code == 404
    assert client.post(f"/follow-ups/{fu_id}/complete").status_code == 404
    assert client.post(f"/follow-ups/{fu_id}/cancel").status_code == 404
    assert client.delete(f"/follow-ups/{fu_id}").status_code == 404


# ============================================================================
# 13. Overdue Boundary Conditions
# ============================================================================

def test_overdue_boundary_conditions(client, sample_business):
    """Test exact now boundary and future boundary for overdue computation."""
    biz_id = sample_business.id
    now_utc = datetime.now(timezone.utc)

    # 10 minutes in future -> Not overdue
    res_future = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Future task", "due_at": (now_utc + timedelta(minutes=10)).isoformat()},
    )
    assert res_future.json()["data"]["is_overdue"] is False

    # 10 minutes in past -> Overdue
    res_past = client.post(
        f"/businesses/{biz_id}/follow-ups",
        json={"title": "Past task", "due_at": (now_utc - timedelta(minutes=10)).isoformat()},
    )
    assert res_past.json()["data"]["is_overdue"] is True

