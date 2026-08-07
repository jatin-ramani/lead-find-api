import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import settings

# ======================================================
# CONFIG
# ======================================================

REQUEST_TIMEOUT = settings.SCRAPER_TIMEOUT_SECONDS

# Some sites serve a stripped page or a 403 to non-browser agents.
USER_AGENT = settings.SCRAPER_USER_AGENT

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Stop reading a response that is far larger than any real marketing page,
# so one bad URL cannot exhaust memory.
MAX_RESPONSE_BYTES = settings.SCRAPER_MAX_RESPONSE_BYTES

HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


# ======================================================
# EMAIL EXTRACTION
# ======================================================

EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}"
)

# The pattern above is quadratic when run across a whole document: on a long
# run of characters with no "@" it backtracks from every start position. Text
# is therefore split into tokens first and the regex only ever sees a short
# candidate, which keeps extraction linear in page size.
TOKEN_SPLIT_PATTERN = re.compile(r"[\s<>()\[\]{}\"'`,;:|\\]+")

# RFC 5321 caps an address at 254 characters; anything longer is not one.
MAX_EMAIL_LENGTH = 254

# Asset filenames such as "logo@2x.png" match the shape of an address.
ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".css", ".js", ".mp4", ".webm", ".pdf", ".woff", ".woff2", ".ttf",
)

# Boilerplate that appears on templated sites but belongs to nobody.
PLACEHOLDER_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "yourdomain.com",
    "yourcompany.com",
    "email.com",
    "sentry.io",
    "wixpress.com",
    "godaddy.com",
}


def _is_valid_email(email: str) -> bool:
    """Reject asset filenames, placeholder domains and malformed addresses."""

    if email.count("@") != 1:
        return False

    local, _, domain = email.partition("@")

    if not local or not domain:
        return False

    if ".." in local or ".." in domain:
        return False

    if local.startswith(".") or local.endswith("."):
        return False

    if domain.startswith("-") or domain.endswith("-"):
        return False

    lowered = email.lower()

    if lowered.endswith(ASSET_SUFFIXES):
        return False

    domain_lower = domain.lower()

    if domain_lower in PLACEHOLDER_DOMAINS:
        return False

    # Also covers subdomains such as o123.ingest.sentry.io.
    if any(
        domain_lower.endswith("." + placeholder)
        for placeholder in PLACEHOLDER_DOMAINS
    ):
        return False

    return True


def _dedupe_emails(candidates: Iterable[str]) -> List[str]:
    """Drop invalid addresses and duplicates, keeping first-seen order."""

    seen = set()
    emails: List[str] = []

    for candidate in candidates:
        email = candidate.strip().strip(".,;:<>()[]\"'").lstrip("/")

        if not _is_valid_email(email):
            continue

        key = email.lower()

        if key in seen:
            continue

        seen.add(key)
        emails.append(email)

    return emails


def _iter_text_emails(text: str) -> Iterable[str]:
    """Scan tokenised text so the regex never runs across the whole document."""

    for token in TOKEN_SPLIT_PATTERN.split(text):

        if "@" not in token or len(token) > MAX_EMAIL_LENGTH:
            continue

        yield from EMAIL_PATTERN.findall(token)


def _extract_emails(soup: BeautifulSoup) -> List[str]:
    """Collect addresses from mailto: links first, then from visible text."""

    candidates: List[str] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()

        if href.lower().startswith("mailto:"):
            # Strip any ?subject=... and split grouped recipients.
            address_part = href[7:].split("?", 1)[0]
            candidates.extend(address_part.split(","))

    candidates.extend(_iter_text_emails(soup.get_text(" ", strip=True)))

    return _dedupe_emails(candidates)


# ======================================================
# SOCIAL LINK EXTRACTION
# ======================================================

# host suffixes that identify a network, plus paths that are share widgets
# rather than the business's own profile.
SOCIAL_NETWORKS: Dict[str, Dict[str, tuple]] = {
    "facebook": {
        "hosts": ("facebook.com", "fb.com", "fb.me"),
        "exclude": ("/sharer", "/share.php", "/plugins/", "/dialog/"),
    },
    "instagram": {
        "hosts": ("instagram.com", "instagr.am"),
        "exclude": (),
    },
    "linkedin": {
        "hosts": ("linkedin.com", "lnkd.in"),
        "exclude": ("/sharearticle", "/sharing/", "/shareindicator"),
    },
    "twitter": {
        "hosts": ("twitter.com", "x.com"),
        "exclude": ("/intent/", "/share", "/widgets"),
    },
    "youtube": {
        "hosts": ("youtube.com", "youtu.be"),
        "exclude": ("/embed/",),
    },
    "whatsapp": {
        "hosts": ("wa.me", "api.whatsapp.com", "web.whatsapp.com", "chat.whatsapp.com"),
        "exclude": (),
    },
}


def _matches_host(host: str, suffixes: tuple) -> bool:
    """Exact host or a subdomain of it — never a lookalike like notfb.com."""

    host = host.lower()

    if host.startswith("www."):
        host = host[4:]

    return any(
        host == suffix or host.endswith("." + suffix) for suffix in suffixes
    )


def _extract_socials(soup: BeautifulSoup, base_url: str) -> Dict[str, Optional[str]]:
    """Return the first profile link found per network."""

    found: Dict[str, Optional[str]] = {name: None for name in SOCIAL_NETWORKS}

    for anchor in soup.find_all("a", href=True):
        raw_href = anchor["href"].strip()

        if not raw_href or raw_href.startswith(("mailto:", "tel:", "#")):
            continue

        # The whatsapp:// app scheme has no host to match on.
        if raw_href.lower().startswith("whatsapp:"):
            if found["whatsapp"] is None:
                found["whatsapp"] = raw_href
            continue

        absolute = urljoin(base_url, raw_href)
        parsed = urlparse(absolute)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue

        path_and_query = (parsed.path + "?" + parsed.query).lower()

        for name, rules in SOCIAL_NETWORKS.items():

            if found[name] is not None:
                continue

            if not _matches_host(parsed.netloc, rules["hosts"]):
                continue

            if any(token in path_and_query for token in rules["exclude"]):
                continue

            found[name] = absolute

        if all(value is not None for value in found.values()):
            break

    return found


# ======================================================
# METADATA EXTRACTION
# ======================================================

def _collapse(value: Optional[str]) -> Optional[str]:
    """Normalise whitespace; return None for an effectively empty string."""

    if not value:
        return None

    collapsed = re.sub(r"\s+", " ", value).strip()

    return collapsed or None


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    if soup.title:
        title = _collapse(soup.title.get_text())

        if title:
            return title

    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        tag = soup.find("meta", attrs=attrs)

        if tag and tag.get("content"):
            title = _collapse(tag["content"])

            if title:
                return title

    heading = soup.find("h1")

    return _collapse(heading.get_text()) if heading else None


def _extract_meta_description(soup: BeautifulSoup) -> Optional[str]:
    candidates = (
        {"name": re.compile(r"^description$", re.I)},
        {"property": "og:description"},
        {"name": re.compile(r"^twitter:description$", re.I)},
    )

    for attrs in candidates:
        tag = soup.find("meta", attrs=attrs)

        if tag and tag.get("content"):
            description = _collapse(tag["content"])

            if description:
                return description

    return None


# ======================================================
# URL HANDLING
# ======================================================

SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def _normalise_url(url: str) -> Optional[str]:
    """Add https:// when no scheme is given; reject anything non-http."""

    if not url or not url.strip():
        return None

    candidate = url.strip()

    if not SCHEME_PATTERN.match(candidate):
        candidate = "https://" + candidate

    parsed = urlparse(candidate)

    if parsed.scheme not in ("http", "https"):
        return None

    if not parsed.netloc or "." not in parsed.netloc:
        return None

    return candidate


def _error(reason: str) -> Dict[str, Any]:
    return {"success": False, "error": reason}


# ======================================================
# PUBLIC API
# ======================================================

def scrape_website(url: str) -> Dict[str, Any]:
    """
    Fetch a business website and pull out its title, description, e-mail
    addresses and social profile links.

    Never raises: every failure comes back as {"success": False, "error": ...}.
    Nothing is written to the database.
    """

    target = _normalise_url(url)

    if target is None:
        return _error("A valid http(s) URL is required.")

    try:
        response = requests.get(
            target,
            headers=DEFAULT_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
    except requests.exceptions.Timeout:
        return _error(f"Request timed out after {REQUEST_TIMEOUT} seconds.")
    except requests.exceptions.TooManyRedirects:
        return _error("Too many redirects.")
    except requests.exceptions.SSLError as exc:
        return _error(f"SSL error: {exc}")
    except requests.exceptions.ConnectionError:
        return _error("Could not connect to the site.")
    except requests.exceptions.RequestException as exc:
        return _error(f"Request failed: {exc}")

    try:
        if response.status_code >= 400:
            return _error(
                f"Site returned HTTP {response.status_code} "
                f"{response.reason or ''}".strip()
            )

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()

        if content_type and not content_type.lower().startswith(HTML_CONTENT_TYPES):
            return _error(f"Unsupported content type: {content_type}")

        try:
            chunks = bytearray()

            for chunk in response.iter_content(chunk_size=16384):
                chunks.extend(chunk)

                if len(chunks) >= MAX_RESPONSE_BYTES:
                    break

            body = bytes(chunks)
        except requests.exceptions.RequestException as exc:
            return _error(f"Failed while reading the response: {exc}")

        if not body:
            return _error("The site returned an empty response.")
    finally:
        response.close()

    # Passing bytes lets BeautifulSoup detect the encoding itself, which is
    # more reliable than the ISO-8859-1 requests falls back to.
    soup = BeautifulSoup(body, "html.parser")

    # Script and style contents are not visible text and produce junk matches.
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    socials = _extract_socials(soup, response.url or target)

    return {
        "success": True,
        "title": _extract_title(soup),
        "meta_description": _extract_meta_description(soup),
        "emails": _extract_emails(soup),
        "facebook": socials["facebook"],
        "instagram": socials["instagram"],
        "linkedin": socials["linkedin"],
        "twitter": socials["twitter"],
        "youtube": socials["youtube"],
        "whatsapp": socials["whatsapp"],
    }
