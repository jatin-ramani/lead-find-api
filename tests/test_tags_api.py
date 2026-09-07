import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from config import settings
from database.models import Business, Tag
from services.lead_scoring import calculate_lead_score
from services.tag_service import attach_tag_to_business, create_tag, get_tags


def test_tag_crud_api(client: TestClient, db: Session):
    # 1. Create tag
    create_res = client.post("/tags", json={"name": "Hot Lead"})
    assert create_res.status_code == 201
    tag = create_res.json()
    assert tag["name"] == "Hot Lead"
    assert tag["slug"] == "hot-lead"
    tag_id = tag["id"]

    # Duplicate create returns 409
    dup_res = client.post("/tags", json={"name": "hot lead"})
    assert dup_res.status_code == 409

    # 2. List tags
    list_res = client.get("/tags")
    assert list_res.status_code == 200
    tags = list_res.json()["data"]
    assert any(t["id"] == tag_id for t in tags)

    # 3. Rename tag
    patch_res = client.patch(f"/tags/{tag_id}", json={"name": "Super Hot Lead"})
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Super Hot Lead"
    assert patch_res.json()["slug"] == "super-hot-lead"

    # 4. Delete tag
    del_res = client.delete(f"/tags/{tag_id}")
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] == 1

    # Get non-existent
    del_again = client.delete(f"/tags/{tag_id}")
    assert del_again.status_code == 404


def test_business_tag_assignment_and_filtering(client: TestClient, db: Session):
    t_hot = create_tag(db, "Hot Target")
    t_dental = create_tag(db, "Dental Opportunity")

    b1 = Business(name="Dental Alpha", city="Ahmedabad", lead_score=85, lead_grade="A")
    b2 = Business(name="Dental Beta", city="Ahmedabad", lead_score=65, lead_grade="B")
    b3 = Business(name="Cafe Gamma", city="Surat", lead_score=45, lead_grade="C")
    db.add_all([b1, b2, b3])
    db.commit()

    # Attach tags
    attach_tag_to_business(db, b1.id, tag_id=t_hot.id)
    attach_tag_to_business(db, b1.id, tag_id=t_dental.id)
    attach_tag_to_business(db, b2.id, tag_id=t_dental.id)

    # 1. Single tag filter via slug
    res_hot = client.get("/businesses?tags=hot-target")
    assert res_hot.status_code == 200
    hot_names = [b["name"] for b in res_hot.json()["data"]]
    assert hot_names == ["Dental Alpha"]

    # 2. Multi-tag AND filter
    res_both = client.get("/businesses?tags=hot-target,dental-opportunity")
    assert res_both.status_code == 200
    both_names = [b["name"] for b in res_both.json()["data"]]
    assert both_names == ["Dental Alpha"]

    # 3. Combined with Grade filter
    res_grade_a_dental = client.get("/businesses?tags=dental-opportunity&lead_grade=B")
    assert res_grade_a_dental.status_code == 200
    grade_b_names = [b["name"] for b in res_grade_a_dental.json()["data"]]
    assert grade_b_names == ["Dental Beta"]

    # 4. Business detail includes tags
    res_detail = client.get(f"/businesses/{b1.id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert "tags" in detail
    assert len(detail["tags"]) == 2
    tag_slugs = [t["slug"] for t in detail["tags"]]
    assert "hot-target" in tag_slugs
    assert "dental-opportunity" in tag_slugs

    # 5. Remove tag via API
    del_tag_res = client.delete(f"/businesses/{b1.id}/tags/{t_hot.id}")
    assert del_tag_res.status_code == 200
    assert del_tag_res.json()["deleted"] == 1

    # Detail now has 1 tag
    detail_after = client.get(f"/businesses/{b1.id}").json()
    assert len(detail_after["tags"]) == 1


def test_bulk_tagging_api(client: TestClient, db: Session):
    t_bulk = create_tag(db, "Bulk Action Tag")
    b1 = Business(name="Biz 1")
    b2 = Business(name="Biz 2")
    db.add_all([b1, b2])
    db.commit()

    # Bulk attach
    attach_res = client.post(
        "/businesses/tags/bulk",
        json={"business_ids": [b1.id, b2.id], "tag_id": t_bulk.id},
    )
    assert attach_res.status_code == 200
    assert attach_res.json()["updated_count"] == 2

    # Bulk remove
    remove_res = client.post(
        "/businesses/tags/bulk-remove",
        json={"business_ids": [b1.id, b2.id], "tag_id": t_bulk.id},
    )
    assert remove_res.status_code == 200
    assert remove_res.json()["updated_count"] == 2


def test_csv_export_includes_tags(client: TestClient, db: Session):
    tag1 = create_tag(db, "VIP Client")
    tag2 = create_tag(db, "Website Needed")
    b = Business(name="VIP Company")
    db.add(b)
    db.commit()
    attach_tag_to_business(db, b.id, tag_id=tag1.id)
    attach_tag_to_business(db, b.id, tag_id=tag2.id)

    res = client.get("/businesses/export/csv")
    assert res.status_code == 200
    assert "Tags" in res.text
    assert "VIP Client, Website Needed" in res.text or ("VIP Client" in res.text and "Website Needed" in res.text)


def test_malicious_strings_and_unicode_tags(client: TestClient, db: Session):
    # Malicious XSS / SQL injection strings are safely stored as plain text
    xss_name = "<script>alert(1)</script>"
    res_xss = client.post("/tags", json={"name": xss_name})
    assert res_xss.status_code == 201
    assert res_xss.json()["name"] == xss_name

    # Multilingual Unicode: Gujarati, Hindi, Arabic, Emoji
    gujarati_name = "અમદાવાદ સ્પેશ્યલ"
    res_guj = client.post("/tags", json={"name": gujarati_name})
    assert res_guj.status_code == 201
    assert res_guj.json()["name"] == gujarati_name
    assert res_guj.json()["slug"] == "અમદાવાદ-સ્પેશ્યલ"

    hindi_name = "प्राथमिकता लीड"
    res_hin = client.post("/tags", json={"name": hindi_name})
    assert res_hin.status_code == 201
    assert res_hin.json()["name"] == hindi_name

    emoji_name = "🔥 High Priority"
    res_emo = client.post("/tags", json={"name": emoji_name})
    assert res_emo.status_code == 201
    assert res_emo.json()["name"] == emoji_name


def test_lead_scoring_invariance_with_tags(client: TestClient, db: Session):
    # Verify that attaching or removing tags never mutates lead scoring or grade
    b = Business(
        name="Scored Biz",
        website="https://scored.example.com",
        phone="+91 9876543210",
        email="contact@scored.example.com",
    )
    score_res = calculate_lead_score(b)
    b.lead_score = score_res.score
    b.lead_grade = score_res.grade
    b.lead_score_reasons = score_res.reasons
    db.add(b)
    db.commit()

    initial_score = b.lead_score
    initial_grade = b.lead_grade
    initial_reasons = list(b.lead_score_reasons)

    # Attach tag
    t = create_tag(db, "Scoring Invariance Tag")
    attach_tag_to_business(db, b.id, tag_id=t.id)

    db.refresh(b)
    assert b.lead_score == initial_score
    assert b.lead_grade == initial_grade
    assert b.lead_score_reasons == initial_reasons

    # Remove tag
    del_res = client.delete(f"/businesses/{b.id}/tags/{t.id}")
    assert del_res.status_code == 200

    db.refresh(b)
    assert b.lead_score == initial_score
    assert b.lead_grade == initial_grade
    assert b.lead_score_reasons == initial_reasons


def test_unauthenticated_tag_endpoints_rejected(unauth_client: TestClient):
    # Protected tag routes must return 401 when unauthenticated
    assert unauth_client.get("/tags").status_code == 401
    assert unauth_client.post("/tags", json={"name": "Secret"}).status_code == 401
    assert unauth_client.patch("/tags/1", json={"name": "Secret"}).status_code == 401
    assert unauth_client.delete("/tags/1").status_code == 401
    assert unauth_client.post("/businesses/1/tags", json={"name": "Secret"}).status_code == 401
    assert unauth_client.delete("/businesses/1/tags/1").status_code == 401
    assert unauth_client.post("/businesses/tags/bulk", json={"business_ids": [1], "tag_name": "X"}).status_code == 401
    assert unauth_client.post("/businesses/tags/bulk-remove", json={"business_ids": [1], "tag_id": 1}).status_code == 401

