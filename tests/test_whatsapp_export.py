"""
Tests for City Mobile Number / WhatsApp XLSX Export.

Validates:
- Exact 3-column structure: ['Business Type', 'Business Name', 'Mobile Number']
- Only businesses from the requested city
- Businesses with valid phone/mobile numbers only
- Deduplication of mobile numbers (normalization while preserving original formatting)
- Exclusion of already-successfully-contacted WhatsApp leads (while retaining failed/pending)
- Large exports beyond 500 records (no application-level 500 limit)
- Correct city-based filename in HTTP response header
- Standard OpenXML zip structure validation
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database.models import Business, BusinessActivity
from services.city_automation_service import (
    get_city_mobile_numbers_export_data,
    export_city_mobile_numbers_xlsx,
)
from services.xlsx_exporter import build_xlsx_bytes


def parse_xlsx_sheet_rows(xlsx_bytes: bytes) -> list[list[str]]:
    """Helper to extract rows and cells from generated XLSX bytes."""
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml")
        root = ET.fromstring(sheet_xml)
        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        
        rows = []
        for row_elem in root.findall(".//main:row", ns):
            row_cells = []
            for cell_elem in row_elem.findall("main:c", ns):
                # Check for inlineStr or plain text
                t_elem = cell_elem.find(".//main:t", ns)
                val = t_elem.text if t_elem is not None and t_elem.text is not None else ""
                row_cells.append(val)
            rows.append(row_cells)
        return rows


class TestWhatsAppMobileExport:
    def test_unauthenticated_export_rejected(self, unauth_client: TestClient):
        res = unauth_client.get("/automations/export-mobile-numbers?city=Ahmedabad")
        assert res.status_code == 401

    def test_missing_city_parameter_fails_validation(self, client: TestClient):
        res = client.get("/automations/export-mobile-numbers")
        assert res.status_code == 422

    def test_exact_three_column_structure_and_headers(self, db: Session, client: TestClient):
        # Create test businesses in Ahmedabad
        b1 = Business(
            name="Apex Clinic",
            category="Healthcare & Clinic",
            phone="+91 98765 43210",
            email="apex@clinic.example",
            city="Ahmedabad",
            lead_grade="A",
            lead_score=90,
            address="123 Ring Road, Ahmedabad",
        )
        b2 = Business(
            name="Surat Textiles",
            category="Manufacturing",
            phone="+91 98765 11111",
            city="Surat",
            lead_grade="B",
        )
        db.add_all([b1, b2])
        db.commit()

        res = client.get("/automations/export-mobile-numbers?city=Ahmedabad")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert 'filename="Ahmedabad.xlsx"' in res.headers["content-disposition"]

        rows = parse_xlsx_sheet_rows(res.content)
        assert len(rows) == 2  # Header + 1 business
        assert rows[0] == ["Business Type", "Business Name", "Mobile Number"]
        assert rows[1] == ["Healthcare & Clinic", "Apex Clinic", "+91 98765 43210"]

    def test_city_filtering_and_phone_eligibility(self, db: Session, client: TestClient):
        # 1. Ahmedabad with valid phone
        b1 = Business(name="Valid Phone Biz", category="Retail", phone="+91 91234 56780", city="Ahmedabad")
        # 2. Ahmedabad with missing phone (None)
        b2 = Business(name="No Phone Biz", category="Retail", phone=None, city="Ahmedabad")
        # 3. Ahmedabad with empty phone string
        b3 = Business(name="Empty Phone Biz", category="Retail", phone="   ", city="Ahmedabad")
        # 4. Ahmedabad with invalid phone (no digits)
        b4 = Business(name="Invalid Phone Biz", category="Retail", phone="N/A", city="Ahmedabad")
        # 5. Different city with valid phone
        b5 = Business(name="Mumbai Biz", category="Retail", phone="+91 99999 88888", city="Mumbai")

        db.add_all([b1, b2, b3, b4, b5])
        db.commit()

        res = client.get("/automations/export-mobile-numbers?city=Ahmedabad")
        assert res.status_code == 200

        rows = parse_xlsx_sheet_rows(res.content)
        assert len(rows) == 2  # Header + 1 valid business
        assert rows[0] == ["Business Type", "Business Name", "Mobile Number"]
        assert rows[1] == ["Retail", "Valid Phone Biz", "+91 91234 56780"]

    def test_preserves_original_phone_formatting_and_country_codes(self, db: Session, client: TestClient):
        b1 = Business(name="Indian Biz", category="Services", phone="+91 98765 43210", city="Bengaluru")
        b2 = Business(name="US Biz", category="Tech", phone="+1 (555) 234-5678", city="Bengaluru")
        b3 = Business(name="UK Biz", category="Consulting", phone="+44 20 7946 0958", city="Bengaluru")

        db.add_all([b1, b2, b3])
        db.commit()

        res = client.get("/automations/export-mobile-numbers?city=Bengaluru")
        assert res.status_code == 200

        rows = parse_xlsx_sheet_rows(res.content)
        assert len(rows) == 4
        assert rows[1][2] == "+91 98765 43210"
        assert rows[2][2] == "+1 (555) 234-5678"
        assert rows[3][2] == "+44 20 7946 0958"

    def test_deduplicates_identical_normalized_mobile_numbers(self, db: Session, client: TestClient):
        # Two businesses with the same phone formatted slightly differently
        b1 = Business(name="Branch 1", category="Auto", phone="+91 98765 00000", city="Pune")
        b2 = Business(name="Branch 2", category="Auto Repairs", phone="+91-98765-00000", city="Pune")
        b3 = Business(name="Branch 3", category="Auto Parts", phone="9876500000", city="Pune")
        b4 = Business(name="Unique Biz", category="Bakery", phone="+91 98765 11111", city="Pune")

        db.add_all([b1, b2, b3, b4])
        db.commit()

        res = client.get("/automations/export-mobile-numbers?city=Pune")
        assert res.status_code == 200

        rows = parse_xlsx_sheet_rows(res.content)
        # Should contain Header + Branch 1 + Unique Biz (total 3 rows)
        assert len(rows) == 3
        assert rows[1][1] == "Branch 1"
        assert rows[1][2] == "+91 98765 00000"
        assert rows[2][1] == "Unique Biz"
        assert rows[2][2] == "+91 98765 11111"

    def test_excludes_already_successfully_contacted_whatsapp_leads(self, db: Session, client: TestClient):
        b1 = Business(name="Already Contacted 1", category="Salon", phone="+91 98000 00001", city="Jaipur")
        b2 = Business(name="Already Contacted 2", category="Spa", phone="+91 98000 00002", city="Jaipur")
        b3 = Business(name="Failed Attempt Biz", category="Cafe", phone="+91 98000 00003", city="Jaipur")
        b4 = Business(name="Fresh Eligible Biz", category="Restaurant", phone="+91 98000 00004", city="Jaipur")

        db.add_all([b1, b2, b3, b4])
        db.commit()

        # Add successful WhatsApp contact activity to b1 and b2
        act1 = BusinessActivity(
            business_id=b1.id,
            activity_type="whatsapp_contacted",
            title="WhatsApp Outreach Sent",
            description="Message delivered to owner",
        )
        act2 = BusinessActivity(
            business_id=b2.id,
            activity_type="whatsapp_sent",
            title="WhatsApp Message Sent",
        )
        # Add failed attempt to b3 (should NOT be excluded)
        act3 = BusinessActivity(
            business_id=b3.id,
            activity_type="whatsapp_failed",
            title="WhatsApp Message Failed",
            description="Number not registered",
        )
        db.add_all([act1, act2, act3])
        db.commit()

        res = client.get("/automations/export-mobile-numbers?city=Jaipur")
        assert res.status_code == 200

        rows = parse_xlsx_sheet_rows(res.content)
        # Should contain Header + b3 (failed attempt still eligible) + b4 (fresh)
        assert len(rows) == 3
        exported_names = [r[1] for r in rows[1:]]
        assert "Already Contacted 1" not in exported_names
        assert "Already Contacted 2" not in exported_names
        assert "Failed Attempt Biz" in exported_names
        assert "Fresh Eligible Biz" in exported_names

    def test_large_export_beyond_500_records_no_cap(self, db: Session, client: TestClient):
        # Create 600 unique businesses with valid phones in Vadodara
        businesses = [
            Business(
                name=f"Vadodara Merchant {i}",
                category="General Trade",
                phone=f"+91 90000 {i:05d}",
                city="Vadodara",
            )
            for i in range(1, 601)
        ]
        db.add_all(businesses)
        db.commit()

        res = client.get("/automations/export-mobile-numbers?city=Vadodara")
        assert res.status_code == 200

        rows = parse_xlsx_sheet_rows(res.content)
        # Header + 600 records = 601 rows
        assert len(rows) == 601
        assert rows[0] == ["Business Type", "Business Name", "Mobile Number"]
        assert rows[1] == ["General Trade", "Vadodara Merchant 1", "+91 90000 00001"]
        assert rows[600] == ["General Trade", "Vadodara Merchant 600", "+91 90000 00600"]

    def test_default_business_type_fallback_when_category_null(self, db: Session, client: TestClient):
        b1 = Business(name="Uncategorized Biz", category=None, phone="+91 99999 12345", city="Delhi")
        db.add(b1)
        db.commit()

        res = client.get("/automations/export-mobile-numbers?city=Delhi")
        assert res.status_code == 200

        rows = parse_xlsx_sheet_rows(res.content)
        assert len(rows) == 2
        assert rows[1][0] == "General Business"
        assert rows[1][1] == "Uncategorized Biz"
        assert rows[1][2] == "+91 99999 12345"
