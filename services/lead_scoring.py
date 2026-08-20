"""
Lead Scoring Service for Lead Finder.

Calculates a deterministic 0-100 lead score and letter grade (A-D) with
transparent, explainable reasons for every discovered business.

Prioritization Philosophy:
- Lead scoring measures "How actionable and valuable is this business as a potential lead?"
- Businesses with NO WEBSITE but reachable via Phone and/or Email are prime outreach targets (Grade A/B).
- Businesses with a website but zero contact points are low actionability (Grade D).
- Technical scrape failures (Cloudflare, timeouts) are not falsely penalized or rewarded.
"""

from dataclasses import dataclass, field
import json
from typing import Any, List, Optional


@dataclass(frozen=True)
class ScoreFactor:
    points: int
    reason: str


@dataclass
class LeadScoreResult:
    score: int
    grade: str
    reasons: List[str] = field(default_factory=list)


def score_to_grade(score: int) -> str:
    """Map numeric score (0-100) to letter grade."""
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _is_present(val: Any) -> bool:
    """Check if value is non-empty string or populated collection."""
    if val is None:
        return False
    if isinstance(val, str):
        trimmed = val.strip()
        return bool(trimmed and trimmed != "-" and trimmed.lower() != "null")
    if isinstance(val, (list, dict, set, tuple)):
        return len(val) > 0
    return bool(val)


def _parse_emails_list(emails_val: Any) -> List[str]:
    """Safely parse email collection or JSON string from database."""
    if not emails_val:
        return []
    if isinstance(emails_val, list):
        return [str(e).strip() for e in emails_val if _is_present(e)]
    if isinstance(emails_val, str):
        try:
            parsed = json.loads(emails_val)
            if isinstance(parsed, list):
                return [str(e).strip() for e in parsed if _is_present(e)]
        except Exception:
            pass
        if "@" in emails_val:
            return [emails_val.strip()]
    return []


def calculate_lead_score(business: Any, website_data: Optional[Any] = None) -> LeadScoreResult:
    """
    Calculate lead score (0-100), grade (A-D), and explainable reasons for a business.
    
    Compatible with SQLAlchemy Business model, dictionaries, or duck-typed objects.
    """
    factors: List[ScoreFactor] = []

    # Extract business properties safely
    if isinstance(business, dict):
        name = business.get("name")
        phone = business.get("phone")
        email = business.get("email")
        website = business.get("website")
        address = business.get("address")
        category = business.get("category")
    else:
        name = getattr(business, "name", None)
        phone = getattr(business, "phone", None)
        email = getattr(business, "email", None)
        website = getattr(business, "website", None)
        address = getattr(business, "address", None)
        category = getattr(business, "category", None)

    # Extract website_data if not directly passed but attached via relationship
    if website_data is None and not isinstance(business, dict):
        attached = getattr(business, "website_data", None)
        if attached:
            if isinstance(attached, list) and len(attached) > 0:
                website_data = attached[0]
            elif not isinstance(attached, list):
                website_data = attached

    # Extract website_data properties safely
    wd_title = None
    wd_desc = None
    wd_emails: List[str] = []
    wd_status = None
    social_links_count = 0

    if website_data:
        if isinstance(website_data, dict):
            wd_title = website_data.get("title")
            wd_desc = website_data.get("meta_description")
            wd_emails = _parse_emails_list(website_data.get("emails"))
            wd_status = website_data.get("status")
            social_keys = ["facebook", "instagram", "linkedin", "twitter", "youtube", "whatsapp"]
            social_links_count = sum(1 for k in social_keys if _is_present(website_data.get(k)))
        else:
            wd_title = getattr(website_data, "title", None)
            wd_desc = getattr(website_data, "meta_description", None)
            wd_emails = _parse_emails_list(getattr(website_data, "emails", None))
            wd_status = getattr(website_data, "status", None)
            social_attrs = ["facebook", "instagram", "linkedin", "twitter", "youtube", "whatsapp"]
            social_links_count = sum(1 for a in social_attrs if _is_present(getattr(website_data, a, None)))

    has_site = _is_present(website)
    has_email = _is_present(email) or len(wd_emails) > 0
    has_phone = _is_present(phone)

    # -------------------------------------------------------------
    # 1. CONTACTABILITY (up to 35 pts)
    # Direct channels determine whether a lead can be converted.
    # -------------------------------------------------------------
    if has_email:
        factors.append(ScoreFactor(15, "Email address available"))
    if has_phone:
        factors.append(ScoreFactor(15, "Phone number available for direct calling"))
    if has_email and has_phone:
        factors.append(ScoreFactor(5, "Multi-channel outreach available (Phone + Email)"))

    # -------------------------------------------------------------
    # 2. WEBSITE OPPORTUNITY (up to 35 pts)
    # High digital opportunity when a business lacks an online presence.
    # -------------------------------------------------------------
    if not has_site:
        factors.append(ScoreFactor(35, "No website (prime digital service opportunity)"))
    else:
        # Website exists: award opportunity points only if scrape completed and revealed thin presence
        if wd_status == "Completed" and not _is_present(wd_desc) and social_links_count == 0:
            factors.append(ScoreFactor(10, "Thin web presence (missing description and social profiles)"))

    # -------------------------------------------------------------
    # 3. SCRAPE / PROFILE ENRICHMENT (up to 20 pts)
    # Deeply enriched leads have verified secondary channels.
    # -------------------------------------------------------------
    if len(wd_emails) > 0:
        factors.append(ScoreFactor(10, "Scraped & verified website email"))
    if social_links_count >= 2:
        factors.append(ScoreFactor(10, "Multiple verified social media channels"))
    elif social_links_count == 1:
        factors.append(ScoreFactor(5, "Social media channel verified"))

    # -------------------------------------------------------------
    # 4. BUSINESS DATA QUALITY (up to 10 pts)
    # Completeness of metadata.
    # -------------------------------------------------------------
    if _is_present(address):
        factors.append(ScoreFactor(4, "Physical address verified"))
    if _is_present(category):
        factors.append(ScoreFactor(4, "Verified industry category"))
    if _is_present(name) and len(str(name).strip()) >= 2:
        factors.append(ScoreFactor(2, "Complete business name"))

    # Sum and strictly clamp score to 0..100
    raw_score = sum(f.points for f in factors)
    total_score = max(0, min(100, raw_score))
    grade = score_to_grade(total_score)
    reasons = [f"+{f.points} {f.reason}" for f in factors]

    return LeadScoreResult(
        score=total_score,
        grade=grade,
        reasons=reasons,
    )
