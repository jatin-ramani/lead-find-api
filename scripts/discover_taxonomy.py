import os, sys, requests, json

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)
from config import settings

candidates = [
    # Bakery
    "commercial.food_and_drink.bakery",
    "commercial.bakery",
    # Beauty / Salon
    "service.beauty",
    "service.beauty.hairdresser",
    "service.beauty.spa",
    "commercial.health_and_beauty",
    # Car repair
    "service.vehicle.repair",
    "service.vehicle.repair.car",
    "service.vehicle.repair.motorcycle",
    # Electronics
    "commercial.elektronics",
    "commercial.houseware_and_hardware",
    "commercial.telecommunication",
    "commercial.computer",
    # Jewellery
    "commercial.jewelry",
    "commercial.jewellery",
    # Activity / Sports / Gym
    "sport.fitness",
    "sport.fitness.fitness_centre",
    "sport",
    "activity.sport_club",
    "entertainment.cinema",
    "entertainment.culture.theatre",
    "entertainment.theme_park",
    # Education
    "education.coaching",
    "education.driving_school",
    "education.language_school",
]

res = {}
for c in candidates:
    r = requests.get(
        settings.GEOAPIFY_PLACES_URL,
        params={
            "categories": c,
            "filter": "circle:72.5800568,23.0215374,5000",
            "limit": 3,
            "apiKey": settings.geoapify_api_key
        }
    )
    if r.status_code == 200:
        data = r.json().get("features", [])
        names = [f.get("properties", {}).get("name") or f.get("properties", {}).get("formatted") for f in data]
        res[c] = {"status": "SUPPORTED", "count": len(data), "samples": names}
        print(f"[VALID] {c}: count={len(data)}, samples={names}")
    else:
        err = r.json().get("message", r.text)
        res[c] = {"status": "INVALID", "error": err}
        print(f"[INVALID] {c}: {err}")

with open("geoapify_taxonomy_discovery.json", "w", encoding="utf-8") as f:
    json.dump(res, f, indent=2)
