import json
import logging
import os
import sys

# Add backend directory to path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from config import settings
from providers.geoapify import fetch_places_page, search_businesses
from services.geocoder import geocode_city

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("category_audit")

CATEGORIES_TO_TEST = [
    # 1. Healthcare
    ("healthcare", ["healthcare.dentist", "healthcare.clinic", "healthcare.pharmacy", "healthcare.hospital"]),
    # 2. Catering / Food
    ("catering", ["catering.cafe", "catering.restaurant", "catering.bakery", "catering.fast_food"]),
    # 3. Service
    ("service", ["service.beauty.salon", "service.beauty.hairdresser", "service.vehicle.car_repair", "service.vehicle.car_wash"]),
    # 4. Education
    ("education", ["education.school", "education.college", "education.music_school", "education.driving_school"]),
    # 5. Commercial
    ("commercial", ["commercial.clothing", "commercial.jewelry", "commercial.electronics", "commercial.supermarket"]),
    # 6. Accommodation
    ("accommodation", ["accommodation.hotel", "accommodation.hostel", "accommodation.guest_house", "accommodation.motel"]),
    # 7. Activity
    ("activity", ["activity.fitness_center", "activity.sport_club", "activity.cinema", "activity.community_center"]),
]

def audit_categories(city="Ahmedabad"):
    print("=" * 80)
    print(f"REAL-WORLD CATEGORY ACCURACY & TAXONOMY AUDIT FOR CITY: {city}")
    print(f"Geoapify Key Configured: {settings.has_geoapify_key}")
    print("=" * 80)

    if not settings.has_geoapify_key:
        print("ERROR: GEOAPIFY_API_KEY is not configured in backend settings.")
        return

    geo_res = geocode_city(city)
    print(f"Geocoding '{city}': {geo_res}")
    if not geo_res:
        print(f"ERROR: Could not geocode city '{city}'.")
        return

    lat, lon, place_id = geo_res
    print(f"Coordinates: lat={lat}, lon={lon}, place_id={place_id}")

    audit_results = []

    for broad_cat, subcats in CATEGORIES_TO_TEST:
        print(f"\n" + "-" * 70)
        print(f"AUDITING CATEGORY: {broad_cat.upper()}")
        print("-" * 70)

        # 1. Test broad category query
        broad_features = search_businesses(city=city, category=broad_cat, limit=10)
        print(f"  [Broad Category '{broad_cat}'] -> Returned {len(broad_features)} places")
        
        sample_names = []
        sample_cats = []
        has_phone_count = 0
        has_website_count = 0
        has_email_count = 0
        unrelated_count = 0

        for f in broad_features[:5]:
            props = f.get("properties", {})
            cat_list = props.get("categories", [])
            name = props.get("name") or props.get("formatted") or "Unnamed"
            contact = props.get("contact", {})
            website = props.get("website")
            phone = contact.get("phone")
            email = contact.get("email")

            if phone: has_phone_count += 1
            if website: has_website_count += 1
            if email: has_email_count += 1

            sample_names.append(name)
            sample_cats.append(cat_list)
            print(f"    - Place: '{name}'")
            print(f"      Categories: {cat_list}")
            print(f"      Website: {website} | Phone: {phone} | Email: {email}")

        # 2. Test subcategories
        subcat_results = {}
        for subcat in subcats:
            sub_features = fetch_places_page(
                category=subcat,
                place_id=place_id,
                latitude=lat,
                longitude=lon,
                limit=5
            )
            subcat_results[subcat] = len(sub_features)
            print(f"    * Subcategory '{subcat}' -> Returned {len(sub_features)} places")
            if sub_features:
                first_props = sub_features[0].get("properties", {})
                first_name = first_props.get("name") or first_props.get("formatted")
                first_cats = first_props.get("categories", [])
                print(f"      Sample 1st result: '{first_name}' (Cats: {first_cats})")

        audit_results.append({
            "category": broad_cat,
            "broad_count": len(broad_features),
            "sample_names": sample_names,
            "has_phone": has_phone_count,
            "has_website": has_website_count,
            "has_email": has_email_count,
            "subcategories_supported": subcat_results,
        })

    print("\n" + "=" * 80)
    print("AUDIT SUMMARY MATRIX")
    print("=" * 80)
    for res in audit_results:
        print(f"Category: {res['category']}")
        print(f"  Broad Results: {res['broad_count']}")
        print(f"  Subcategories Supported by Geoapify: {res['subcategories_supported']}")
        print(f"  Contact Info Captured: Phone={res['has_phone']}, Website={res['has_website']}, Email={res['has_email']}")
        print("-" * 50)

if __name__ == "__main__":
    audit_categories()
