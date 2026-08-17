import logging
from typing import Optional, Tuple

import requests

from config import settings
from providers.exceptions import GeoapifyError

logger = logging.getLogger(__name__)


def geocode_city(city: str) -> Optional[Tuple[float, float, Optional[str]]]:
    """
    Resolve a city name to (latitude, longitude, place_id).

    Returns None when Geoapify answered normally but knows no such place —
    that is an answer, not a failure. Raises `GeoapifyError` when it could not
    be asked at all, so a broken key or an outage cannot be mistaken for a
    city that does not exist.
    """

    if not settings.has_geoapify_key:
        raise GeoapifyError(
            "GEOAPIFY_API_KEY is not configured; cannot geocode."
        )

    try:
        response = requests.get(
            settings.GEOAPIFY_GEOCODE_URL,
            params={
                "text": city,
                "limit": 1,
                "apiKey": settings.geoapify_api_key,
            },
            timeout=settings.GEOAPIFY_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Geocoding request failed for %r: %s", city, exc)

        raise GeoapifyError(f"Geocoding request failed: {exc}") from exc

    if response.status_code != 200:
        logger.error(
            "Geocoding failed for %r: HTTP %s", city, response.status_code
        )

        raise GeoapifyError(
            f"Geoapify Error {response.status_code} while geocoding {city!r}"
        )

    features = response.json().get("features") or []

    if not features:
        logger.info("No geocoding match for %r", city)
        return None

    feature = features[0]
    longitude, latitude = feature["geometry"]["coordinates"][:2]
    place_id = feature.get("properties", {}).get("place_id")

    return latitude, longitude, place_id


def get_coordinates(city: str) -> Optional[Tuple[float, float]]:
    """Legacy helper returning (latitude, longitude)."""
    result = geocode_city(city)
    if result is None:
        return None
    return result[0], result[1]
