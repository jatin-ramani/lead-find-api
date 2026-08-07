import logging
from typing import Optional, Tuple

import requests

from config import settings

logger = logging.getLogger(__name__)


def get_coordinates(city: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a city name to (latitude, longitude), or None if it cannot be
    resolved.
    """

    if not settings.GEOAPIFY_API_KEY:
        raise RuntimeError(
            "GEOAPIFY_API_KEY is not configured; cannot geocode."
        )

    try:
        response = requests.get(
            settings.GEOAPIFY_GEOCODE_URL,
            params={
                "text": city,
                "limit": 1,
                "apiKey": settings.GEOAPIFY_API_KEY,
            },
            # Without a timeout a hung connection pins the worker thread for
            # the lifetime of the process.
            timeout=settings.GEOAPIFY_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.exception("Geocoding request failed for %r", city)
        return None

    if response.status_code != 200:
        logger.error(
            "Geocoding failed for %r: HTTP %s", city, response.status_code
        )
        return None

    # `.get` rather than `[...]`: a payload without "features" should return
    # no match, not raise KeyError inside a background job.
    features = response.json().get("features") or []

    if not features:
        logger.info("No geocoding match for %r", city)
        return None

    longitude, latitude = features[0]["geometry"]["coordinates"][:2]

    return latitude, longitude
