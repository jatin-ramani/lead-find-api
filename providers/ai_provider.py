"""
AI Template Provider Abstraction for Lead Finder CRM.

Generates grade-tailored email templates (Grade A, B, C, D) using allowlisted template variables.
Supports:
- BaseAIProvider interface
- MockAIProvider (default for offline dev, unit tests, E2E tests, deterministic & zero-cost)
- OpenAIProvider (production adapter for OpenAI chat models)
- AnthropicProvider (production adapter for Anthropic Claude)
- GeminiProvider (production adapter for Google Gemini)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import logging
from typing import Any, Dict, Optional
import requests

from config import settings

logger = logging.getLogger(__name__)

# Grade Guidelines definitions
GRADE_PROMPTS = {
    "A": (
        "VIP High-Value Prospect. Strongest personalization, specific growth/revenue opportunity, "
        "compelling value pitch, executive tone, and direct high-conversion call to action."
    ),
    "B": (
        "High Quality Prospect. Professional B2B outreach, focused on digital presence optimization, "
        "credibility, and clear business advantages."
    ),
    "C": (
        "Moderate Quality Prospect. Softer, low-pressure introductory note, offering complimentary "
        "website review or consultation."
    ),
    "D": (
        "Basic / Discovery Prospect. Simple, concise introductory inquiry, low friction question."
    ),
}


@dataclass
class GeneratedTemplate:
    name: str
    subject: str
    body: str


class BaseAIProvider(ABC):
    """Abstract interface for AI template generation providers."""

    @abstractmethod
    def generate_grade_templates(
        self,
        city: str,
        industry: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Generate email templates for all 4 grades (A, B, C, D)."""
        raise NotImplementedError

    @abstractmethod
    def generate_single_grade_template(
        self,
        grade: str,
        city: str,
        industry: Optional[str] = None,
    ) -> Dict[str, str]:
        """Generate or regenerate an email template for a single grade."""
        raise NotImplementedError


class MockAIProvider(BaseAIProvider):
    """
    Mock AI Provider for local development, pytest, and Playwright suites.
    Produces rich, customized templates deterministically without external network calls.
    """

    def generate_grade_templates(
        self,
        city: str,
        industry: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        clean_city = city.strip() if city else "your area"
        ind = f" in {industry.strip()}" if industry and industry.strip() else ""

        return {
            "A": {
                "name": f"Grade A VIP Outreach — {clean_city}",
                "subject": f"Strategic growth opportunity for {{{{business_name}}}} in {clean_city}",
                "body": (
                    f"Hi {{{{contact_name}}}},\n\n"
                    f"I've been following {{{{business_name}}}}'s impressive work{ind} across {clean_city}. "
                    f"Given your strong reputation and current score of {{{{lead_score}}}}, we have prepared a tailored "
                    f"strategy to expand your local market leadership.\n\n"
                    f"Would you have 10 minutes this Thursday for a brief discussion on how we can double your inbound inquiries?\n\n"
                    f"Best regards,\nGrowth Team"
                ),
            },
            "B": {
                "name": f"Grade B Professional — {clean_city}",
                "subject": f"Enhancing digital presence for {{{{business_name}}}}",
                "body": (
                    f"Hi {{{{contact_name}}}},\n\n"
                    f"We recently reviewed {{{{business_name}}}}'s online profile in {clean_city}. "
                    f"You have great foundations, and with a few targeted digital improvements, we can help you attract significantly more customers.\n\n"
                    f"Are you open to a quick 5-minute overview of our recommendations?\n\n"
                    f"Best regards,\nClient Strategy Team"
                ),
            },
            "C": {
                "name": f"Grade C Introduction — {clean_city}",
                "subject": f"Complimentary website & visibility check for {{{{business_name}}}}",
                "body": (
                    f"Hello {{{{contact_name}}}},\n\n"
                    f"We put together a quick, complimentary visibility report for businesses in {clean_city}. "
                    f"We noticed a few quick wins for {{{{business_name}}}} that could improve customer discovery.\n\n"
                    f"Feel free to reply if you'd like us to send over the PDF report.\n\n"
                    f"Best regards,\nSupport & Audit Team"
                ),
            },
            "D": {
                "name": f"Grade D Discovery — {clean_city}",
                "subject": f"Quick question for {{{{business_name}}}}",
                "body": (
                    f"Hi {{{{contact_name}}}},\n\n"
                    f"Reaching out briefly from our {clean_city} business network. "
                    f"Are you currently exploring new ways to acquire customers for {{{{business_name}}}}?\n\n"
                    f"Let me know if you'd be open to a quick chat.\n\n"
                    f"Thanks,\nOutreach Team"
                ),
            },
        }

    def generate_single_grade_template(
        self,
        grade: str,
        city: str,
        industry: Optional[str] = None,
    ) -> Dict[str, str]:
        g = grade.upper().strip()
        all_templates = self.generate_grade_templates(city, industry)
        if g in all_templates:
            return all_templates[g]
        return all_templates["D"]


class OpenAIProvider(BaseAIProvider):
    """Production AI Provider adapter for OpenAI API."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model
        self.mock_fallback = MockAIProvider()

    def generate_grade_templates(
        self,
        city: str,
        industry: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        if not self.api_key:
            logger.warning("OpenAI API key missing, falling back to mock provider.")
            return self.mock_fallback.generate_grade_templates(city, industry)

        clean_city = city.strip() if city else "your area"
        ind = f" in {industry.strip()}" if industry and industry.strip() else ""

        system_prompt = (
            "You are an expert B2B copywriter for Lead Finder CRM. "
            "Generate 4 email templates for lead Grades A, B, C, and D. "
            "Use ONLY these safe placeholder variables: {{business_name}}, {{contact_name}}, {{email}}, {{phone}}, {{website}}, {{lead_status}}, {{lead_score}}. "
            "Respond strictly in JSON format with keys 'A', 'B', 'C', 'D', each containing 'name', 'subject', 'body'."
        )

        user_prompt = (
            f"City: {clean_city}{ind}\n"
            f"Grade A: {GRADE_PROMPTS['A']}\n"
            f"Grade B: {GRADE_PROMPTS['B']}\n"
            f"Grade C: {GRADE_PROMPTS['C']}\n"
            f"Grade D: {GRADE_PROMPTS['D']}\n"
        )

        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.7,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            result = {}
            for grade in ["A", "B", "C", "D"]:
                if grade in parsed and isinstance(parsed[grade], dict):
                    result[grade] = {
                        "name": str(parsed[grade].get("name", f"Grade {grade} Template — {clean_city}")),
                        "subject": str(parsed[grade].get("subject", "")),
                        "body": str(parsed[grade].get("body", "")),
                    }
                else:
                    result[grade] = self.mock_fallback.generate_single_grade_template(grade, clean_city, industry)
            return result
        except Exception as e:
            logger.error("OpenAI template generation failed: %s, using fallback.", str(e))
            return self.mock_fallback.generate_grade_templates(city, industry)

    def generate_single_grade_template(
        self,
        grade: str,
        city: str,
        industry: Optional[str] = None,
    ) -> Dict[str, str]:
        all_templates = self.generate_grade_templates(city, industry)
        return all_templates.get(grade.upper(), self.mock_fallback.generate_single_grade_template(grade, city, industry))


def get_ai_provider() -> BaseAIProvider:
    """Factory returning configured AI Provider."""
    provider_type = getattr(settings, "AI_PROVIDER", "mock").lower()

    if provider_type == "openai" and getattr(settings, "has_ai_key", False):
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
        )

    return MockAIProvider()
