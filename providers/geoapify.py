import logging
from typing import List, Optional

import requests

from config import settings
from providers.exceptions import GeoapifyError
from services.geocoder import geocode_city, get_coordinates
from services.taxonomy import normalize_category

logger = logging.getLogger(__name__)

__all__ = [
    "GeoapifyError",
    "search_businesses",
    "search_businesses_by_location",
    "fetch_places_page",
]


def _request(params: dict) -> list:
    """Call the Places API and return its features, raising on any error."""

    if not settings.has_geoapify_key:
        raise GeoapifyError(
            "GEOAPIFY_API_KEY is not configured; cannot query Geoapify."
        )

    try:
        response = requests.get(
            settings.GEOAPIFY_PLACES_URL,
            params={**params, "apiKey": settings.geoapify_api_key},
            timeout=settings.GEOAPIFY_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GeoapifyError(f"Geoapify request failed: {exc}") from exc

    if response.status_code != 200:
        raise GeoapifyError(
            f"Geoapify Error {response.status_code}: {response.text}"
        )

    return response.json().get("features", [])


def fetch_places_page(
    category: str,
    place_id: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    radius: Optional[int] = None,
) -> list:
    """
    Perform ONE request to Geoapify Places API for a single page.

    Prefers `filter=place:{place_id}` when `place_id` is present.
    Falls back to `filter=circle:{lon},{lat},{radius}` when coordinates are given.
    """
    if limit is None:
        limit = settings.GEOAPIFY_SEARCH_LIMIT

    normalized_cat = normalize_category(category)

    params = {
        "categories": normalized_cat,
        "limit": limit,
        "offset": offset,
    }

    if place_id:
        params["filter"] = f"place:{place_id}"
    elif latitude is not None and longitude is not None:
        if radius is None:
            radius = settings.GEOAPIFY_SEARCH_RADIUS_METRES
        params["filter"] = f"circle:{longitude},{latitude},{radius}"
    else:
        raise ValueError("Either place_id or latitude/longitude must be provided.")

    return _request(params)


def search_businesses(
    city: str,
    category: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list:
    """
    Fetch one page of businesses for a city.
    Geocodes city to extract place_id & coordinates, then fetches page.
    """
    geo_result = geocode_city(city)

    if not geo_result:
        return []

    lat, lon, place_id = geo_result

    return fetch_places_page(
        category=category,
        place_id=place_id,
        latitude=lat,
        longitude=lon,
        limit=limit,
        offset=offset,
    )


def search_businesses_by_location(
    latitude: float,
    longitude: float,
    category: str,
    radius: Optional[int] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list:
    """Fetch one page of businesses by location circle."""
    return fetch_places_page(
        category=category,
        latitude=latitude,
        longitude=longitude,
        limit=limit,
        offset=offset,
        radius=radius,
    )
