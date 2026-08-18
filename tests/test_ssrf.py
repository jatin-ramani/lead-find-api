import ipaddress
import socket
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.website_scraper import (
    is_public_ip,
    _validate_and_normalise_url,
    _resolve_and_validate_host,
    _safe_fetch,
    scrape_website,
)

# ======================================================
# IP VALIDATION UNIT TESTS
# ======================================================

class TestIpValidation:
    """Test is_public_ip against all IPv4/IPv6 address classes."""

    @pytest.mark.parametrize(
        "ip_str",
        [
            # Loopback
            "127.0.0.1",
            "127.0.0.2",
            "127.255.255.254",
            "::1",
            # RFC 1918 Private
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.0.1",
            "192.168.255.255",
            # Link local & Cloud metadata
            "169.254.169.254",
            "169.254.0.1",
            "fe80::1",
            "fe80::ffff",
            # IPv6 Unique Local Address (ULA)
            "fc00::1",
            "fd00::1",
            # IPv4-mapped IPv6 loopback / private / metadata
            "::ffff:127.0.0.1",
            "::ffff:10.0.0.1",
            "::ffff:169.254.169.254",
            "::ffff:192.168.1.1",
            # Unspecified & Broadcast
            "0.0.0.0",
            "255.255.255.255",
            "::",
            # Multicast & Reserved
            "224.0.0.1",
            "239.255.255.255",
            "240.0.0.1",
            "ff02::1",
            # CGNAT & Test Networks
            "100.64.0.1",
            "192.0.2.1",
            "198.51.100.1",
            "203.0.113.1",
            "2001:db8::1",
        ],
    )
    def test_blocked_ips(self, ip_str):
        assert is_public_ip(ip_str) is False

    @pytest.mark.parametrize(
        "ip_str",
        [
            "93.184.216.34",      # example.com
            "8.8.8.8",            # Google DNS
            "1.1.1.1",            # Cloudflare DNS
            "142.250.190.46",     # Google
            "2606:2800:220:1:248:1893:25c8:1946", # example.com IPv6
            "2607:f8b0:4005:805::200e",          # Google IPv6
        ],
    )
    def test_public_ips(self, ip_str):
        assert is_public_ip(ip_str) is True


# ======================================================
# URL VALIDATION & SCHEME / PORT TESTS
# ======================================================

class TestUrlValidation:
    """Test URL normalization, scheme filtering, userinfo rejection, and port policies."""

    @pytest.mark.parametrize(
        "raw_url",
        [
            "http://127.0.0.1",
            "http://localhost",
            "http://localhost:8000",
            "http://0.0.0.0",
            "http://10.0.0.1",
            "http://172.16.0.1",
            "http://192.168.1.1",
            "http://169.254.169.254",
            "http://[::1]",
            "http://[fc00::1]",
            "http://[fe80::1]",
            "http://localhost.localdomain",
            "http://backend:8000",
            "http://db:5432",
            "http://redis:6379",
            "http://app.local",
            "file:///etc/passwd",
            "ftp://example.com",
            "gopher://example.com",
            "data:text/html,hello",
            "javascript:alert(1)",
            "ws://example.com",
            "http://user:pass@example.com",
            "http://example.com:22",
            "http://example.com:25",
            "http://example.com:3306",
            "http://example.com:5432",
            "http://example.com:6379",
            "http://example.com:8000",
        ],
    )
    def test_rejected_urls(self, raw_url):
        result = scrape_website(raw_url)
        assert result["success"] is False
        assert result["error"] in ("Website URL is not allowed.", "A valid http(s) URL is required.")

    @pytest.mark.parametrize(
        "raw_url,expected_hostname",
        [
            ("example.com", "example.com"),
            ("https://www.example.com", "www.example.com"),
            ("http://EXAMPLE.COM", "example.com"),
            ("http://example.com.", "example.com"),
        ],
    )
    def test_allowed_url_normalization(self, raw_url, expected_hostname):
        validated = _validate_and_normalise_url(raw_url)
        assert validated is not None
        assert validated["hostname"] == expected_hostname


# ======================================================
# DNS RESOLUTION TESTS
# ======================================================

class TestDnsResolution:
    """Test DNS resolution against private, public, and mixed A/AAAA records."""

    @pytest.mark.parametrize(
        "resolved_ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "::1",
            "fc00::1",
            "fe80::1",
        ],
    )
    def test_dns_resolving_to_private_ip_is_rejected(self, resolved_ip):
        mock_addr = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved_ip, 80))
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr):
            pinned_ip = _resolve_and_validate_host("fake-domain.test", 80)
            assert pinned_ip is None

    def test_dns_resolving_to_public_ip_is_accepted(self):
        mock_addr = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr):
            pinned_ip = _resolve_and_validate_host("example.com", 80)
            assert pinned_ip == "93.184.216.34"

    def test_dns_returning_mixed_public_and_private_is_rejected(self):
        """If ANY resolved IP is private/internal, the domain must be rejected."""
        mock_addr = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr):
            pinned_ip = _resolve_and_validate_host("mixed-dns.test", 80)
            assert pinned_ip is None


# ======================================================
# REDIRECT HARDENING TESTS
# ======================================================

class TestRedirectHardening:
    """Test bounded manual redirect loop and redirection target validation."""

    def test_redirect_to_localhost_is_rejected(self):
        # Initial target resolves to public IP, but redirects to 127.0.0.1
        resp1 = MagicMock()
        resp1.status_code = 302
        resp1.headers = {"Location": "http://127.0.0.1/system"}

        with patch("socket.getaddrinfo") as mock_dns, patch("requests.Session.get") as mock_get:
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
            mock_get.return_value = resp1

            result = scrape_website("http://example.com")
            assert result["success"] is False
            assert result["error"] == "Website URL is not allowed."

    def test_redirect_to_cloud_metadata_is_rejected(self):
        resp1 = MagicMock()
        resp1.status_code = 301
        resp1.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        with patch("socket.getaddrinfo") as mock_dns, patch("requests.Session.get") as mock_get:
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
            mock_get.return_value = resp1

            result = scrape_website("http://example.com")
            assert result["success"] is False
            assert result["error"] == "Website URL is not allowed."

    def test_excessive_redirects_are_rejected(self):
        resp = MagicMock()
        resp.status_code = 302
        resp.headers = {"Location": "http://example.com/loop"}

        with patch("socket.getaddrinfo") as mock_dns, patch("requests.Session.get") as mock_get:
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
            mock_get.return_value = resp

            result = scrape_website("http://example.com")
            assert result["success"] is False
            assert "redirect" in result["error"].lower()

    def test_public_to_public_redirect_is_allowed(self):
        resp1 = MagicMock()
        resp1.status_code = 301
        resp1.headers = {"Location": "https://www.example.com"}

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.headers = {"Content-Type": "text/html"}
        resp2.iter_content.return_value = [b"<html><head><title>Example Domain</title></head><body><h1>Example Domain</h1></body></html>"]
        resp2.url = "https://www.example.com"

        with patch("socket.getaddrinfo") as mock_dns, patch("requests.Session.get", side_effect=[resp1, resp2]):
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

            result = scrape_website("http://example.com")
            assert result["success"] is True
            assert result["title"] == "Example Domain"


# ======================================================
# LEGITIMATE SCRAPING BEHAVIOR
# ======================================================

class TestLegitimateScraping:
    """Verify that valid public website scraping produces expected metadata."""

    def test_public_website_scraping_success(self):
        html_body = b"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Acme Web Solutions</title>
            <meta name="description" content="Leading web design agency in London." />
        </head>
        <body>
            <h1>Acme Web Solutions</h1>
            <p>Contact us at contact@acmesolutions.co.uk or support@acmesolutions.co.uk</p>
            <a href="https://facebook.com/acmeweb">Facebook</a>
            <a href="https://instagram.com/acmeweb">Instagram</a>
            <a href="https://linkedin.com/company/acmeweb">LinkedIn</a>
        </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.iter_content.return_value = [html_body]
        mock_resp.url = "https://acmesolutions.co.uk"

        with patch("socket.getaddrinfo") as mock_dns, patch("requests.Session.get", return_value=mock_resp):
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.21.55.12", 443))]

            result = scrape_website("acmesolutions.co.uk")
            assert result["success"] is True
            assert result["title"] == "Acme Web Solutions"
            assert result["meta_description"] == "Leading web design agency in London."
            assert "contact@acmesolutions.co.uk" in result["emails"]
            assert result["facebook"] == "https://facebook.com/acmeweb"
            assert result["instagram"] == "https://instagram.com/acmeweb"
            assert result["linkedin"] == "https://linkedin.com/company/acmeweb"
