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
    "email",
    "phone",
    "website",
    "lead_status",
    "lead_score",
    "follow_up_title",
    "follow_up_due_at",
]

# Regex pattern matching {{ variable_name }}
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def get_supported_variables() -> List[Dict[str, str]]:
    """Return descriptors for all supported template variables."""
    return [
        {"key": "business_name", "label": "Business Name", "description": "The name of the lead or company", "example": "Apex Dental Clinic"},
        {"key": "contact_name", "label": "Contact Name", "description": "Primary contact name if available", "example": "Dr. Sarah Smith"},
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
        var_name = match.group(1).lower()
        if var_name in ALLOWLISTED_TEMPLATE_VARIABLES:
            val = context.get(var_name)
            if val is None:
                return ""
            val_str = str(val)
            # Bound single variable replacement length to 10,000 chars
            val_str = val_str[:10_000]
            return html.escape(val_str) if escape_html else val_str
        # Non-allowlisted placeholder is left intact
        return match.group(0)

    return _VARIABLE_PATTERN.sub(_replace_match, safe_template)
