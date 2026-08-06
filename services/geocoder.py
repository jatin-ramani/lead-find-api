import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEOAPIFY_API_KEY")


def get_coordinates(city):
    url = "https://api.geoapify.com/v1/geocode/search"

    params = {
        "text": city,
        "limit": 1,
        "apiKey": API_KEY
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        print("Error:", response.status_code)
        return None

    data = response.json()

    if not data["features"]:
        return None

    coordinates = data["features"][0]["geometry"]["coordinates"]

    longitude = coordinates[0]
    latitude = coordinates[1]

    return latitude, longitude


if __name__ == "__main__":
    coords = get_coordinates("Ahmedabad")

    if coords:
        print("Latitude :", coords[0])
        print("Longitude:", coords[1])
    else:
        print("City not found")