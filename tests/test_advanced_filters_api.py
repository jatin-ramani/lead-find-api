"""
Unit, Integration, Validation, Security, and Regression Tests for
Phase 1, Feature 6.1 — Advanced Filters Backend Architecture.
"""
from typing import List
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database.models import Business
from services.lead_scoring import calculate_lead_score
from services.note_service import create_business_note
from services.tag_service import attach_tag_to_business, create_tag


@pytest.fixture
def populated_filter_db(db: Session) -> dict:
    """Create a rich, deterministic set of businesses spanning all filter dimensions."""
    tag_hot = create_tag(db, "Hot")
    tag_priority = create_tag(db, "Priority")
    tag_enterprise = create_tag(db, "Enterprise")

    # Business 1: Ahmedabad, Dental, No Website, Has Email & Phone, Grade A, Score 95, Favorite, Status Contacted, Tags: Hot, Priority
    b1 = Business(
        name="Apex Dental Care",
        city="Ahmedabad",
        category="Dental",
        website=None,
        email="contact@apexdental.test",
        phone="+91 98765 00001",
        lead_score=95,
        lead_grade="A",
        is_favorite=True,
        lead_status="contacted",
    )

    # Business 2: Ahmedabad, Dental, Has Website, Has Email, No Phone, Grade B, Score 75, Not Favorite, Status Interested, Tags: Hot
    b2 = Business(
        name="Smile Craft Clinic",
        city="Ahmedabad",
        category="Dental",
        website="https://smilecraft.test",
        email="info@smilecraft.test",
        phone=None,
        lead_score=75,
        lead_grade="B",
        is_favorite=False,
        lead_status="interested",
    )

    # Business 3: Surat, Restaurant, Has Website, Has Phone, No Email, Grade C, Score 55, Favorite, Status Follow-up, Tags: Priority
    b3 = Business(
        name="Surat Spice Villa",
        city="Surat",
        category="Restaurant",
        website="https://suratspice.test",
        email=None,
        phone="+91 98765 00003",
        lead_score=55,
        lead_grade="C",
        is_favorite=True,
        lead_status="follow_up",
    )

    # Business 4: Surat, Healthcare, No Website, No Email, Has Phone, Grade D, Score 30, Not Favorite, Status New, Tags: Enterprise
    b4 = Business(
        name="Surat Diagnostic Lab",
        city="Surat",
        category="Healthcare",
        website=None,
        email=None,
        phone="+91 98765 00004",
        lead_score=30,
        lead_grade="D",
        is_favorite=False,
        lead_status="new",
    )

    # Business 5: Rajkot, Retail, Has Website, Has Email & Phone, Grade A, Score 85, Favorite, Status Converted, Tags: Hot, Priority, Enterprise
    b5 = Business(
        name="Rajkot Mega Mart",
        city="Rajkot",
        category="Retail",
        website="https://rajkotmart.test",
        email="sales@rajkotmart.test",
        phone="+91 98765 00005",
        lead_score=85,
        lead_grade="A",
        is_favorite=True,
        lead_status="converted",
    )

    # Business 6: Vadodara, Retail, No Website, No Email, No Phone, Grade D, Score 10, Not Favorite, Status Lost, Tags: []
    b6 = Business(
        name="Vadodara General Store",
        city="Vadodara",
        category="Retail",
        website="",
        email="",
        phone="",
        lead_score=10,
        lead_grade="D",
        is_favorite=False,
        lead_status="lost",
    )

    db.add_all([b1, b2, b3, b4, b5, b6])
    db.commit()

    attach_tag_to_business(db, b1.id, tag_id=tag_hot.id)
    attach_tag_to_business(db, b1.id, tag_id=tag_priority.id)

    attach_tag_to_business(db, b2.id, tag_id=tag_hot.id)

    attach_tag_to_business(db, b3.id, tag_id=tag_priority.id)

    attach_tag_to_business(db, b4.id, tag_id=tag_enterprise.id)

    attach_tag_to_business(db, b5.id, tag_id=tag_hot.id)
    attach_tag_to_business(db, b5.id, tag_id=tag_priority.id)
    attach_tag_to_business(db, b5.id, tag_id=tag_enterprise.id)

    create_business_note(db, b1.id, "Top tier lead in Ahmedabad.")

    return {
        "b1": b1,
        "b2": b2,
        "b3": b3,
        "b4": b4,
        "b5": b5,
        "b6": b6,
        "tag_hot": tag_hot,
        "tag_priority": tag_priority,
        "tag_enterprise": tag_enterprise,
    }


# ============================================================================
# 1. INDIVIDUAL FILTERS
# ============================================================================

def test_individual_filter_search(client: TestClient, populated_filter_db: dict):
    # Search by name
    res = client.get("/businesses?search=Apex")
    assert res.status_code == 200
    names = [b["name"] for b in res.json()["data"]]
    assert names == ["Apex Dental Care"]

    # Search by email
    res_email = client.get("/businesses?search=smilecraft.test")
    assert res_email.status_code == 200
    assert len(res_email.json()["data"]) == 1
    assert res_email.json()["data"][0]["name"] == "Smile Craft Clinic"


def test_individual_filter_city_and_category(client: TestClient, populated_filter_db: dict):
    res_city = client.get("/businesses?city=Ahmedabad")
    assert res_city.status_code == 200
    assert len(res_city.json()["data"]) == 2

    res_cat = client.get("/businesses?category=Retail")
    assert res_cat.status_code == 200
    assert len(res_cat.json()["data"]) == 2


def test_individual_filter_has_website_boolean(client: TestClient, populated_filter_db: dict):
    res_true = client.get("/businesses?has_website=true")
    assert res_true.status_code == 200
    assert len(res_true.json()["data"]) == 3  # b2, b3, b5

    res_false = client.get("/businesses?has_website=false")
    assert res_false.status_code == 200
    assert len(res_false.json()["data"]) == 3  # b1, b4, b6


def test_individual_filter_has_email_boolean(client: TestClient, populated_filter_db: dict):
    res_true = client.get("/businesses?has_email=true")
    assert res_true.status_code == 200
    assert len(res_true.json()["data"]) == 3  # b1, b2, b5

    res_false = client.get("/businesses?has_email=false")
    assert res_false.status_code == 200
    assert len(res_false.json()["data"]) == 3  # b3, b4, b6


def test_individual_filter_has_phone_boolean(client: TestClient, populated_filter_db: dict):
    res_true = client.get("/businesses?has_phone=true")
    assert res_true.status_code == 200
    assert len(res_true.json()["data"]) == 4  # b1, b3, b4, b5

    res_false = client.get("/businesses?has_phone=false")
    assert res_false.status_code == 200
    assert len(res_false.json()["data"]) == 2  # b2, b6


def test_individual_filter_lead_grade(client: TestClient, populated_filter_db: dict):
    for grade, expected_count in [("A", 2), ("B", 1), ("C", 1), ("D", 2)]:
        res = client.get(f"/businesses?lead_grade={grade}")
        assert res.status_code == 200
        assert len(res.json()["data"]) == expected_count

    # Lowercase grade should be accepted and normalized
    res_lower = client.get("/businesses?lead_grade=a")
    assert res_lower.status_code == 200
    assert len(res_lower.json()["data"]) == 2


def test_individual_filter_lead_score_boundaries(client: TestClient, populated_filter_db: dict):
    # min_lead_score >= 80 (b1: 95, b5: 85)
    res_min = client.get("/businesses?min_lead_score=80")
    assert res_min.status_code == 200
    assert len(res_min.json()["data"]) == 2

    # max_lead_score <= 55 (b3: 55, b4: 30, b6: 10)
    res_max = client.get("/businesses?max_lead_score=55")
    assert res_max.status_code == 200
    assert len(res_max.json()["data"]) == 3

    # score range 55 to 85 inclusive (b3: 55, b2: 75, b5: 85)
    res_range = client.get("/businesses?min_lead_score=55&max_lead_score=85")
    assert res_range.status_code == 200
    assert len(res_range.json()["data"]) == 3


def test_individual_filter_tags_single_and_multi_and_semantics(client: TestClient, populated_filter_db: dict):
    # Single tag: Hot (b1, b2, b5)
    res_single = client.get("/businesses?tags=hot")
    assert res_single.status_code == 200
    assert len(res_single.json()["data"]) == 3

    # Multi tag AND semantics: Hot,Priority (b1, b5)
    res_multi = client.get("/businesses?tags=hot,priority")
    assert res_multi.status_code == 200
    names = [b["name"] for b in res_multi.json()["data"]]
    assert set(names) == {"Apex Dental Care", "Rajkot Mega Mart"}

    # Triple tag AND semantics: Hot,Priority,Enterprise (b5 only)
    res_triple = client.get("/businesses?tags=hot,priority,enterprise")
    assert res_triple.status_code == 200
    assert len(res_triple.json()["data"]) == 1
    assert res_triple.json()["data"][0]["name"] == "Rajkot Mega Mart"

    # Nonexistent tag returns empty
    res_none = client.get("/businesses?tags=nonexistent-tag-12345")
    assert res_none.status_code == 200
    assert res_none.json()["data"] == []


def test_individual_filter_is_favorite(client: TestClient, populated_filter_db: dict):
    res_fav = client.get("/businesses?is_favorite=true")
    assert res_fav.status_code == 200
    assert len(res_fav.json()["data"]) == 3  # b1, b3, b5

    res_unfav = client.get("/businesses?is_favorite=false")
    assert res_unfav.status_code == 200
    assert len(res_unfav.json()["data"]) == 3  # b2, b4, b6


def test_individual_filter_lead_status_all_six(client: TestClient, populated_filter_db: dict):
    statuses = ["new", "contacted", "interested", "follow_up", "converted", "lost"]
    for st in statuses:
        res = client.get(f"/businesses?lead_status={st}")
        assert res.status_code == 200
        assert len(res.json()["data"]) == 1
        assert res.json()["data"][0]["lead_status"] == st


# ============================================================================
# 2. COMBINED FILTERS (AND SEMANTICS)
# ============================================================================

def test_combined_filters_matrix(client: TestClient, populated_filter_db: dict):
    # 1. grade + score (Grade A + min_lead_score=90 -> b1 only)
    res1 = client.get("/businesses?lead_grade=A&min_lead_score=90")
    assert res1.status_code == 200
    assert len(res1.json()["data"]) == 1
    assert res1.json()["data"][0]["name"] == "Apex Dental Care"

    # 2. status + favorite (status=contacted + is_favorite=true -> b1 only)
    res2 = client.get("/businesses?lead_status=contacted&is_favorite=true")
    assert res2.status_code == 200
    assert len(res2.json()["data"]) == 1
    assert res2.json()["data"][0]["name"] == "Apex Dental Care"

    # 3. tags + favorite (tags=priority + is_favorite=true -> b1, b3, b5)
    res3 = client.get("/businesses?tags=priority&is_favorite=true")
    assert res3.status_code == 200
    assert len(res3.json()["data"]) == 3

    # 4. city + category + status (city=Surat + category=Restaurant + status=follow_up -> b3)
    res4 = client.get("/businesses?city=Surat&category=Restaurant&lead_status=follow_up")
    assert res4.status_code == 200
    assert len(res4.json()["data"]) == 1
    assert res4.json()["data"][0]["name"] == "Surat Spice Villa"

    # 5. email + phone + website (has_website=false + has_email=true + has_phone=true -> b1)
    res5 = client.get("/businesses?has_website=false&has_email=true&has_phone=true")
    assert res5.status_code == 200
    assert len(res5.json()["data"]) == 1
    assert res5.json()["data"][0]["name"] == "Apex Dental Care"

    # 6. score + grade + status (min_score=80 + grade=A + status=converted -> b5)
    res6 = client.get("/businesses?min_lead_score=80&lead_grade=A&lead_status=converted")
    assert res6.status_code == 200
    assert len(res6.json()["data"]) == 1
    assert res6.json()["data"][0]["name"] == "Rajkot Mega Mart"

    # 7. tags + score + favorite + status (tags=hot + min_score=80 + is_favorite=true + lead_status=converted -> b5)
    res7 = client.get("/businesses?tags=hot&min_lead_score=80&is_favorite=true&lead_status=converted")
    assert res7.status_code == 200
    assert len(res7.json()["data"]) == 1
    assert res7.json()["data"][0]["name"] == "Rajkot Mega Mart"

    # 8. ALL supported filters simultaneously:
    # city=Rajkot & category=Retail & search=Mega & has_website=true & has_email=true & has_phone=true
    # & lead_grade=A & min_lead_score=80 & max_lead_score=100 & tags=hot,priority & is_favorite=true & lead_status=converted
    all_filters = (
        "/businesses?"
        "search=Mega"
        "&city=Rajkot"
        "&category=Retail"
        "&has_website=true"
        "&has_email=true"
        "&has_phone=true"
        "&lead_grade=A"
        "&min_lead_score=80"
        "&max_lead_score=100"
        "&tags=hot,priority"
        "&is_favorite=true"
        "&lead_status=converted"
    )
    res8 = client.get(all_filters)
    assert res8.status_code == 200
    assert len(res8.json()["data"]) == 1
    assert res8.json()["data"][0]["name"] == "Rajkot Mega Mart"


# ============================================================================
# 3. VALIDATION & BOUNDARY TESTS (HTTP 422, NEVER 500)
# ============================================================================

def test_validation_and_boundaries(client: TestClient, populated_filter_db: dict):
    # Valid score boundaries: 0 and 100
    assert client.get("/businesses?min_lead_score=0&max_lead_score=100").status_code == 200

    # Negative score -> 422
    assert client.get("/businesses?min_lead_score=-1").status_code == 422
    assert client.get("/businesses?max_lead_score=-1").status_code == 422

    # Score > 100 -> 422
    assert client.get("/businesses?min_lead_score=101").status_code == 422
    assert client.get("/businesses?max_lead_score=105").status_code == 422

    # min_lead_score > max_lead_score -> 422
    res_cross = client.get("/businesses?min_lead_score=80&max_lead_score=50")
    assert res_cross.status_code == 422

    # Invalid lead grade -> 422
    for bad_grade in ["E", "Z", "123", "gradeA", "invalid"]:
        assert client.get(f"/businesses?lead_grade={bad_grade}").status_code == 422

    # Invalid lead status -> 422
    for bad_status in ["done", "maybe", "closed", "random", "123"]:
        assert client.get(f"/businesses?lead_status={bad_status}").status_code == 422

    # Invalid booleans -> 422
    assert client.get("/businesses?has_website=not_a_bool").status_code == 422
    assert client.get("/businesses?has_email=maybe").status_code == 422
    assert client.get("/businesses?has_phone=2").status_code == 422
    assert client.get("/businesses?is_favorite=unknown").status_code == 422


# ============================================================================
# 4. PAGINATION & SORTING WITH ACTIVE FILTERS
# ============================================================================

def test_pagination_and_sorting_with_advanced_filters(client: TestClient, populated_filter_db: dict):
    # Filter matching 3 businesses (has_website=true -> b2, b3, b5) with page_size=2
    res_p1 = client.get("/businesses?has_website=true&page=1&pageSize=2")
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert len(data_p1["data"]) == 2
    assert data_p1["pagination"]["totalItems"] == 3
    assert data_p1["pagination"]["totalPages"] == 2
    assert data_p1["pagination"]["page"] == 1

    res_p2 = client.get("/businesses?has_website=true&page=2&pageSize=2")
    assert res_p2.status_code == 200
    data_p2 = res_p2.json()
    assert len(data_p2["data"]) == 1
    assert data_p2["pagination"]["page"] == 2

    # Sorting with filters: sort by lead_score desc on has_website=true
    res_sorted = client.get("/businesses?has_website=true&sortBy=lead_score&sortOrder=desc")
    assert res_sorted.status_code == 200
    scores = [b["lead_score"] for b in res_sorted.json()["data"]]
    assert scores == [85, 75, 55]  # b5, b2, b3


# ============================================================================
# 5. EXPORT & PREVIEW PARITY
# ============================================================================

def test_export_and_preview_parity_with_advanced_filters(client: TestClient, populated_filter_db: dict):
    filter_params = {
        "city": "Ahmedabad",
        "category": "Dental",
        "has_email": True,
        "lead_grade": "A",
        "min_lead_score": 90,
        "is_favorite": True,
        "lead_status": "contacted",
    }

    # 1. Preview API
    preview_res = client.post(
        "/businesses/export/preview",
        json={
            "scope": "filtered",
            "filters": filter_params,
            "qualification": {"has_email": False, "has_phone": False},
        },
    )
    assert preview_res.status_code == 200
    assert preview_res.json()["export_count"] == 1

    # 2. GET /businesses list query
    query_str = "&".join(f"{k}={v}" for k, v in filter_params.items())
    list_res = client.get(f"/businesses?{query_str}")
    assert list_res.status_code == 200
    assert len(list_res.json()["data"]) == 1
    assert list_res.json()["data"][0]["name"] == "Apex Dental Care"

    # 3. GET /businesses/export/csv
    csv_res = client.get(f"/businesses/export/csv?{query_str}")
    assert csv_res.status_code == 200
    lines = [line for line in csv_res.text.splitlines() if line.strip()]
    assert len(lines) == 2  # Header + 1 business row
    assert "Apex Dental Care" in lines[1]
    assert "Contacted" in lines[1]
    assert "Top tier lead in Ahmedabad." not in csv_res.text  # Note content privacy


# ============================================================================
# 6. SECURITY & SQL INJECTION RESISTANCE
# ============================================================================

def test_sql_injection_resistance_in_all_filter_fields(client: TestClient, populated_filter_db: dict):
    payloads = [
        "' OR '1'='1",
        "'; DROP TABLE businesses; --",
        "1 UNION SELECT null, null, null--",
        "admin'--",
    ]

    for payload in payloads:
        # Search injection attempt
        res_search = client.get(f"/businesses?search={payload}")
        assert res_search.status_code == 200
        assert isinstance(res_search.json()["data"], list)

        # City injection attempt
        res_city = client.get(f"/businesses?city={payload}")
        assert res_city.status_code == 200
        assert res_city.json()["data"] == []

        # Category injection attempt
        res_cat = client.get(f"/businesses?category={payload}")
        assert res_cat.status_code == 200
        assert res_cat.json()["data"] == []

        # Tags injection attempt
        res_tags = client.get(f"/businesses?tags={payload}")
        assert res_tags.status_code == 200
        assert res_tags.json()["data"] == []


# ============================================================================
# 7. INVARIANCE VERIFICATION
# ============================================================================

def test_filtering_is_strictly_read_only_and_preserves_all_data(client: TestClient, populated_filter_db: dict, db: Session):
    b1 = populated_filter_db["b1"]
    db.refresh(b1)

    init_score = b1.lead_score
    init_grade = b1.lead_grade
    init_fav = b1.is_favorite
    init_status = b1.lead_status
    init_tag_count = len(b1.tags)
    init_note_count = len(b1.notes)

    # Perform complex queries repeatedly
    for _ in range(5):
        client.get("/businesses?city=Ahmedabad&lead_grade=A&is_favorite=true&lead_status=contacted&tags=hot,priority")

    db.refresh(b1)
    assert b1.lead_score == init_score
    assert b1.lead_grade == init_grade
    assert b1.is_favorite == init_fav
    assert b1.lead_status == init_status
    assert len(b1.tags) == init_tag_count
    assert len(b1.notes) == init_note_count
