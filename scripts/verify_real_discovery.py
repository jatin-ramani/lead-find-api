import json
import logging
import os
import sys

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from config import settings
from providers.geoapify import search_businesses
from services.taxonomy import normalize_category

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TEST_CASES = [
    # (User input, Expected Geoapify category, Expected place keyword/concept)
    ("Dental Clinics", "healthcare.dentist", "Dental / Teeth / Clinic"),
    ("Clinics", "healthcare.clinic_or_praxis", "Clinic / Doctor / Medical"),
    ("Pharmacies", "healthcare.pharmacy", "Pharmacy / Chemist / Medical"),
    ("Cafes", "catering.cafe", "Cafe / Coffee / Ice Cream"),
    ("Restaurants", "catering.restaurant", "Restaurant / Dining"),
    ("Bakeries", "commercial.food_and_drink.bakery", "Bakery / Cake / Sweets"),
    ("Salons", "service.beauty.hairdresser", "Salon / Hairdresser / Beauty"),
    ("Auto Repair", "service.vehicle.repair", "Garage / Repair / Mechanic"),
    ("Electronics", "commercial.elektronics", "Electronics / Electrical / Mobile"),
    ("Jewellery", "commercial.jewelry", "Jewellery / Jewelers / Gold"),
    ("Clothing", "commercial.clothing", "Clothing / Boutique / Apparel"),
    ("Gyms", "sport.fitness", "Gym / Fitness / Health"),
    ("Cinemas", "entertainment.cinema", "Cinema / Theatre / Movie"),
]

def run_real_discovery_verification(city="Ahmedabad"):
    print("=" * 80)
    print(f"VERIFYING REAL-WORLD BUSINESS DISCOVERY ACCURACY IN '{city.upper()}'")
    print("=" * 80)

    results = []
    all_passed = True

    for user_input, expected_cat, concept in TEST_CASES:
        norm_cat = normalize_category(user_input)
        assert norm_cat == expected_cat, f"Normalization mismatch for {user_input}: got {norm_cat}, expected {expected_cat}"

        print(f"\n[TEST] User Query: '{user_input}' -> Normalized Key: '{norm_cat}'")
        places = search_businesses(city=city, category=norm_cat, limit=5)

        if not places:
            print(f"  WARNING: No places returned for '{user_input}' in {city}")
            results.append({
                "query": user_input,
                "normalized": norm_cat,
                "count": 0,
                "places": [],
                "status": "PASS_ZERO_RESULTS"
            })
            continue

        place_names = []
        for p in places:
            props = p.get("properties", {})
            name = props.get("name") or props.get("formatted") or "Unnamed"
            place_names.append(name)
            categories = props.get("categories", [])
            print(f"  - Place: '{name}' | Categories: {categories}")

        print(f"  Result: {len(places)} places found. (Top: '{place_names[0]}')")
        results.append({
            "query": user_input,
            "normalized": norm_cat,
            "count": len(places),
            "places": place_names,
            "status": "PASS"
        })

    print("\n" + "=" * 80)
    print("REAL-WORLD DISCOVERY VERIFICATION SUMMARY")
    print("=" * 80)
    for r in results:
        print(f"Query: '{r['query']}' -> Key: '{r['normalized']}' -> Found: {r['count']} -> Status: {r['status']}")

    with open("real_discovery_verification.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("Saved results to real_discovery_verification.json")

if __name__ == "__main__":
    run_real_discovery_verification()
