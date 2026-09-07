import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database.models import Business
from services.favorite_service import (
    bulk_set_favorites,
    get_favorite_count,
    set_business_favorite,
    toggle_business_favorite,
)
from services.lead_scoring import calculate_lead_score
from services.tag_service import attach_tag_to_business, create_tag


def test_business_favorite_default_false(db: Session):
    b = Business(name="Default Business")
    db.add(b)
    db.commit()
    db.refresh(b)
    assert b.is_favorite is False


def test_favorite_service_operations(db: Session):
    b1 = Business(name="Biz 1", is_favorite=False)
    b2 = Business(name="Biz 2", is_favorite=True)
    db.add_all([b1, b2])
    db.commit()

    # Idempotent set True
    res1 = set_business_favorite(db, b1.id, True)
    assert res1.is_favorite is True
    res1_again = set_business_favorite(db, b1.id, True)
    assert res1_again.is_favorite is True

    # Idempotent set False
    res2 = set_business_favorite(db, b2.id, False)
    assert res2.is_favorite is False
    res2_again = set_business_favorite(db, b2.id, False)
    assert res2_again.is_favorite is False

    # Toggle
    toggled = toggle_business_favorite(db, b1.id)
    assert toggled.is_favorite is False

    # Favorite count
    set_business_favorite(db, b1.id, True)
    set_business_favorite(db, b2.id, True)
    assert get_favorite_count(db) == 2

    # Bulk set
    bulk_res = bulk_set_favorites(db, [b1.id, b2.id, 99999], is_favorite=False)
    assert bulk_res["updated_count"] == 2
    assert bulk_res["total_requested"] == 3
    assert get_favorite_count(db) == 0


def test_favorite_api_crud_and_idempotency(client: TestClient, db: Session):
    b = Business(name="Target Company", is_favorite=False)
    db.add(b)
    db.commit()

    # 1. PATCH favorite
    res_patch = client.patch(f"/businesses/{b.id}/favorite", json={"is_favorite": True})
    assert res_patch.status_code == 200
    assert res_patch.json()["is_favorite"] is True

    # 2. Re-patching is idempotent
    res_patch_dup = client.patch(f"/businesses/{b.id}/favorite", json={"is_favorite": True})
    assert res_patch_dup.status_code == 200
    assert res_patch_dup.json()["is_favorite"] is True

    # 3. DELETE favorite
    res_del = client.delete(f"/businesses/{b.id}/favorite")
    assert res_del.status_code == 200
    assert res_del.json()["is_favorite"] is False

    # 4. POST favorite
    res_post = client.post(f"/businesses/{b.id}/favorite")
    assert res_post.status_code == 200
    assert res_post.json()["is_favorite"] is True

    # 5. Non-existent business 404
    assert client.patch("/businesses/999999/favorite", json={"is_favorite": True}).status_code == 404
    assert client.post("/businesses/999999/favorite").status_code == 404
    assert client.delete("/businesses/999999/favorite").status_code == 404


def test_bulk_favorite_api(client: TestClient, db: Session):
    b1 = Business(name="Biz A", is_favorite=False)
    b2 = Business(name="Biz B", is_favorite=False)
    b3 = Business(name="Biz C", is_favorite=True)
    db.add_all([b1, b2, b3])
    db.commit()

    # Bulk favorite b1, b2, b3, 99999
    res = client.post(
        "/businesses/favorite/bulk",
        json={"business_ids": [b1.id, b2.id, b3.id, 99999], "is_favorite": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["updated_count"] == 2  # b1, b2 updated (b3 was already true, 99999 ignored)
    assert data["total_requested"] == 4

    # Bulk unfavorite
    res_unfav = client.post(
        "/businesses/favorite/bulk",
        json={"business_ids": [b1.id, b2.id, b3.id], "is_favorite": False},
    )
    assert res_unfav.status_code == 200
    assert res_unfav.json()["updated_count"] == 3


def test_favorite_filtering_and_combinations(client: TestClient, db: Session):
    tag = create_tag(db, "Hot Lead")

    b_fav_tag = Business(name="Dental Alpha", city="Ahmedabad", is_favorite=True, lead_score=85, lead_grade="A")
    b_fav_notag = Business(name="Dental Beta", city="Ahmedabad", is_favorite=True, lead_score=65, lead_grade="B")
    b_notfav = Business(name="Dental Gamma", city="Surat", is_favorite=False, lead_score=90, lead_grade="A")
    db.add_all([b_fav_tag, b_fav_notag, b_notfav])
    db.commit()

    attach_tag_to_business(db, b_fav_tag.id, tag_id=tag.id)

    # 1. Filter is_favorite=true
    res_fav = client.get("/businesses?is_favorite=true")
    assert res_fav.status_code == 200
    fav_names = {b["name"] for b in res_fav.json()["data"]}
    assert fav_names == {"Dental Alpha", "Dental Beta"}

    # 2. Filter is_favorite=false
    res_notfav = client.get("/businesses?is_favorite=false")
    assert res_notfav.status_code == 200
    notfav_names = {b["name"] for b in res_notfav.json()["data"]}
    assert "Dental Gamma" in notfav_names
    assert "Dental Alpha" not in notfav_names

    # 3. Combined with tag and city
    res_combo = client.get("/businesses?is_favorite=true&tags=hot-lead&city=Ahmedabad")
    assert res_combo.status_code == 200
    combo_names = [b["name"] for b in res_combo.json()["data"]]
    assert combo_names == ["Dental Alpha"]

    # 4. Sorting by is_favorite
    res_sort = client.get("/businesses?sortBy=is_favorite&sortOrder=desc")
    assert res_sort.status_code == 200
    first_item = res_sort.json()["data"][0]
    assert first_item["is_favorite"] is True


def test_csv_export_includes_favorite_column(client: TestClient, db: Session):
    b1 = Business(name="Fav Client", is_favorite=True)
    b2 = Business(name="Normal Client", is_favorite=False)
    db.add_all([b1, b2])
    db.commit()

    res = client.get("/businesses/export/csv")
    assert res.status_code == 200
    assert "Favorite" in res.text
    assert "Fav Client" in res.text
    assert "Yes" in res.text
    assert "No" in res.text


def test_lead_score_and_tags_invariance_on_favorite(client: TestClient, db: Session):
    tag = create_tag(db, "Priority Client")
    b = Business(
        name="Clinic X",
        website="https://clinic-x.com",
        phone="+91 99999 88888",
        email="info@clinic-x.com",
    )
    score_res = calculate_lead_score(b)
    b.lead_score = score_res.score
    b.lead_grade = score_res.grade
    b.lead_score_reasons = score_res.reasons
    db.add(b)
    db.commit()

    attach_tag_to_business(db, b.id, tag_id=tag.id)

    initial_score = b.lead_score
    initial_grade = b.lead_grade
    initial_reasons = list(b.lead_score_reasons)

    # Favorite business
    client.post(f"/businesses/{b.id}/favorite")
    db.refresh(b)
    assert b.is_favorite is True
    assert b.lead_score == initial_score
    assert b.lead_grade == initial_grade
    assert b.lead_score_reasons == initial_reasons
    assert len(b.tags) == 1
    assert b.tags[0].name == "Priority Client"

    # Unfavorite business
    client.delete(f"/businesses/{b.id}/favorite")
    db.refresh(b)
    assert b.is_favorite is False
    assert b.lead_score == initial_score
    assert b.lead_grade == initial_grade
    assert b.lead_score_reasons == initial_reasons
    assert len(b.tags) == 1


def test_unauthenticated_favorite_endpoints_rejected(unauth_client: TestClient):
    assert unauth_client.patch("/businesses/1/favorite", json={"is_favorite": True}).status_code == 401
    assert unauth_client.post("/businesses/1/favorite").status_code == 401
    assert unauth_client.delete("/businesses/1/favorite").status_code == 401
    assert unauth_client.post("/businesses/favorite/bulk", json={"business_ids": [1], "is_favorite": True}).status_code == 401


def test_bulk_favorite_edge_cases(client: TestClient, db: Session):
    b1 = Business(name="Edge Biz", is_favorite=False)
    db.add(b1)
    db.commit()

    # 1. Empty list
    res_empty = client.post("/businesses/favorite/bulk", json={"business_ids": [], "is_favorite": True})
    assert res_empty.status_code == 200
    assert res_empty.json()["updated_count"] == 0
    assert res_empty.json()["total_requested"] == 0

    # 2. Duplicate IDs
    res_dup = client.post("/businesses/favorite/bulk", json={"business_ids": [b1.id, b1.id, b1.id], "is_favorite": True})
    assert res_dup.status_code == 200
    assert res_dup.json()["updated_count"] == 1
    assert res_dup.json()["total_requested"] == 1

    # 3. Mixed valid and non-existent IDs
    res_mixed = client.post("/businesses/favorite/bulk", json={"business_ids": [b1.id, 999999, 888888], "is_favorite": False})
    assert res_mixed.status_code == 200
    assert res_mixed.json()["updated_count"] == 1
    assert res_mixed.json()["total_requested"] == 3


def test_favorites_filtered_csv_export(client: TestClient, db: Session):
    b1 = Business(name="Fav Only 1", is_favorite=True)
    b2 = Business(name="Fav Only 2", is_favorite=True)
    b3 = Business(name="Not Fav 3", is_favorite=False)
    db.add_all([b1, b2, b3])
    db.commit()

    res = client.get("/businesses/export/csv?is_favorite=true")
    assert res.status_code == 200
    assert "Fav Only 1" in res.text
    assert "Fav Only 2" in res.text
    assert "Not Fav 3" not in res.text

