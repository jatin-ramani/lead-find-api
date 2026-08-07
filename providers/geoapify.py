import logging

import requests

from config import settings
from services.geocoder import get_coordinates

logger = logging.getLogger(__name__)


def _request(params: dict) -> list:
    """Call the Places API and return its features, raising on any error."""

    if not settings.GEOAPIFY_API_KEY:
        raise RuntimeError(
            "GEOAPIFY_API_KEY is not configured; cannot query Geoapify."
        )

    response = requests.get(
        settings.GEOAPIFY_PLACES_URL,
        params={**params, "apiKey": settings.GEOAPIFY_API_KEY},
        timeout=settings.GEOAPIFY_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise Exception(
            f"Geoapify Error {response.status_code}: {response.text}"
        )

    return response.json().get("features", [])


def search_businesses(city: str, category: str):

    coords = get_coordinates(city)

    if not coords:
        return []

    latitude, longitude = coords

    return _request(
        {
            "categories": category,
            "filter": (
                f"circle:{longitude},{latitude},"
                f"{settings.GEOAPIFY_SEARCH_RADIUS_METRES}"
            ),
            "limit": settings.GEOAPIFY_SEARCH_LIMIT,
        }
    )


def search_businesses_by_location(
    latitude: float,
    longitude: float,
    category: str,
    radius: int = None,
):

    if radius is None:
        radius = settings.GEOAPIFY_SEARCH_RADIUS_METRES

    return _request(
        {
            "categories": category,
            "filter": f"circle:{longitude},{latitude},{radius}",
            "limit": settings.GEOAPIFY_SEARCH_LIMIT,
        }
    )
