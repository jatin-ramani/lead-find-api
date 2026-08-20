import pytest
from services.lead_scoring import calculate_lead_score, score_to_grade, LeadScoreResult


def test_score_to_grade_boundaries():
    assert score_to_grade(100) == "A"
    assert score_to_grade(80) == "A"
    assert score_to_grade(79) == "B"
    assert score_to_grade(60) == "B"
    assert score_to_grade(59) == "C"
    assert score_to_grade(40) == "C"
    assert score_to_grade(39) == "D"
    assert score_to_grade(0) == "D"


def test_scenario_a_no_website_email_phone():
    """Scenario A: No website + Email + Phone + Category + Address + Name -> Grade A (80 pts)"""
    b = {
        "name": "Apex Dental Clinic",
        "phone": "+91 9876543210",
        "email": "contact@apexdental.com",
        "website": None,
        "address": "123 Ring Road, Ahmedabad",
        "category": "Dentist",
    }
    res = calculate_lead_score(b)
    # Email(15) + Phone(15) + Multi(5) + NoSite(35) + Addr(4) + Cat(4) + Name(2) = 80 -> A
    assert res.score == 80
    assert res.grade == "A"
    assert any("No website" in r for r in res.reasons)
    assert any("Email address available" in r for r in res.reasons)
    assert any("Phone number available" in r for r in res.reasons)
    assert any("Multi-channel outreach" in r for r in res.reasons)


def test_scenario_b_no_website_email_only():
    """Scenario B: No website + Email only -> Grade B (60 pts)"""
    b = {
        "name": "Elite Consulting",
        "phone": None,
        "email": "hello@eliteconsulting.com",
        "website": None,
        "address": "45 Corporate Way",
        "category": "Consulting",
    }
    res = calculate_lead_score(b)
    # Email(15) + NoSite(35) + Addr(4) + Cat(4) + Name(2) = 60 -> B
    assert res.score == 60
    assert res.grade == "B"


def test_scenario_c_no_website_phone_only():
    """Scenario C: No website + Phone only -> Grade B (60 pts)"""
    b = {
        "name": "Local Auto Repair",
        "phone": "+91 9998887776",
        "email": None,
        "website": None,
        "address": "77 Garage Lane",
        "category": "Auto Repair",
    }
    res = calculate_lead_score(b)
    # Phone(15) + NoSite(35) + Addr(4) + Cat(4) + Name(2) = 60 -> B
    assert res.score == 60
    assert res.grade == "B"


def test_scenario_d_website_email_phone():
    """Scenario D: Website + Email + Phone -> Grade C (45 pts)"""
    b = {
        "name": "Standard Bakery",
        "phone": "+91 9876543210",
        "email": "bakery@example.com",
        "website": "https://standardbakery.com",
        "address": "12 Bakery St",
        "category": "Bakery",
    }
    res = calculate_lead_score(b)
    # Email(15) + Phone(15) + Multi(5) + Addr(4) + Cat(4) + Name(2) = 45 -> C
    assert res.score == 45
    assert res.grade == "C"


def test_scenario_e_excellent_website_scraped():
    """Scenario E: Excellent website with scrape, email, phone, social profiles -> Grade B (65 pts)"""
    b = {
        "name": "Zenith Tech Solutions",
        "phone": "+91 9876543210",
        "email": "hello@zenith.com",
        "website": "https://zenithtech.example",
        "address": "Tech Park, Bangalore",
        "category": "Software Company",
    }
    wd = {
        "title": "Zenith Tech Official",
        "meta_description": "Premier enterprise software and cloud consulting.",
        "emails": ["sales@zenith.com", "info@zenith.com"],
        "facebook": "https://facebook.com/zenith",
        "linkedin": "https://linkedin.com/company/zenith",
        "status": "Completed",
    }
    res = calculate_lead_score(b, wd)
    # Email(15) + Phone(15) + Multi(5) + ScrapedEmail(10) + Socials(10) + Addr(4) + Cat(4) + Name(2) = 65 -> B
    assert res.score == 65
    assert res.grade == "B"


def test_scenario_f_failed_scrape_email_phone():
    """Scenario F: Failed scrape with email and phone -> Grade C (45 pts) (No false penalty/award for scrape failure)"""
    b = {
        "name": "Cloudflare Protected Inc",
        "phone": "+91 9876543210",
        "email": "info@cloudflareprotected.com",
        "website": "https://cloudflareprotected.com",
        "address": "Tower A",
        "category": "IT",
    }
    wd = {
        "status": "Failed",
    }
    res = calculate_lead_score(b, wd)
    # Email(15) + Phone(15) + Multi(5) + Addr(4) + Cat(4) + Name(2) = 45 -> C
    assert res.score == 45
    assert res.grade == "C"


def test_scenario_g_successful_thin_scrape_email_phone():
    """Scenario G: Successful scrape that revealed thin web presence -> Grade C (55 pts)"""
    b = {
        "name": "Quiet Salon",
        "phone": "+91 9876543210",
        "email": "salon@example.com",
        "website": "https://quietsalon.com",
        "address": "Market St",
        "category": "Salon",
    }
    wd = {
        "status": "Completed",
        "meta_description": "",
        "facebook": None,
        "instagram": None,
    }
    res = calculate_lead_score(b, wd)
    # Email(15) + Phone(15) + Multi(5) + ThinPresence(10) + Addr(4) + Cat(4) + Name(2) = 55 -> C
    assert res.score == 55
    assert res.grade == "C"


def test_scenario_h_website_no_email_no_phone():
    """Scenario H: Website exists, but zero contact information -> Grade D (10 pts)"""
    b = {
        "name": "Ghost Enterprise",
        "phone": None,
        "email": None,
        "website": "https://ghostenterprise.com",
        "address": "Unknown Road",
        "category": "Corporate",
    }
    res = calculate_lead_score(b)
    # Addr(4) + Cat(4) + Name(2) = 10 -> D
    assert res.score == 10
    assert res.grade == "D"


def test_scenario_i_no_website_no_contact_info():
    """Scenario I: No website + No contact information -> Grade C (45 pts)"""
    b = {
        "name": "Nameless Corner Shop",
        "phone": None,
        "email": None,
        "website": None,
        "address": "Old Bazaar",
        "category": "Retail",
    }
    res = calculate_lead_score(b)
    # NoSite(35) + Addr(4) + Cat(4) + Name(2) = 45 -> C
    assert res.score == 45
    assert res.grade == "C"


def test_scenario_j_complete_business_no_website():
    """Scenario J: Complete business with phone, email, name, category, address, no website -> Grade A (80 pts)"""
    b = {
        "name": "Radiant Health Dental",
        "phone": "+91 9876543210",
        "email": "care@radianthealth.com",
        "website": None,
        "address": "404 Healthcare Boulevard",
        "category": "Healthcare",
    }
    res = calculate_lead_score(b)
    assert res.score == 80
    assert res.grade == "A"
    assert 0 <= res.score <= 100


def test_score_never_exceeds_100_or_below_0():
    for score in [-50, -1, 0, 39, 40, 59, 60, 79, 80, 100, 150]:
        grade = score_to_grade(score)
        assert grade in ("A", "B", "C", "D")


def test_safe_contact_handling():
    """Test contact data with null, empty, whitespace, malformed, or dirty values."""
    b = {
        "name": "   ",
        "phone": "  -  ",
        "email": "null",
        "website": "   ",
        "address": " ",
        "category": "",
    }
    res = calculate_lead_score(b)
    # All fields treated as absent. NoSite gives 35 -> Grade D (35 pts)
    assert res.score == 35
    assert res.grade == "D"
