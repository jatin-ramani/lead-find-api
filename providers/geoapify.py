import os
import requests
from dotenv import load_dotenv

from services.geocoder import get_coordinates

load_dotenv()

API_KEY = os.getenv("GEOAPIFY_API_KEY")


def search_businesses(city: str, category: str):

    coords = get_coordinates(city)

    if not coords:
        return []

    latitude, longitude = coords

    url = "https://api.geoapify.com/v2/places"

    params = {
        "categories": category,
        "filter": f"circle:{longitude},{latitude},5000",
        "limit": 20,
        "apiKey": API_KEY,
    }

    response = requests.get(
    url,
    params=params,
    timeout=30,
    )

    if response.status_code != 200:

        raise Exception(
            f"Geoapify Error {response.status_code}: {response.text}"
        )

    data = response.json()

    return data.get("features", [])

def search_businesses_by_location(
    latitude: float,
    longitude: float,
    category: str,
    radius: int = 500,
):

    url = "https://api.geoapify.com/v2/places"

    params = {
        "categories": category,
        "filter": f"circle:{longitude},{latitude},{radius}",
        "limit": 20,
        "apiKey": API_KEY,
    }

    response = requests.get(
        url,
        params=params,
        timeout=30,
    )

    if response.status_code != 200:
        raise Exception(
            f"Geoapify Error {response.status_code}: {response.text}"
        )

    data = response.json()

    return data.get("features", [])