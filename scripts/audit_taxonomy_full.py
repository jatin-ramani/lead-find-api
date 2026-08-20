import json
import logging
import os
import sys
import time

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from config import settings
import requests
from providers.geoapify import fetch_places_page, search_businesses
from services.geocoder import geocode_city

# Candidate Geoapify subcategories to audit
TEST_TAXONOMY = {
    "healthcare": [
        "healthcare",
        "healthcare.dentist",
        "healthcare.clinic_or_praxis",
        "healthcare.clinic_or_praxis.general",
        "healthcare.pharmacy",
        "healthcare.hospital",
    ],
    "catering": [
        "catering",
        "catering.restaurant",
        "catering.fast_food",
        "catering.cafe",
        "catering.bakery",
        "catering.bar",
        "catering.pub",
    ],
    "service": [
        "service",
        "service.beauty",
        "service.beauty.hairdresser",
        "service.beauty.salon",
        "service.vehicle",
        "service.vehicle.car_repair",
        "service.vehicle.car_wash",
        "service.financial.bank",
        "service.tailor",
        "service.dry_cleaning",
    ],
    "education": [
        "education",
        "education.school",
        "education.college",
        "education.university",
        "education.music_school",
        "education.driving_school",
        "education.library",
    ],
    "commercial": [
        "commercial",
        "commercial.clothing",
        "commercial.jewelry",
        "commercial.electronics",
        "commercial.supermarket",
        "commercial.health_and_beauty",
        "commercial.books",
        "commercial.furniture_and_interior",
    ],
    "accommodation": [
        "accommodation",
        "accommodation.hotel",
        "accommodation.hostel",
        "accommodation.guest_house",
        "accommodation.motel",
        "accommodation.apartment",
    ],
    "activity": [
        "activity",
        "activity.sport_club",
        "activity.fitness_center",
        "activity.cinema",
        "activity.community_center",
        "activity.sports_centre",
    ],
}

def run_taxonomy_audit(city="Ahmedabad"):
    geo_res = geocode_city(city)
    lat, lon, place_id = geo_res

    results = {}
    print(f"Auditing taxonomy for {city} (place_id: {place_id})...\n")

    for broad_cat, subcats in TEST_TAXONOMY.items():
        print(f"=== {broad_cat.upper()} ===")
        results[broad_cat] = []
        for cat in subcats:
            time.sleep(0.2)
            try:
                places = fetch_places_page(
                    category=cat,
                    place_id=place_id,
                    latitude=lat,
                    longitude=lon,
                    limit=5,
                )
                first_name = ""
                first_cats = []
                has_phone = 0
                has_web = 0
                if places:
                    first_p = places[0].get("properties", {})
                    first_name = first_p.get("name") or first_p.get("formatted") or "Unnamed"
                    first_cats = first_p.get("categories", [])
                    has_phone = sum(1 for p in places if p.get("properties", {}).get("contact", {}).get("phone"))
                    has_web = sum(1 for p in places if p.get("properties", {}).get("website"))
                print(f"  [SUPPORTED] '{cat}': count={len(places)}, 1st='{first_name}', phone={has_phone}/5, web={has_web}/5, cats={first_cats}")
                results[broad_cat].append({
                    "category": cat,
                    "supported": True,
                    "count": len(places),
                    "first_place": first_name,
                    "first_cats": first_cats,
                    "has_phone": has_phone,
                    "has_web": has_web,
                })
            except Exception as e:
                err_msg = str(e)
                print(f"  [UNSUPPORTED/ERROR] '{cat}': {err_msg}")
                results[broad_cat].append({
                    "category": cat,
                    "supported": False,
                    "error": err_msg,
                })
        print()

    # Save results to a json file
    with open("category_taxonomy_audit_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("Results saved to category_taxonomy_audit_results.json")

if __name__ == "__main__":
    run_taxonomy_audit()
