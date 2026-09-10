"""Integration tests for City-First Grade-Based Email Automations API and Execution."""

from datetime import datetime, timezone
import pytest

from database.models import Business, BusinessActivity, EmailCampaign, EmailCampaignRecipient, EmailTemplate
from database.crud import save_business
from providers.ai_provider import MockAIProvider, get_ai_provider
from providers.email_provider import MockEmailProvider, get_email_provider


class TestCityAutomationEndpoints:
    def test_unauthenticated_requests_rejected(self, unauth_client):
        assert unauth_client.get("/automations/cities").status_code == 401
        assert unauth_client.get("/automations/city-stats?city=Ahmedabad").status_code == 401
        assert unauth_client.post("/automations/generate-templates", json={"city": "Ahmedabad"}).status_code == 401
        assert unauth_client.post("/automations/generate-single-template", json={"grade": "A", "city": "Ahmedabad"}).status_code == 401
        assert unauth_client.post("/automations/start-city-automation", json={"city": "Ahmedabad", "templates": {}}).status_code == 401
        assert unauth_client.get("/automations/runs").status_code == 401
        assert unauth_client.get("/automations/runs/1").status_code == 401
        assert unauth_client.post("/automations/runs/1/cancel").status_code == 401

    def test_get_available_cities_and_stats(self, client, db):
        # Create businesses across different cities and grades
        b1 = Business(
            name="Ahmedabad Dental Care",
            city="Ahmedabad",
            email="dental@ahmedabad.example",
            lead_grade="A",
            lead_score=92,
            phone="+91 98765 43210",
        )
        b2 = Business(
            name="Ahmedabad Auto Works",
            city="Ahmedabad",
            email="auto@ahmedabad.example",
            lead_grade="B",
            lead_score=78,
        )
        b3 = Business(
            name="Ahmedabad Retailer (No Email)",
            city="Ahmedabad",
            email=None,
            lead_grade="C",
            lead_score=50,
        )
        b4 = Business(
            name="Surat Logistics",
            city="Surat",
            email="info@suratlogistics.example",
            lead_grade="A",
            lead_score=88,
        )
        db.add_all([b1, b2, b3, b4])
        db.commit()

        # 1. Test /automations/cities
        res = client.get("/automations/cities")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        cities = {c["city"]: c for c in data["items"]}
        assert "Ahmedabad" in cities
        assert cities["Ahmedabad"]["total_leads"] == 3
        assert cities["Ahmedabad"]["eligible_leads"] == 2
        assert cities["Ahmedabad"]["ineligible_leads"] == 1
        assert "Surat" in cities
        assert cities["Surat"]["total_leads"] == 1
        assert cities["Surat"]["eligible_leads"] == 1

        # 2. Test /automations/city-stats
        res = client.get("/automations/city-stats?city=Ahmedabad")
        assert res.status_code == 200
        stats = res.json()
        assert stats["success"] is True
        assert stats["city"] == "Ahmedabad"
        assert stats["total_leads"] == 3
        assert stats["email_eligible_leads"] == 2
        assert stats["ineligible_leads"] == 1
        assert stats["grades"]["A"]["total"] == 1
        assert stats["grades"]["A"]["eligible"] == 1
        assert stats["grades"]["B"]["total"] == 1
        assert stats["grades"]["B"]["eligible"] == 1
        assert stats["grades"]["C"]["total"] == 1
        assert stats["grades"]["C"]["eligible"] == 0
        assert stats["grades"]["D"]["total"] == 0

    def test_generate_ai_templates(self, client):
        # Test full 4-grade generation
        res = client.post(
            "/automations/generate-templates",
            json={"city": "Ahmedabad", "industry": "Healthcare"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["city"] == "Ahmedabad"
        templates = data["data"]
        assert set(templates.keys()) == {"A", "B", "C", "D"}
        for grade in ["A", "B", "C", "D"]:
            assert "subject" in templates[grade]
            assert "body" in templates[grade]
            assert len(templates[grade]["subject"]) > 5
            # Ensure allowlisted template variables exist in generated body
            body_lower = templates[grade]["body"].lower()
            assert "{{business_name}}" in body_lower or "{{contact_name}}" in body_lower or "{{business name}}" in body_lower or "{{contact name}}" in body_lower

        # Test single grade regeneration
        res_single = client.post(
            "/automations/generate-single-template",
            json={"grade": "A", "city": "Ahmedabad", "industry": "Dental"},
        )
        assert res_single.status_code == 200
        data_single = res_single.json()
        assert data_single["success"] is True
        assert data_single["grade"] == "A"
        assert "subject" in data_single["data"]
        assert "body" in data_single["data"]

    def test_start_city_automation_end_to_end(self, client, db):
        # Create businesses for each grade
        b_a = Business(
            name="Apex Clinic",
            city="Pune",
            email="contact@apexclinic.example",
            lead_grade="A",
            lead_score=95,
        )
        b_b = Business(
            name="Pune Bakery",
            city="Pune",
            email="order@punebakery.example",
            lead_grade="B",
            lead_score=75,
        )
        b_c = Business(
            name="Pune Repairs",
            city="Pune",
            email="service@punerepairs.example",
            lead_grade="C",
            lead_score=60,
        )
        b_d = Business(
            name="Pune General Store",
            city="Pune",
            email="general@punestore.example",
            lead_grade="D",
            lead_score=30,
        )
        # Ineligible lead (no email)
        b_no_email = Business(
            name="Pune Offline Shop",
            city="Pune",
            email="",
            lead_grade="A",
            lead_score=90,
        )
        db.add_all([b_a, b_b, b_c, b_d, b_no_email])
        db.commit()

        # Launch automation
        payload = {
            "city": "Pune",
            "name": "Pune Outreach Campaign Q3",
            "templates": {
                "A": {
                    "subject": "VIP Partnership with {{business_name}}",
                    "body": "Hello {{contact_name}},\n\nGrade A partnership for {{business_name}}.",
                },
                "B": {
                    "subject": "Growth Solutions for {{business_name}}",
                    "body": "Hello {{contact_name}},\n\nGrade B proposal for {{business_name}}.",
                },
                "C": {
                    "subject": "Quick consultation for {{business_name}}",
                    "body": "Hello {{contact_name}},\n\nGrade C outreach for {{business_name}}.",
                },
                "D": {
                    "subject": "Connecting with {{business_name}}",
                    "body": "Hello {{contact_name}},\n\nGrade D intro for {{business_name}}.",
                },
            },
        }

        from services.city_automation_service import get_city_lead_grade_stats
        print("IN TEST DB BIZ COUNT:", db.query(Business).filter(Business.city == "Pune").count())
        print("IN TEST STATS:", get_city_lead_grade_stats(db, "Pune"))

        res = client.post("/automations/start-city-automation", json=payload)
        print("STATUS:", res.status_code, res.text)
        assert res.status_code == 201
        report = res.json()["data"]
        assert report["city"] == "Pune"
        assert report["status"] == "running"
        assert report["recipient_count"] == 4  # The 4 with valid emails

        campaign_id = report["id"]

        # Check report detail endpoint (TestClient finishes background tasks synchronously)
        res_report = client.get(f"/automations/runs/{campaign_id}")
        print("GET REPORT STATUS:", res_report.status_code, res_report.text)
        assert res_report.status_code == 200
        data_rep = res_report.json()["data"]
        assert data_rep["id"] == campaign_id
        assert data_rep["status"] == "completed"
        assert data_rep["sent_count"] == 4
        assert data_rep["failed_count"] == 0
        assert data_rep["remaining_count"] == 0
        assert len(data_rep["remaining_recipients"]) == 0
        assert data_rep["grade_breakdown"]["A"]["sent"] == 1
        assert data_rep["grade_breakdown"]["B"]["sent"] == 1
        assert data_rep["grade_breakdown"]["C"]["sent"] == 1
        assert data_rep["grade_breakdown"]["D"]["sent"] == 1
        assert len(data_rep["recipient_logs"]) == 4

        # Check list runs endpoint
        res_runs = client.get("/automations/runs")
        assert res_runs.status_code == 200
        runs_data = res_runs.json()
        assert runs_data["total"] >= 1
        matching_run = next((r for r in runs_data["items"] if r["id"] == campaign_id), None)
        assert matching_run is not None
        assert matching_run["city"] == "Pune"

    def test_start_city_automation_no_eligible_leads_fails(self, client, db):
        # City with only email-less businesses
        b = Business(
            name="No Email Co",
            city="GhostCity",
            email="",
            lead_grade="A",
        )
        db.add(b)
        db.commit()

        payload = {
            "city": "GhostCity",
            "templates": {
                "A": {"subject": "Hi", "body": "Body"},
                "B": {"subject": "Hi", "body": "Body"},
                "C": {"subject": "Hi", "body": "Body"},
                "D": {"subject": "Hi", "body": "Body"},
            },
        }
        res = client.post("/automations/start-city-automation", json=payload)
        assert res.status_code == 400
        error_json = res.json()
        error_msg = error_json.get("message", "") or error_json.get("detail", "")
        assert "No email-eligible leads found" in str(error_msg)

    def test_cancel_scheduled_city_automation(self, client, db):
        b = Business(
            name="Future Lead",
            city="Delhi",
            email="future@delhi.example",
            lead_grade="A",
        )
        db.add(b)
        db.commit()

        # Start with scheduled date in future
        payload = {
            "city": "Delhi",
            "scheduled_at": "2028-01-01T12:00:00Z",
            "templates": {
                "A": {"subject": "Hi {{business_name}}", "body": "Body"},
                "B": {"subject": "Hi {{business_name}}", "body": "Body"},
                "C": {"subject": "Hi {{business_name}}", "body": "Body"},
                "D": {"subject": "Hi {{business_name}}", "body": "Body"},
            },
        }
        res = client.post("/automations/start-city-automation", json=payload)
        assert res.status_code == 201
        report = res.json()["data"]
        assert report["status"] == "scheduled"
        assert report["sent_count"] == 0
        assert report["pending_count"] == 1

        campaign_id = report["id"]

        # Cancel it
        res_cancel = client.post(f"/automations/runs/{campaign_id}/cancel")
        assert res_cancel.status_code == 200
        cancel_report = res_cancel.json()["data"]
        assert cancel_report["status"] == "cancelled"
        assert cancel_report["cancelled_count"] == 1

    def test_get_master_template_api(self, client):
        res = client.get("/automations/master-template?city=Jaipur")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["city"] == "Jaipur"
        tpl = data["data"]
        assert "Quick idea for {{Business Name}}" in tpl["subject"]
        assert "Codebait" in tpl["body"]
        assert "Jatin Ramani" in tpl["body"]

    def test_start_city_automation_with_master_template(self, client, db):
        b1 = Business(name="Jaipur Gems", city="Jaipur", email="gems@jaipur.example", lead_grade="A")
        b2 = Business(name="Jaipur Crafts", city="Jaipur", email="crafts@jaipur.example", lead_grade="C")
        db.add_all([b1, b2])
        db.commit()

        payload = {
            "city": "Jaipur",
            "name": "Jaipur Master Automation",
            "template": {
                "subject": "Quick idea for {{Business Name}}",
                "body": "<p>Hi {{Contact Name}},</p><p>We build modern websites...</p><p>Best,<br><strong>Jatin Ramani</strong></p>",
                "name": "Jaipur Master Template",
            },
        }
        res = client.post("/automations/start-city-automation", json=payload)
        assert res.status_code == 201
        data = res.json()["data"]
        assert data["city"] == "Jaipur"
        assert data["recipient_count"] == 2
        assert data["status"] == "running"

    def test_sent_lead_exclusion_rules(self, client, db):
        # 1. Setup London leads with various states
        # Lead 1: Already sent via CampaignRecipient
        b_sent = Business(name="London Sent Co", city="London", email="sent@london.example", lead_grade="A", lead_score=95)
        # Lead 2: Failed send (should remain eligible)
        b_failed = Business(name="London Failed Co", city="London", email="failed@london.example", lead_grade="B", lead_score=75)
        # Lead 3: Pending in a draft/running campaign (should remain eligible for new query)
        b_pending = Business(name="London Pending Co", city="London", email="pending@london.example", lead_grade="C", lead_score=50)
        # Lead 4: Skipped (should remain eligible)
        b_skipped = Business(name="London Skipped Co", city="London", email="skipped@london.example", lead_grade="D", lead_score=25)
        # Lead 5: Fresh lead with email (eligible)
        b_fresh = Business(name="London Fresh Co", city="London", email="fresh@london.example", lead_grade="A", lead_score=90)
        # Lead 6: Lead with missing email
        b_no_email = Business(name="London No Email Co", city="London", email=None, lead_grade="B", lead_score=60)

        db.add_all([b_sent, b_failed, b_pending, b_skipped, b_fresh, b_no_email])
        db.commit()

        # Create past campaign and recipients
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        hist_tpl = EmailTemplate(name="Hist Tpl", subject="Sub", body="Body", is_archived=False, created_at=now, updated_at=now)
        db.add(hist_tpl)
        camp = EmailCampaign(name="Historical Campaign", template=hist_tpl, status="completed", filter_criteria_json="{}", created_at=now, updated_at=now)
        camp2 = EmailCampaign(name="Historical Campaign 2", template=hist_tpl, status="completed", filter_criteria_json="{}", created_at=now, updated_at=now)
        db.add_all([camp, camp2])
        db.flush()

        r_sent1 = EmailCampaignRecipient(campaign=camp, business_id=b_sent.id, recipient_email=b_sent.email, status="sent", created_at=now, updated_at=now)
        # Duplicate successful send on same business across campaigns to test deduplication
        r_sent2 = EmailCampaignRecipient(campaign=camp2, business_id=b_sent.id, recipient_email=b_sent.email, status="sent", created_at=now, updated_at=now)
        r_failed = EmailCampaignRecipient(campaign=camp, business_id=b_failed.id, recipient_email=b_failed.email, status="failed", created_at=now, updated_at=now)
        r_pending = EmailCampaignRecipient(campaign=camp, business_id=b_pending.id, recipient_email=b_pending.email, status="pending", created_at=now, updated_at=now)
        r_skipped = EmailCampaignRecipient(campaign=camp, business_id=b_skipped.id, recipient_email=b_skipped.email, status="skipped", created_at=now, updated_at=now)

        db.add_all([r_sent1, r_sent2, r_failed, r_pending, r_skipped])
        db.commit()

        # 2. Check /automations/cities
        res_cities = client.get("/automations/cities")
        assert res_cities.status_code == 200
        cities_map = {c["city"]: c for c in res_cities.json()["items"]}
        assert "London" in cities_map
        london = cities_map["London"]
        # Total: 6, Already Sent: 1, Eligible: 4 (b_failed, b_pending, b_skipped, b_fresh), Ineligible: 1 (b_no_email)
        assert london["total_leads"] == 6
        assert london["already_sent_leads"] == 1
        assert london["eligible_leads"] == 4
        assert london["ineligible_leads"] == 1

        # 3. Check /automations/city-stats?city=London
        res_stats = client.get("/automations/city-stats?city=London")
        assert res_stats.status_code == 200
        stats = res_stats.json()
        assert stats["total_leads"] == 6
        assert stats["already_sent_leads"] == 1
        assert stats["email_eligible_leads"] == 4
        assert stats["ineligible_leads"] == 1
        assert stats["grades"]["A"]["total"] == 2
        assert stats["grades"]["A"]["eligible"] == 1  # b_fresh is eligible, b_sent is already sent
        assert stats["grades"]["A"]["already_sent"] == 1

        # 4. Start new city automation for London and verify recipient snapshot excludes b_sent
        b_sent_id = b_sent.id
        res_start = client.post(
            "/automations/start-city-automation",
            json={
                "city": "London",
                "name": "London Hardening Test Run",
            },
        )
        assert res_start.status_code == 201
        run_data = res_start.json()["data"]
        # Exactly 4 recipients are targeted (b_sent excluded)
        assert run_data["recipient_count"] == 4

        # Verify historical sent records are intact
        assert db.query(EmailCampaignRecipient).filter(EmailCampaignRecipient.business_id == b_sent_id, EmailCampaignRecipient.status == "sent").count() == 2


