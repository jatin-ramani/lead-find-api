import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import app
from database.crud import save_business, save_website_data
from database.db import get_db
from database.models import Business


def test_api_lead_score_filter_and_sort(client: TestClient, db: Session):
    # Seed 3 businesses with different profiles
    b1 = Business(
        name="Top Tier Leads",
        phone="+91 9999999991",
        email="lead1@example.com",
        website=None,
        city="Ahmedabad",
        category="Dental",
        address="101 St",
        lead_score=85,
        lead_grade="A",
    )
    b2 = Business(
        name="Mid Tier Leads",
        phone="+91 9999999992",
        email="lead2@example.com",
        website="https://lead2.com",
        city="Ahmedabad",
        category="Dental",
        address="102 St",
        lead_score=65,
        lead_grade="B",
    )
    b3 = Business(
        name="Low Tier Leads",
        phone=None,
        email=None,
        website="https://lead3.com",
        city="Surat",
        category="Cafe",
        address=None,
        lead_score=25,
        lead_grade="D",
    )
    db.add_all([b1, b2, b3])
    db.commit()

    # Filter by lead_grade=A
    res_a = client.get("/businesses?lead_grade=A")
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert any(b["name"] == "Top Tier Leads" for b in data_a["data"])
    assert not any(b["name"] == "Mid Tier Leads" for b in data_a["data"])

    # Filter by score range
    res_range = client.get("/businesses?min_lead_score=60&max_lead_score=90")
    assert res_range.status_code == 200
    data_range = res_range.json()
    names = [b["name"] for b in data_range["data"]]
    assert "Top Tier Leads" in names
    assert "Mid Tier Leads" in names
    assert "Low Tier Leads" not in names

    # Sort by lead_score desc
    res_sort = client.get("/businesses?sortBy=lead_score&sortOrder=desc")
    assert res_sort.status_code == 200
    scores = [b["lead_score"] for b in res_sort.json()["data"]]
    assert scores == sorted(scores, reverse=True)

    # Detail view with reasons
    res_detail = client.get(f"/businesses/{b1.id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["lead_score"] == 85
    assert detail["lead_grade"] == "A"
    assert "lead_score_reasons" in detail
    assert isinstance(detail["lead_score_reasons"], list)

    # Cities aggregate metrics
    res_cities = client.get("/businesses/cities")
    assert res_cities.status_code == 200
    cities = res_cities.json()["data"]
    ahmedabad = next((c for c in cities if c["city"] == "Ahmedabad"), None)
    assert ahmedabad is not None
    assert "averageLeadScore" in ahmedabad
    assert "highQualityLeads" in ahmedabad
    assert ahmedabad["highQualityLeads"] >= 1

    # CSV export contains lead score headers
    res_csv = client.get("/businesses/export/csv")
    assert res_csv.status_code == 200
    assert "Lead Score" in res_csv.text
    assert "Lead Grade" in res_csv.text
