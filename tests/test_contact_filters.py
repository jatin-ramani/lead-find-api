import csv
import io

import pytest

from database.crud import get_businesses, get_dashboard_stats, save_business


ROWS = [
    ("No site both", "111", "both@example.com", None, "A", "Alpha"),
    ("No site email", None, "email@example.com", "", "A", "Beta"),
    ("No site phone", "222", None, "   ", "B", "Alpha"),
    ("No site none", "  ", " ", None, "B", "Beta"),
    ("Site both", "333", "site@example.com", "https://site.test", "A", "Alpha"),
    ("Site phone", "444", None, " https://phone.test ", "B", "Beta"),
    ("Whitespace", " ", "   ", "\t", "C", "Gamma"),
]


@pytest.fixture
def populated(db):
    for index, (name, phone, email, website, city, category) in enumerate(ROWS):
        save_business(db, name, phone, email, website, city, category, None,
                      "Has Website" if website and website.strip() else "No Website",
                      f"phase7-{index}")
    return db


def names(result):
    return {row.name for row in result["data"]}


@pytest.mark.parametrize(("filters", "expected"), [
    ({}, {row[0] for row in ROWS}),
    ({"has_website": True}, {"Site both", "Site phone"}),
    ({"has_website": False}, {"No site both", "No site email", "No site phone", "No site none", "Whitespace"}),
    ({"has_email": True}, {"No site both", "No site email", "Site both"}),
    ({"has_phone": True}, {"No site both", "No site phone", "Site both", "Site phone"}),
    ({"has_website": False, "has_email": True}, {"No site both", "No site email"}),
    ({"has_website": False, "has_phone": True}, {"No site both", "No site phone"}),
    ({"has_website": False, "has_email": True, "has_phone": True}, {"No site both"}),
    ({"has_website": True, "has_email": True}, {"Site both"}),
    ({"has_website": True, "has_phone": True}, {"Site both", "Site phone"}),
    ({"has_website": True, "has_email": True, "has_phone": True}, {"Site both"}),
])
def test_boolean_filter_combinations(populated, filters, expected):
    assert names(get_businesses(populated, page_size=100, **filters)) == expected


def test_empty_database(db):
    result = get_businesses(db, has_website=False, has_email=True)
    assert result["data"] == []
    assert result["pagination"]["totalItems"] == 0


def test_pagination_and_sort_after_filtering(populated):
    first = get_businesses(populated, has_website=False, page=1, page_size=2,
                           sort_by="name", sort_order="asc")
    second = get_businesses(populated, has_website=False, page=2, page_size=2,
                            sort_by="name", sort_order="asc")
    assert first["pagination"]["totalItems"] == 5
    assert first["pagination"]["totalPages"] == 3
    assert [row.name for row in first["data"]] == ["No site both", "No site email"]
    assert [row.name for row in second["data"]] == ["No site none", "No site phone"]


def test_api_rejects_unknown_boolean(client):
    response = client.get("/businesses?has_email=perhaps")
    assert response.status_code == 422
    assert response.json()["error"] == "VALIDATION_ERROR"




@pytest.mark.parametrize(("query", "expected"), [
    ("", {row[0] for row in ROWS}),
    ("has_website=true", {"Site both", "Site phone"}),
    ("has_website=false", {"No site both", "No site email", "No site phone", "No site none", "Whitespace"}),
    ("has_email=true", {"No site both", "No site email", "Site both"}),
    ("has_phone=true", {"No site both", "No site phone", "Site both", "Site phone"}),
    ("has_website=false&has_email=true", {"No site both", "No site email"}),
    ("has_website=false&has_phone=true", {"No site both", "No site phone"}),
    ("has_website=false&has_email=true&has_phone=true", {"No site both"}),
    ("has_email=true&has_phone=true", {"No site both", "Site both"}),
    ("has_website=true&has_email=true", {"Site both"}),
    ("has_website=true&has_phone=true", {"Site both", "Site phone"}),
    ("has_website=true&has_email=true&has_phone=true", {"Site both"}),
])
def test_api_boolean_filter_combinations(client, populated, query, expected):
    response = client.get(f"/businesses?{query}")
    assert response.status_code == 200
    assert {row["name"] for row in response.json()["data"]} == expected

def test_dashboard_actionable_aggregation(populated):
    stats = get_dashboard_stats(populated)["business"]
    assert stats == {
        "totalBusinesses": 7,
        "withWebsite": 2,
        "withoutWebsite": 5,
        "withEmail": 3,
        "withoutEmail": 4,
        "withPhone": 4,
        "actionableLeads": 3,
    }


def test_filtered_export_uses_boolean_contract(client, populated):
    response = client.get("/businesses/export/csv?has_website=false&has_email=true&has_phone=true")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert [row[1] for row in rows[1:]] == ["No site both"]


def test_selected_export_and_preview(client, populated):
    ids = [row.id for row in populated.query(__import__('database.models', fromlist=['Business']).Business).all()]
    body = {"business_ids": ids, "has_email": True, "has_phone": True}
    response = client.post("/businesses/export/csv", json=body)
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert {row[1] for row in rows[1:]} == {"No site both", "Site both"}
    preview = client.post("/businesses/export/preview", json={
        "scope": "selected", "business_ids": ids,
        "qualification": {"has_email": True, "has_phone": True},
    }).json()
    assert preview == {"success": True, "total_selected": 7,
                       "matching_qualification": 2, "export_count": 2}


def test_filtered_preview_combines_base_and_qualification(client, populated):
    preview = client.post("/businesses/export/preview", json={
        "scope": "filtered", "filters": {"has_website": False},
        "qualification": {"has_email": True, "has_phone": True},
    }).json()
    assert preview == {"success": True, "total_selected": 5,
                       "matching_qualification": 1, "export_count": 1}


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_csv_formula_cells_are_prefixed(client, db, prefix):
    save_business(db, prefix + "danger", "+91 123", "safe@example.com", None,
                  "City", "Category", "Normal", "No Website", "csv-" + prefix)
    raw = client.get("/businesses/export/csv").content.decode("utf-8-sig")
    row = list(csv.reader(io.StringIO(raw)))[1]
    assert row[1] == "'" + prefix + "danger"
    assert row[2] == "'+91 123"
    assert row[3] == "safe@example.com"
    assert row[6:9] == ["Category", "Normal", "No Website"]
def test_csv_preserves_unicode_quotes_line_breaks_and_empty_cells(client, db):
    save_business(
        db,
        'ગુજરાતી "વ્યવસાય", सेवा',
        None,
        None,
        None,
        "અમદાવાદ",
        "सेवा",
        "Line one\nLine two",
        "No Website",
        "csv-unicode",
    )
    response = client.get("/businesses/export/csv")
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert 'filename="businesses.csv"' in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    row = rows[1]
    assert row[1] == 'ગુજરાતી "વ્યવસાય", सेवा'
    assert row[2:5] == ["", "", ""]
    assert row[5:8] == ["અમદાવાદ", "सेवा", "Line one\nLine two"]
