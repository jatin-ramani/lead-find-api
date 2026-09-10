"""
Safe Template Variable Engine for Lead Finder Email Automations.

Provides deterministic, allowlisted placeholder substitution for email subjects and bodies.
Guarantees:
- Only allowlisted variables are interpolated
- Raw expressions / code injections are rejected or left unmodified
- Missing values degrade gracefully to empty strings or fallbacks
"""

import html
import re
from typing import Any, Dict, List

ALLOWLISTED_TEMPLATE_VARIABLES: List[str] = [
    "business_name",
    "contact_name",
    "city",
    "email",
    "phone",
    "website",
    "lead_status",
    "lead_score",
    "follow_up_title",
    "follow_up_due_at",
]

# Regex pattern matching {{ variable_name }} or {{ Variable Name }}
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_ ]+?)\s*\}\}")


def get_supported_variables() -> List[Dict[str, str]]:
    """Return descriptors for all supported template variables."""
    return [
        {"key": "business_name", "label": "Business Name", "description": "The name of the lead or company", "example": "Apex Dental Clinic"},
        {"key": "contact_name", "label": "Contact Name", "description": "Primary contact name if available", "example": "Dr. Sarah Smith"},
        {"key": "city", "label": "City", "description": "City or geographic location of the business", "example": "Surat"},
        {"key": "email", "label": "Email Address", "description": "Business contact email address", "example": "contact@apexdental.com"},
        {"key": "phone", "label": "Phone Number", "description": "Business telephone number", "example": "+1 555-0199"},
        {"key": "website", "label": "Website URL", "description": "Business website URL", "example": "https://apexdental.com"},
        {"key": "lead_status", "label": "CRM Status", "description": "Current pipeline status", "example": "New"},
        {"key": "lead_score", "label": "Lead Score", "description": "Calculated qualification score (0-100)", "example": "85"},
        {"key": "follow_up_title", "label": "Follow-Up Title", "description": "Title of the associated follow-up task", "example": "Send product overview"},
        {"key": "follow_up_due_at", "label": "Follow-Up Due Date", "description": "Formatted due date/time of the task", "example": "2026-09-10 14:00 UTC"},
    ]


MAX_TEMPLATE_LENGTH = 100_000


def render_template(
    template_str: str,
    context: Dict[str, Any],
    escape_html: bool = False,
) -> str:
    """
    Render a template string by replacing allowlisted placeholder variables.

    Args:
        template_str: The raw template containing `{{var}}` placeholders.
        context: Mapping of variable names to their values.
        escape_html: If True, values will be HTML-escaped before insertion.

    Returns:
        Rendered string with placeholders resolved.
    """
    if not template_str:
        return ""

    # Truncate overly long templates to avoid excessive memory consumption / ReDoS
    safe_template = template_str[:MAX_TEMPLATE_LENGTH]

    def _replace_match(match: re.Match) -> str:
        raw_key = match.group(1).strip()
        var_name = raw_key.lower().replace(" ", "_")
        if var_name in ALLOWLISTED_TEMPLATE_VARIABLES:
            val = context.get(var_name)
            if val is None:
                val = context.get(raw_key)
            if val is None:
                return ""
            val_str = str(val)
            # Bound single variable replacement length to 10,000 chars
            val_str = val_str[:10_000]
            return html.escape(val_str) if escape_html else val_str
        # Non-allowlisted placeholder is left intact
        return match.group(0)

    return _VARIABLE_PATTERN.sub(_replace_match, safe_template)


def html_to_plain_text(html_str: str) -> str:
    """
    Convert HTML email content to clean plain-text fallback.
    Converts <p> to paragraph breaks, <br> to newlines, and strips HTML tags.
    """
    if not html_str:
        return ""
    text = html_str
    # Replace <br> and <br/> with newline
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Replace </p> with double newline
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = html.unescape(text)
    # Normalize multiple newlines (max 2 consecutive)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ensure_html_email(content: str) -> str:
    """
    Ensure the email body is clean, properly formatted HTML with <p> and <br> tags.
    - If content already contains HTML block tags (<p>, <div>, <br>), returns it intact.
    - If content is plain text, converts paragraphs (separated by double newlines) into <p> tags,
      single line breaks into <br>, and selectively emphasizes approved cold-email value propositions.
    """
    if not content:
        return ""

    text = content.strip()
    # Check if already contains HTML tags
    if re.search(r"<(p|div|br|html|body|table)\b", text, re.IGNORECASE):
        return text

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    html_paragraphs = []

    for p in paragraphs:
        lines = [line.strip() for line in p.split("\n") if line.strip()]
        p_content = "<br>\n".join(lines)
        html_paragraphs.append(f"<p>{p_content}</p>")

    html_body = "\n\n".join(html_paragraphs)

    # Selectively bold approved value propositions if present in plaintext without <strong>
    strong_phrases = [
        "look more credible, capture more leads and turn visitors into customers.",
        "new customers, enquiries and appointments.",
        "Jatin Ramani",
        "Codebait",
        "show you what your business could look like online",
        "free, no-obligation website mockup",
        "Would you be open to seeing the mockup?",
    ]
    for phrase in strong_phrases:
        if phrase in html_body and f"<strong>{phrase}</strong>" not in html_body:
            html_body = html_body.replace(phrase, f"<strong>{phrase}</strong>", 1)

    return html_body

