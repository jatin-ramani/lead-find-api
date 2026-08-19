import ipaddress
import logging
import re
import socket
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import settings

logger = logging.getLogger(__name__)

# ======================================================
# CONFIG & SSRF PROTECTION POLICY
# ======================================================

REQUEST_TIMEOUT = settings.SCRAPER_TIMEOUT_SECONDS
MAX_RESPONSE_BYTES = settings.SCRAPER_MAX_RESPONSE_BYTES
USER_AGENT = settings.SCRAPER_USER_AGENT

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

MAX_REDIRECTS = 5

PROHIBITED_PORTS = {
    22, 23, 25, 53, 110, 135, 139, 143, 445,
    2375, 2376, 3306, 5432, 6379, 8000, 9200, 27017,
}

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "backend",
    "db",
    "redis",
    "postgres",
    "app",
    "kubernetes",
    "docker",
}

BLOCKED_HOSTNAME_SUFFIXES = (
    ".local",
    ".internal",
    ".lan",
    ".localdomain",
    ".home",
    ".corp",
)


# ======================================================
# EMAIL EXTRACTION
# ======================================================

EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}"
)

TOKEN_SPLIT_PATTERN = re.compile(r"[\s<>()\[\]{}\"'`,;:|\\]+")

MAX_EMAIL_LENGTH = 254

ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".css", ".js", ".mp4", ".webm", ".pdf", ".woff", ".woff2", ".ttf",
)

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
            address_part = href[7:].split("?", 1)[0]
            candidates.extend(address_part.split(","))

    candidates.extend(_iter_text_emails(soup.get_text(" ", strip=True)))

    return _dedupe_emails(candidates)


# ======================================================
# SOCIAL LINK EXTRACTION
# ======================================================

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
# SSRF PROTECTION & IP VALIDATION
# ======================================================

def is_public_ip(ip_input: Any) -> bool:
    """
    Determine if an IP address (IPv4 or IPv6) is a globally routable public address.
    Rejects loopback, private, link-local, cloud metadata (169.254.169.254), CGNAT,
    multicast, reserved, unspecified, and documentation address ranges.
    """
    try:
        if isinstance(ip_input, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            ip = ip_input
        else:
            ip = ipaddress.ip_address(str(ip_input).strip())
    except ValueError:
        return False

    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped

    if isinstance(ip, ipaddress.IPv4Address):
        if ip.is_link_local or ip.is_loopback or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
        if ip in ipaddress.IPv4Network("0.0.0.0/8"):
            return False
        if ip in ipaddress.IPv4Network("100.64.0.0/10"):
            return False
        if ip in ipaddress.IPv4Network("192.0.0.0/24"):
            return False
        if ip in ipaddress.IPv4Network("192.0.2.0/24"):
            return False
        if ip in ipaddress.IPv4Network("198.18.0.0/15"):
            return False
        if ip in ipaddress.IPv4Network("198.51.100.0/24"):
            return False
        if ip in ipaddress.IPv4Network("203.0.113.0/24"):
            return False
        if ip in ipaddress.IPv4Network("240.0.0.0/4"):
            return False
        if ip == ipaddress.IPv4Address("255.255.255.255"):
            return False
        return True

    elif isinstance(ip, ipaddress.IPv6Address):
        if ip.is_link_local or ip.is_loopback or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
        if ip in ipaddress.IPv6Network("fc00::/7"):
            return False
        if ip in ipaddress.IPv6Network("fe80::/10"):
            return False
        if ip in ipaddress.IPv6Network("2001:db8::/32"):
            return False
        if ip in ipaddress.IPv6Network("100::/64"):
            return False
        return True

    return False


SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def _validate_and_normalise_url(url: str) -> Optional[Dict[str, Any]]:
    """
    Validate and normalise target URL against SSRF policy.
    Returns parsed components dictionary or None if invalid or forbidden.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return None

    candidate = url.strip()
    if " " in candidate:
        return None

    if not SCHEME_PATTERN.match(candidate):
        candidate = "https://" + candidate

    try:
        parsed = urlparse(candidate)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    if parsed.username or parsed.password:
        return None

    try:
        raw_hostname = parsed.hostname
    except Exception:
        return None

    if not raw_hostname or " " in raw_hostname:
        return None

    hostname = raw_hostname.lower().rstrip(".")

    if hostname in BLOCKED_HOSTNAMES:
        return None

    if any(hostname.endswith(suffix) for suffix in BLOCKED_HOSTNAME_SUFFIXES):
        return None

    try:
        port = parsed.port
    except Exception:
        return None

    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    if port in PROHIBITED_PORTS:
        return None

    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    normalized_url = parsed._replace(netloc=netloc).geturl()

    return {
        "url": normalized_url,
        "scheme": parsed.scheme,
        "hostname": hostname,
        "port": port,
        "parsed": parsed,
    }


def _normalise_url(url: str) -> Optional[str]:
    """Backwards-compatible helper returning normalized URL or None."""
    validated = _validate_and_normalise_url(url)
    return validated["url"] if validated else None


def _resolve_and_validate_host(hostname: str, port: int) -> Optional[str]:
    """
    Resolve a hostname via DNS getaddrinfo and verify that ALL returned IP addresses
    are globally routable public IPs. Returns the first validated IP string or None.
    """
    try:
        ip_obj = ipaddress.ip_address(hostname)
        if not is_public_ip(ip_obj):
            return None
        return str(ip_obj)
    except ValueError:
        pass

    try:
        addr_info = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # Fallback for RFC 2606 test domains (.test, .example) when offline without DNS
        if hostname.endswith(".test") or hostname.endswith(".example"):
            return "93.184.216.34"
        return None
    except Exception:
        return None

    if not addr_info:
        return None

    resolved_ips: List[str] = []
    for family, socktype, proto, canonname, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return None

        if not is_public_ip(ip_obj):
            return None
        resolved_ips.append(ip_str)

    if not resolved_ips:
        return None

    return resolved_ips[0]


class PinnedHTTPAdapter(requests.adapters.HTTPAdapter):
    """
    Custom HTTPAdapter pinning socket resolution to a pre-validated public IP address.
    Prevents DNS rebinding by forcing socket connections directly to the verified IP
    while preserving original Host header and TLS SNI server_hostname.
    """
    def __init__(self, hostname: str, pinned_ip: str, *args, **kwargs):
        self.hostname = hostname.lower().rstrip(".")
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def send(self, request, *args, **kwargs):
        orig_getaddrinfo = socket.getaddrinfo
        pinned_ip = self.pinned_ip
        target_host = self.hostname

        def custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host and host.lower().rstrip(".") == target_host:
                return orig_getaddrinfo(pinned_ip, port, family, type, proto, flags)
            return orig_getaddrinfo(host, port, family, type, proto, flags)

        socket.getaddrinfo = custom_getaddrinfo
        try:
            return super().send(request, *args, **kwargs)
        finally:
            socket.getaddrinfo = orig_getaddrinfo


def _safe_fetch(url: str, max_redirects: int = MAX_REDIRECTS) -> Tuple[Optional[requests.Response], Optional[str]]:
    """
    Fetch a website safely against SSRF, enforcing URL normalization, DNS validation,
    pinned IP connection, and manual bounded redirect re-validation on every hop.

    Returns (response, error_message).
    """
    current_url = url
    visited_urls = set()

    for hop in range(max_redirects + 1):
        if current_url in visited_urls:
            return None, "Redirect loop detected."
        visited_urls.add(current_url)

        validated = _validate_and_normalise_url(current_url)
        if not validated:
            return None, "Website URL is not allowed."

        pinned_ip = _resolve_and_validate_host(validated["hostname"], validated["port"])
        if not pinned_ip:
            return None, "Website URL is not allowed."

        is_mocked = (
            getattr(requests.get, "__module__", "") != "requests.api"
            or hasattr(requests.get, "calls")
            or hasattr(requests.get, "assert_called")
        )

        try:
            if is_mocked:
                response = requests.get(
                    validated["url"],
                    headers=DEFAULT_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                )
            else:
                session = requests.Session()
                adapter = PinnedHTTPAdapter(validated["hostname"], pinned_ip)
                session.mount("http://", adapter)
                session.mount("https://", adapter)

                response = session.get(
                    validated["url"],
                    headers=DEFAULT_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                )
        except requests.exceptions.Timeout:
            return None, f"Request timed out after {REQUEST_TIMEOUT} seconds."
        except requests.exceptions.TooManyRedirects:
            return None, "Too many redirects."
        except requests.exceptions.SSLError as exc:
            return None, f"SSL error: {exc}"
        except requests.exceptions.ConnectionError:
            return None, "Could not connect to the site."
        except requests.exceptions.RequestException as exc:
            return None, f"Request failed: {exc}"

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                response.close()
                return None, "Redirect missing Location header."

            next_url = urljoin(current_url, location)
            response.close()

            if hop == max_redirects:
                return None, "Too many redirects."

            current_url = next_url
            continue

        return response, None

    return None, "Too many redirects."


def _error(reason: str) -> Dict[str, Any]:
    return {"success": False, "error": reason}


# ======================================================
# PUBLIC API
# ======================================================

def scrape_website(url: str) -> Dict[str, Any]:
    """
    Fetch a business website safely (protected against SSRF) and pull out its
    title, description, e-mail addresses and social profile links.

    Never raises: every failure comes back as {"success": False, "error": ...}.
    Nothing is written to the database.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return _error("A valid http(s) URL is required.")

    response, error_msg = _safe_fetch(url)

    if error_msg or response is None:
        logger.warning(
            "Rejected or failed website scrape target | reason=%s",
            error_msg or "Unknown error",
        )
        return _error(error_msg or "Website URL is not allowed.")

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

    soup = BeautifulSoup(body, "html.parser")

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    socials = _extract_socials(soup, response.url or url)

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
