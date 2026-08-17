"""HTTP layer: status codes, payload shapes, validation and error handling."""

import json

import pytest

from database import crud
from database.models import WebsiteData


class TestSystemEndpoints:
    def test_root(self, client):
        assert client.get("/").status_code == 200

    def test_health_reports_connected(self, client):
        body = client.get("/health").json()

        assert body["status"] == "healthy"
        assert body["database"] == "connected"
        assert body["timestamp"].endswith("+00:00")

    def test_version_matches_settings(self, client):
        from config import settings

        body = client.get("/version").json()

        assert body == {"name": settings.APP_NAME, "version": settings.APP_VERSION}

    def test_system_reports_dialect_not_the_url(self, client):
        """A connection string carries credentials and must never be echoed."""

        from config import settings

        body = client.get("/system").json()

        assert body["database"] == "sqlite"
        assert settings.database_url not in json.dumps(body)
        assert "sqlite:///" not in json.dumps(body)

    def test_system_does_not_leak_the_api_key(self, client):
        from config import settings

        assert settings.geoapify_api_key not in client.get("/system").text

    def test_openapi_schema_builds(self, client):
        schema = client.get("/openapi.json").json()

        # 19 paths, 23 operations — several paths carry more than one method.
        operations = [
            (method, path)
            for path, methods in schema["paths"].items()
            for method in methods
        ]

        assert schema["openapi"].startswith("3.")
        assert len(schema["paths"]) == 19
        assert len(operations) == 23

    def test_every_operation_is_documented(self, client):
        """Swagger is the contract; an undocumented endpoint is a regression."""

        schema = client.get("/openapi.json").json()

        undocumented = [
            f"{method.upper()} {path}"
            for path, methods in schema["paths"].items()
            for method, operation in methods.items()
            if not operation.get("summary") or not operation.get("description")
        ]

        assert undocumented == []


class TestListBusinesses:
    def test_empty_database(self, client):
        body = client.get("/businesses").json()

        assert body["data"] == []
        assert body["pagination"]["totalItems"] == 0

    def test_envelope_and_row_shape(self, client, sample_businesses):
        body = client.get("/businesses?pageSize=1").json()

        assert set(body) == {"success", "data", "pagination"}
        assert set(body["data"][0]) == {
            "id", "name", "phone", "email", "website",
            "city", "category", "address", "status",
        }

    def test_pagination_query_params(self, client, sample_businesses):
        body = client.get("/businesses?page=2&pageSize=3").json()

        assert body["pagination"]["page"] == 2
        assert body["pagination"]["pageSize"] == 3
        assert len(body["data"]) == 3

    @pytest.mark.parametrize(
        "query", ["page=0", "page=-1", "pageSize=0", "pageSize=101"]
    )
    def test_out_of_range_paging_is_rejected(self, client, query):
        assert client.get(f"/businesses?{query}").status_code == 422

    @pytest.mark.parametrize("query", ["page=1", "pageSize=1", "pageSize=100"])
    def test_boundary_paging_is_accepted(self, client, query):
        assert client.get(f"/businesses?{query}").status_code == 200

    def test_filters_and_search(self, client, sample_businesses):
        assert client.get("/businesses?city=Surat").json()["data"]
        assert client.get("/businesses?search=Business 01").json()[
            "pagination"]["totalItems"] == 1
        assert client.get("/businesses?city=Atlantis").json()["data"] == []

    def test_sorting(self, client, sample_businesses):
        names = [b["name"] for b in client.get(
            "/businesses?sortBy=name&sortOrder=asc&pageSize=100").json()["data"]]

        assert names == sorted(names)

    def test_invalid_sort_falls_back_rather_than_erroring(self, client, sample_businesses):
        response = client.get("/businesses?sortBy=bogus&sortOrder=bogus")

        assert response.status_code == 200
        ids = [b["id"] for b in response.json()["data"]]
        assert ids == sorted(ids, reverse=True)


class TestGetBusiness:
    def test_found(self, client, business_factory):
        business = business_factory(name="Target")

        body = client.get(f"/businesses/{business.id}").json()

        assert body["name"] == "Target"

    def test_missing_returns_404(self, client):
        response = client.get("/businesses/9999")

        assert response.status_code == 404
        assert response.json()["message"] == "Business not found"

    def test_non_integer_id_returns_422(self, client):
        assert client.get("/businesses/abc").status_code == 422


class TestDeleteBusiness:
    def test_single_delete(self, client, business_factory):
        business = business_factory()

        response = client.delete(f"/businesses/{business.id}")

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert client.get(f"/businesses/{business.id}").status_code == 404

    def test_single_delete_missing_returns_404(self, client):
        assert client.delete("/businesses/9999").status_code == 404

    def test_bulk_delete(self, client, sample_businesses):
        response = client.request(
            "DELETE", "/businesses", json={"business_ids": [1, 1, 2, 9999]}
        )

        assert response.status_code == 200
        assert response.json() == {"success": True, "deleted": 2}

    def test_bulk_delete_is_idempotent_and_never_404s(self, client, sample_businesses):
        client.request("DELETE", "/businesses", json={"business_ids": [1]})
        again = client.request("DELETE", "/businesses", json={"business_ids": [1]})

        assert again.status_code == 200
        assert again.json()["deleted"] == 0

    @pytest.mark.parametrize(
        "payload", [{}, {"ids": [1]}, {"business_ids": ["abc"]}, {"business_ids": "1"}]
    )
    def test_malformed_bulk_delete_body_returns_422(self, client, payload):
        assert client.request(
            "DELETE", "/businesses", json=payload
        ).status_code == 422


class TestCsvExport:
    def _rows(self, text):
        import csv
        import io

        return list(csv.reader(io.StringIO(text.lstrip("﻿"))))

    def test_headers_and_bom(self, client, sample_businesses):
        response = client.get("/businesses/export/csv")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="businesses.csv"' in response.headers["content-disposition"]
        assert response.text.startswith("﻿"), "missing UTF-8 BOM for Excel"

    def test_column_order(self, client, sample_businesses):
        rows = self._rows(client.get("/businesses/export/csv").text)

        assert rows[0] == [
            "ID", "Name", "Phone", "Email", "Website",
            "City", "Category", "Address", "Status",
        ]

    def test_exports_every_matching_row_ignoring_pagination(
        self, client, sample_businesses
    ):
        rows = self._rows(client.get("/businesses/export/csv").text)

        assert len(rows) - 1 == 10

    def test_filters_apply(self, client, sample_businesses):
        rows = self._rows(client.get("/businesses/export/csv?city=Surat").text)

        assert {r[5] for r in rows[1:]} == {"Surat"}

    def test_selected_export_matches_get_format(self, client, sample_businesses):
        get_rows = self._rows(client.get("/businesses/export/csv").text)
        post = client.post("/businesses/export/csv", json={"business_ids": [1, 1, 9999]})
        post_rows = self._rows(post.text)

        assert post.headers["content-type"].startswith("text/csv")
        assert post_rows[0] == get_rows[0]
        assert [r[0] for r in post_rows[1:]] == ["1"]

    def test_selected_export_with_no_valid_ids_returns_header_only(
        self, client, sample_businesses
    ):
        rows = self._rows(
            client.post("/businesses/export/csv", json={"business_ids": [9999]}).text
        )

        assert len(rows) == 1

    def test_quoting_survives_commas_quotes_and_newlines(self, client, business_factory):
        business_factory(
            name='Café "Quoted", Inc', address="10 Ring Rd\nLine Two",
            phone=None, place_id="tricky",
        )

        rows = self._rows(client.get("/businesses/export/csv").text)

        assert rows[1][1] == 'Café "Quoted", Inc'
        assert "\n" in rows[1][7]
        assert rows[1][2] == "", "None should render as an empty cell"


class TestWebsiteDataEndpoint:
    def test_404_before_any_scrape(self, client, business_factory):
        business = business_factory()

        response = client.get(f"/businesses/{business.id}/website")

        assert response.status_code == 404

    def test_returns_decoded_emails(self, client, db, business_factory):
        business = business_factory()
        crud.save_website_data(
            db, business_id=business.id, title="T", emails=["a@x.test", "b@x.test"]
        )

        body = client.get(f"/businesses/{business.id}/website").json()

        assert body["success"] is True
        assert body["data"]["emails"] == ["a@x.test", "b@x.test"]

    @pytest.mark.parametrize(
        "stored,expected",
        [
            ('["a@x.test"]', ["a@x.test"]),
            ("[]", []),
            ("not json at all", []),
            ("null", []),
            ('{"a":1}', []),
            ("", []),
        ],
    )
    def test_malformed_stored_emails_degrade_to_empty_list(
        self, client, db, business_factory, stored, expected
    ):
        """One bad row must not 500 the endpoint."""

        business = business_factory()
        db.add(WebsiteData(business_id=business.id, emails=stored, status="Completed"))
        db.commit()

        response = client.get(f"/businesses/{business.id}/website")

        assert response.status_code == 200
        assert response.json()["data"]["emails"] == expected


class TestJobEndpoints:
    def test_scan_jobs_empty(self, client):
        assert client.get("/scan/jobs").json() == []

    def test_scan_jobs_latest_404_when_none(self, client):
        assert client.get("/scan/jobs/latest").status_code == 404

    def test_scan_jobs_latest(self, client, db):
        crud.create_scan_job(db, city="Ahmedabad", category="commercial")

        body = client.get("/scan/jobs/latest").json()

        assert body["city"] == "Ahmedabad"

    def test_scrape_jobs_envelope(self, client, db):
        crud.create_scrape_job(db, total_websites=5)

        body = client.get("/scrape/jobs").json()

        assert body["success"] is True
        assert body["data"][0]["total_websites"] == 5

    def test_scrape_job_by_id(self, client, db):
        job_id = crud.create_scrape_job(db, total_websites=3)

        assert client.get(f"/scrape/jobs/{job_id}").json()["data"]["id"] == job_id

    def test_scrape_job_missing_and_malformed(self, client):
        assert client.get("/scrape/jobs/9999").status_code == 404
        assert client.get("/scrape/jobs/abc").status_code == 422

    def test_delete_scrape_job(self, client, db):
        job_id = crud.create_scrape_job(db, total_websites=1)

        assert client.delete(f"/scrape/jobs/{job_id}").status_code == 200
        assert client.delete(f"/scrape/jobs/{job_id}").status_code == 404


class TestDashboardEndpoint:
    def test_grouped_shape(self, client, sample_businesses):
        body = client.get("/dashboard/stats").json()

        assert set(body) == {
            "business", "websiteData", "scrapeJobs", "scanJobs",
            "latestScanJob", "latestScrapeJob",
        }
        assert set(body["business"]) == {
            "totalBusinesses", "withWebsite", "withoutWebsite",
            "withEmail", "withoutEmail",
        }

    def test_counts_are_internally_consistent(self, client, sample_businesses):
        body = client.get("/dashboard/stats").json()
        business, web = body["business"], body["websiteData"]

        assert business["withWebsite"] + business["withoutWebsite"] == business[
            "totalBusinesses"]
        assert web["completed"] + web["failed"] + web["pending"] == business["withWebsite"]

    def test_empty_database_returns_zeros(self, client):
        body = client.get("/dashboard/stats").json()

        assert body["business"]["totalBusinesses"] == 0
        assert body["latestScanJob"] is None
