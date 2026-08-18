"""Website scraper and the Geoapify provider. No test touches the network."""

import time

import pytest
import requests

from services import website_scraper
from services.website_scraper import (
    _is_valid_email,
    _iter_text_emails,
    _normalise_url,
    scrape_website,
)
from tests.fakes import FakeResponse, RecordingGet, html_response


@pytest.fixture
def fake_get(monkeypatch):
    """Replace requests.get inside the scraper with a recorder."""

    def _install(*responses):
        recorder = RecordingGet(*responses)
        monkeypatch.setattr(website_scraper.requests, "get", recorder)
        return recorder

    return _install


class TestUrlNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", None),
            ("   ", None),
            (None, None),
            ("asopalav.test", "https://asopalav.test"),
            ("www.asopalav.test", "https://www.asopalav.test"),
            ("http://a.test", "http://a.test"),
            ("ftp://a.test", None),
            ("javascript:alert(1)", None),
            ("localhost", None),
            ("not a url", None),
        ],
    )
    def test_normalisation(self, raw, expected):
        assert _normalise_url(raw) == expected

    def test_empty_url_returns_error_without_network(self, fake_get):
        fake_get()  # queue nothing: any request would raise

        result = scrape_website("")

        assert result["success"] is False
        assert "valid http(s) URL" in result["error"]


class TestSuccessfulScrape:
    def test_extracts_everything(self, fake_get, scraped_html):
        fake_get(html_response(scraped_html))

        result = scrape_website("https://asopalav.test")

        assert result["success"] is True
        assert result["title"] == "Asopalav — Ethnic Wear"
        assert result["meta_description"] == "Designer sarees in Ahmedabad."
        assert result["facebook"] == "https://www.facebook.com/asopalav"
        assert result["instagram"] == "https://instagram.com/asopalav/"
        assert result["linkedin"] == "https://in.linkedin.com/company/asopalav"
        assert result["twitter"] == "https://x.com/asopalav"
        assert result["youtube"] == "https://youtu.be/abc123"
        assert result["whatsapp"] == "https://wa.me/919876543210"

    def test_response_shape_is_stable(self, fake_get, scraped_html):
        fake_get(html_response(scraped_html))

        assert set(scrape_website("https://a.test")) == {
            "success", "title", "meta_description", "emails",
            "facebook", "instagram", "linkedin", "twitter", "youtube",
            "whatsapp",
        }

    def test_browser_user_agent_and_timeout_are_sent(self, fake_get, scraped_html):
        recorder = fake_get(html_response(scraped_html))

        scrape_website("https://a.test")

        call = recorder.calls[0]

        assert "Mozilla" in call["headers"]["User-Agent"]
        assert call["timeout"] == website_scraper.REQUEST_TIMEOUT
        assert call["allow_redirects"] is False

    def test_missing_socials_are_none_and_emails_a_list(self, fake_get):
        fake_get(html_response("<html><head><title>Bare</title></head><body/></html>"))

        result = scrape_website("https://a.test")

        assert result["emails"] == []
        assert result["facebook"] is None

    def test_title_falls_back_to_og_then_h1(self, fake_get):
        fake_get(html_response(
            '<html><head><meta property="og:title" content="OG Title">'
            '<meta property="og:description" content="OG desc"></head>'
            "<body><h1>H1</h1></body></html>"
        ))
        result = scrape_website("https://a.test")
        assert result["title"] == "OG Title"
        assert result["meta_description"] == "OG desc"

        fake_get(html_response("<html><body><h1>  Just  An  H1  </h1></body></html>"))
        assert scrape_website("https://a.test")["title"] == "Just An H1"

    def test_utf8_is_decoded_correctly(self, fake_get):
        fake_get(html_response(
            '<html><head><meta charset="utf-8"><title>Café Münster</title>'
            "</head><body/></html>"
        ))

        assert scrape_website("https://a.test")["title"] == "Café Münster"


class TestEmailExtraction:
    def test_dedupes_case_insensitively_and_strips_mailto_params(
        self, fake_get, scraped_html
    ):
        fake_get(html_response(scraped_html))

        emails = [e.lower() for e in scrape_website("https://a.test")["emails"]]

        assert emails.count("sales@asopalav.test") == 1
        assert "support@asopalav.test" in emails

    def test_rejects_assets_placeholders_and_script_contents(
        self, fake_get, scraped_html
    ):
        fake_get(html_response(scraped_html))

        emails = [e.lower() for e in scrape_website("https://a.test")["emails"]]

        assert not any("2x.png" in e for e in emails)
        assert not any("example.com" in e for e in emails)
        assert "fake@script.com" not in emails

    @pytest.mark.parametrize(
        "email,valid",
        [
            ("a@b.test", True),
            ("first.last+tag@sub.domain.co.uk", True),
            ("logo@2x.png", False),
            ("user@example.com", False),
            ("x@o1.ingest.sentry.io", False),
            ("dbl..dot@a.test", False),
            (".lead@a.test", False),
            ("no-at-sign", False),
            ("two@at@signs.test", False),
        ],
    )
    def test_validity_rules(self, email, valid):
        assert _is_valid_email(email) is valid

    def test_extraction_is_linear_not_quadratic(self):
        """
        Regression guard for a ReDoS: the email regex used to backtrack from
        every start position, so a long run of characters with no "@" hung the
        worker. Must stay comfortably fast on a large page.
        """

        payload = "x" * 2_000_000

        started = time.perf_counter()
        list(_iter_text_emails(payload))
        elapsed = time.perf_counter() - started

        assert elapsed < 2.0, f"email extraction took {elapsed:.1f}s — quadratic again?"

    def test_still_finds_addresses_in_large_documents(self):
        payload = ("filler " * 50_000) + " reachme@needle.test"

        assert "reachme@needle.test" in list(_iter_text_emails(payload))


class TestSocialRules:
    def test_share_widgets_and_lookalikes_are_excluded(self, fake_get):
        fake_get(html_response(
            '<html><body>'
            '<a href="https://facebook.com/sharer/sharer.php?u=x">share</a>'
            '<a href="https://notfacebook.test/evil">lookalike</a>'
            '<a href="https://twitter.com/intent/tweet">tweet</a>'
            "</body></html>"
        ))

        result = scrape_website("https://a.test")

        assert result["facebook"] is None
        assert result["twitter"] is None

    def test_whatsapp_app_scheme(self, fake_get):
        fake_get(html_response(
            '<html><body><a href="whatsapp://send?phone=919999999999">wa</a></body></html>'
        ))

        assert scrape_website("https://a.test")["whatsapp"] == (
            "whatsapp://send?phone=919999999999"
        )


class TestFailureHandling:
    """Every failure must come back as a dict, never as an exception."""

    @pytest.mark.parametrize(
        "response,fragment",
        [
            (FakeResponse(status_code=404, reason="Not Found"), "HTTP 404"),
            (FakeResponse(status_code=500, reason="Server Error"), "HTTP 500"),
            (
                FakeResponse(headers={"Content-Type": "application/pdf"},
                             content=b"%PDF-1.4"),
                "Unsupported content type",
            ),
            (FakeResponse(content=b""), "empty response"),
        ],
    )
    def test_bad_responses(self, fake_get, response, fragment):
        fake_get(response)

        result = scrape_website("https://a.test")

        assert result["success"] is False
        assert fragment.lower() in result["error"].lower()

    @pytest.mark.parametrize(
        "exception,fragment",
        [
            (requests.exceptions.Timeout(), "timed out"),
            (requests.exceptions.ConnectionError(), "Could not connect"),
            (requests.exceptions.TooManyRedirects(), "Too many redirects"),
            (requests.exceptions.SSLError("bad cert"), "SSL error"),
            (requests.exceptions.RequestException("boom"), "Request failed"),
        ],
    )
    def test_network_exceptions_become_error_dicts(self, fake_get, exception, fragment):
        fake_get(exception)

        result = scrape_website("https://a.test")

        assert result["success"] is False
        assert fragment.lower() in result["error"].lower()
        assert "title" not in result

    def test_oversized_response_is_capped(self, fake_get, monkeypatch):
        monkeypatch.setattr(website_scraper, "MAX_RESPONSE_BYTES", 1024)

        huge = "<html><body>" + ("x" * 50_000) + "</body></html>"
        fake_get(html_response(huge))

        result = scrape_website("https://a.test")

        assert result["success"] is True  # truncated, not failed

    def test_response_is_always_closed(self, fake_get, scraped_html):
        response = html_response(scraped_html)
        fake_get(response)

        scrape_website("https://a.test")

        assert response.closed is True, "connection leaked"


class TestGeoapifyProvider:
    """The provider must never be reached without mocking."""

    @pytest.fixture
    def fake_http(self, monkeypatch):
        """
        One recorder for both modules.

        `providers.geoapify.requests` and `services.geocoder.requests` are the
        same module object, so patching them separately would have the second
        patch silently replace the first. Responses are queued in call order:
        geocode first, then places.
        """

        def _install(*responses):
            recorder = RecordingGet(*responses)
            monkeypatch.setattr(requests, "get", recorder)
            return recorder

        return _install

    def test_search_returns_features(self, fake_http):
        from providers.geoapify import search_businesses
        from tests.fakes import geoapify_feature, geocode_payload, places_payload

        fake_http(
            FakeResponse(json_data=geocode_payload()),
            FakeResponse(json_data=places_payload([geoapify_feature()])),
        )

        features = search_businesses("Ahmedabad", "commercial")

        assert len(features) == 1
        assert features[0]["properties"]["name"] == "Asopalav"

    def test_unresolvable_city_short_circuits(self, fake_http):
        from providers.geoapify import search_businesses

        # Only the geocode response is queued; a Places call would raise.
        recorder = fake_http(FakeResponse(json_data={"features": []}))

        assert search_businesses("Nowhere", "commercial") == []
        assert recorder.call_count == 1, "Places API called despite no coordinates"

    def test_places_error_raises(self, fake_http):
        """
        The type matters, not just the message: callers branch on
        GeoapifyError to tell an upstream outage from a bug of their own.
        """

        from providers.geoapify import GeoapifyError, search_businesses
        from tests.fakes import geocode_payload

        fake_http(
            FakeResponse(json_data=geocode_payload()),
            FakeResponse(
                status_code=400,
                content=b'{"message":"Category not supported"}',
            ),
        )

        with pytest.raises(GeoapifyError, match="Geoapify Error 400"):
            search_businesses("Ahmedabad", "not-a-category")

    def test_places_network_failure_raises_geoapify_error(self, fake_http):
        """A timeout used to escape as a bare requests exception."""

        from providers.geoapify import GeoapifyError, search_businesses
        from tests.fakes import geocode_payload

        fake_http(
            FakeResponse(json_data=geocode_payload()),
            requests.exceptions.ConnectTimeout("timed out"),
        )

        with pytest.raises(GeoapifyError, match="request failed"):
            search_businesses("Ahmedabad", "commercial")

    def test_missing_api_key_raises_geoapify_error(self, monkeypatch):
        from config import settings
        from providers.geoapify import GeoapifyError, search_businesses_by_location

        monkeypatch.setattr(settings, "GEOAPIFY_API_KEY", None)

        with pytest.raises(GeoapifyError, match="not configured"):
            search_businesses_by_location(23.0, 72.5, "commercial")

    def test_configured_limit_and_radius_are_used(self, fake_http):
        from config import settings
        from providers.geoapify import search_businesses
        from tests.fakes import geocode_payload, places_payload

        recorder = fake_http(
            FakeResponse(json_data=geocode_payload()),
            FakeResponse(json_data=places_payload()),
        )

        search_businesses("Ahmedabad", "commercial")

        params = recorder.calls[1]["params"]   # [0] is the geocode call

        assert params["limit"] == settings.GEOAPIFY_SEARCH_LIMIT
        assert str(settings.GEOAPIFY_SEARCH_RADIUS_METRES) in params["filter"]
        assert params["apiKey"] == settings.geoapify_api_key
        assert isinstance(params["apiKey"], str), (
            "a SecretStr would be sent to Geoapify as the literal "
            "'**********' and every call would 401"
        )

    def test_geocoder_timeout_is_set(self, fake_http):
        from config import settings
        from services.geocoder import get_coordinates
        from tests.fakes import geocode_payload

        recorder = fake_http(FakeResponse(json_data=geocode_payload()))

        get_coordinates("Ahmedabad")

        assert recorder.calls[0]["timeout"] == settings.GEOAPIFY_TIMEOUT_SECONDS

    def test_geocoder_survives_a_malformed_payload(self, fake_http):
        from services.geocoder import get_coordinates

        fake_http(FakeResponse(json_data={"unexpected": "shape"}))

        assert get_coordinates("Ahmedabad") is None

    def test_geocoder_no_match_returns_none(self, fake_http):
        """
        A place Geoapify has never heard of is an answer, not a failure — the
        one case that still returns None.
        """

        from services.geocoder import get_coordinates

        fake_http(FakeResponse(json_data={"features": []}))

        assert get_coordinates("Nowhere-at-all") is None

    def test_geocoder_network_failure_raises(self, fake_http):
        from providers.exceptions import GeoapifyError
        from services.geocoder import get_coordinates

        fake_http(requests.exceptions.ConnectionError())

        with pytest.raises(GeoapifyError, match="Geocoding request failed"):
            get_coordinates("Ahmedabad")

    def test_geocoder_http_error_raises_rather_than_looking_like_no_match(
        self, fake_http
    ):
        """
        The bug this exists for: a revoked key answers 401, and returning None
        made the scan finish "successfully" with zero businesses.
        """

        from providers.exceptions import GeoapifyError
        from services.geocoder import get_coordinates

        fake_http(FakeResponse(status_code=401, content=b"Invalid apiKey"))

        with pytest.raises(GeoapifyError, match="401"):
            get_coordinates("Ahmedabad")
