"""Test doubles for outbound HTTP, so no test ever touches the network."""

from typing import Any, Dict, List, Optional

import requests


class FakeResponse:
    """Enough of `requests.Response` for the code under test."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: Optional[Any] = None,
        content: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
        url: str = "https://example.test/",
        reason: str = "OK",
    ):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = content.decode("utf-8", "replace") if content else ""
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.url = url
        self.reason = reason
        self.closed = False

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def iter_content(self, chunk_size: int = 8192):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self) -> None:
        self.closed = True


def html_response(html: str, **kwargs) -> FakeResponse:
    return FakeResponse(content=html.encode("utf-8"), **kwargs)


def geoapify_feature(
    name: str = "Asopalav",
    place_id: str = "place-1",
    website: Optional[str] = "https://asopalav.test",
    phone: Optional[str] = "+91 79 2676 5592",
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """One feature shaped like a real Geoapify Places result."""

    contact: Dict[str, str] = {}

    if phone:
        contact["phone"] = phone
    if email:
        contact["email"] = email

    properties: Dict[str, Any] = {
        "name": name,
        "place_id": place_id,
        "formatted": f"{name}, Ring Road, Ahmedabad, India",
    }

    if website:
        properties["website"] = website
    if contact:
        properties["contact"] = contact

    return {"type": "Feature", "properties": properties}


def geocode_payload(latitude: float = 23.0225, longitude: float = 72.5714):
    return {
        "features": [
            {"geometry": {"coordinates": [longitude, latitude]}}
        ]
    }


def places_payload(features: Optional[List[Dict[str, Any]]] = None):
    return {"features": features if features is not None else []}


class RecordingGet:
    """
    Stand-in for `requests.get` that records calls and replays queued responses.

    Raising `requests.RequestException` subclasses is supported by queueing the
    exception instance instead of a response.
    """

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})

        if not self.queue:
            raise AssertionError(f"unexpected extra request to {url}")

        item = self.queue.pop(0)

        if isinstance(item, Exception):
            raise item

        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)


__all__ = [
    "FakeResponse",
    "RecordingGet",
    "geoapify_feature",
    "geocode_payload",
    "html_response",
    "places_payload",
    "requests",
]
