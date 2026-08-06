import os
import requests
from dotenv import load_dotenv

from database.db import SessionLocal
from database.crud import save_business
from services.geocoder import get_coordinates

load_dotenv()

API_KEY = os.getenv("GEOAPIFY_API_KEY")


def search_business(city, category):

    coords = get_coordinates(city)

    if not coords:
        print("City not found")
        return

    latitude, longitude = coords

    url = "https://api.geoapify.com/v2/places"

    params = {
        "categories": category,
        "filter": f"circle:{longitude},{latitude},5000",
        "limit": 20,
        "apiKey": API_KEY,
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        print("Error:", response.status_code)
        return

    data = response.json()

    db = SessionLocal()

    added = 0

    for business in data.get("features", []):

        p = business.get("properties", {})
        contact = p.get("contact", {})

        website = p.get("website")

        status = "No Website" if not website else "Has Website"

        saved = save_business(
            db=db,
            name=p.get("name"),
            phone=contact.get("phone"),
            email=contact.get("email"),
            website=website,
            city=city,
            category=category,
            address=p.get("formatted"),
            status=status,
            place_id=p.get("place_id"),
        )

        if saved:
            added += 1

    db.close()

    print(f"\n✅ {added} businesses saved.")


if __name__ == "__main__":
    search_business(
        city="Ahmedabad",
        category="commercial"
    )