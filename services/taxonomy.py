"""
Category taxonomy and alias normalization for Geoapify Places API.

Maps natural user queries, common business subcategory terms, and known taxonomy
variations to verified Geoapify category identifiers.
"""

from typing import Dict, List, Optional, Tuple
import re

# Comprehensive alias lookup mapping user input (lowercased, normalized) -> Geoapify category key
CATEGORY_ALIASES: Dict[str, str] = {
    # -------------------------------------------------------------------------
    # 1. HEALTHCARE
    # -------------------------------------------------------------------------
    "healthcare": "healthcare",
    "health": "healthcare",
    "dentist": "healthcare.dentist",
    "dentists": "healthcare.dentist",
    "dental": "healthcare.dentist",
    "dental clinic": "healthcare.dentist",
    "dental clinics": "healthcare.dentist",
    "orthodontist": "healthcare.dentist",
    "orthodontists": "healthcare.dentist",
    "healthcare.clinic": "healthcare.clinic_or_praxis",
    "clinic": "healthcare.clinic_or_praxis",
    "clinics": "healthcare.clinic_or_praxis",
    "doctor": "healthcare.clinic_or_praxis",
    "doctors": "healthcare.clinic_or_praxis",
    "general clinic": "healthcare.clinic_or_praxis",
    "general clinics": "healthcare.clinic_or_praxis",
    "medical clinic": "healthcare.clinic_or_praxis",
    "medical clinics": "healthcare.clinic_or_praxis",
    "pharmacy": "healthcare.pharmacy",
    "pharmacies": "healthcare.pharmacy",
    "chemist": "healthcare.pharmacy",
    "chemists": "healthcare.pharmacy",
    "drugstore": "healthcare.pharmacy",
    "drugstores": "healthcare.pharmacy",
    "hospital": "healthcare.hospital",
    "hospitals": "healthcare.hospital",
    "veterinarian": "healthcare.veterinary",
    "veterinary": "healthcare.veterinary",
    "vet": "healthcare.veterinary",
    "vets": "healthcare.veterinary",

    # -------------------------------------------------------------------------
    # 2. CATERING / FOOD
    # -------------------------------------------------------------------------
    "catering": "catering",
    "food": "catering",
    "cafe": "catering.cafe",
    "cafes": "catering.cafe",
    "café": "catering.cafe",
    "cafés": "catering.cafe",
    "coffee": "catering.cafe",
    "coffee shop": "catering.cafe",
    "coffee shops": "catering.cafe",
    "restaurant": "catering.restaurant",
    "restaurants": "catering.restaurant",
    "dining": "catering.restaurant",
    "fast food": "catering.fast_food",
    "fastfood": "catering.fast_food",
    "quick bites": "catering.fast_food",
    "bakery": "commercial.food_and_drink.bakery",
    "bakeries": "commercial.food_and_drink.bakery",
    "bake house": "commercial.food_and_drink.bakery",
    "cake shop": "commercial.food_and_drink.bakery",
    "cake shops": "commercial.food_and_drink.bakery",
    "pastry shop": "commercial.food_and_drink.bakery",
    "catering.bakery": "commercial.food_and_drink.bakery",
    "commercial.bakery": "commercial.food_and_drink.bakery",
    "bar": "catering.bar",
    "bars": "catering.bar",
    "pub": "catering.pub",
    "pubs": "catering.pub",
    "ice cream": "catering.cafe.ice_cream",
    "ice cream shop": "catering.cafe.ice_cream",

    # -------------------------------------------------------------------------
    # 3. SERVICE
    # -------------------------------------------------------------------------
    "service": "service",
    "services": "service",
    "salon": "service.beauty.hairdresser",
    "salons": "service.beauty.hairdresser",
    "hair salon": "service.beauty.hairdresser",
    "hair salons": "service.beauty.hairdresser",
    "hairdresser": "service.beauty.hairdresser",
    "hairdressers": "service.beauty.hairdresser",
    "barber": "service.beauty.hairdresser",
    "barbers": "service.beauty.hairdresser",
    "barber shop": "service.beauty.hairdresser",
    "barbershop": "service.beauty.hairdresser",
    "beauty parlour": "service.beauty.hairdresser",
    "beauty parlor": "service.beauty.hairdresser",
    "service.beauty.salon": "service.beauty.hairdresser",
    "spa": "service.beauty.spa",
    "spas": "service.beauty.spa",
    "car repair": "service.vehicle.repair",
    "auto repair": "service.vehicle.repair",
    "car service": "service.vehicle.repair",
    "mechanic": "service.vehicle.repair",
    "mechanics": "service.vehicle.repair",
    "garage": "service.vehicle.repair",
    "garages": "service.vehicle.repair",
    "service.vehicle.car_repair": "service.vehicle.repair",
    "service.car_repair": "service.vehicle.repair",
    "car wash": "service.vehicle.car_wash",
    "carwash": "service.vehicle.car_wash",
    "auto wash": "service.vehicle.car_wash",
    "bank": "service.financial.bank",
    "banks": "service.financial.bank",
    "atm": "service.financial.atm",
    "dry cleaning": "service.cleaning.dry_cleaning",
    "dry cleaner": "service.cleaning.dry_cleaning",
    "dry cleaners": "service.cleaning.dry_cleaning",
    "laundry": "service.cleaning.dry_cleaning",
    "service.dry_cleaning": "service.cleaning.dry_cleaning",
    "tailor": "service.tailor",
    "tailors": "service.tailor",

    # -------------------------------------------------------------------------
    # 4. EDUCATION
    # -------------------------------------------------------------------------
    "education": "education",
    "school": "education.school",
    "schools": "education.school",
    "high school": "education.school",
    "primary school": "education.school",
    "college": "education.college",
    "colleges": "education.college",
    "university": "education.university",
    "universities": "education.university",
    "library": "education.library",
    "libraries": "education.library",
    "coaching": "education.school",
    "tuition": "education.school",
    "training institute": "education.college",
    "driving school": "education.driving_school",
    "driving schools": "education.driving_school",
    "music school": "education.music_school",
    "language school": "education.language_school",

    # -------------------------------------------------------------------------
    # 5. COMMERCIAL / RETAIL
    # -------------------------------------------------------------------------
    "commercial": "commercial",
    "retail": "commercial",
    "shop": "commercial",
    "shops": "commercial",
    "store": "commercial",
    "stores": "commercial",
    "clothing": "commercial.clothing",
    "clothes": "commercial.clothing",
    "clothing store": "commercial.clothing",
    "clothing stores": "commercial.clothing",
    "boutique": "commercial.clothing",
    "boutiques": "commercial.clothing",
    "apparel": "commercial.clothing",
    "supermarket": "commercial.supermarket",
    "supermarkets": "commercial.supermarket",
    "grocery": "commercial.supermarket",
    "groceries": "commercial.supermarket",
    "mall": "commercial.shopping_mall",
    "malls": "commercial.shopping_mall",
    "shopping mall": "commercial.shopping_mall",
    "jewellery": "commercial.jewelry",
    "jewelry": "commercial.jewelry",
    "jewellers": "commercial.jewelry",
    "jewelers": "commercial.jewelry",
    "jewelry store": "commercial.jewelry",
    "jewellery store": "commercial.jewelry",
    "electronics": "commercial.elektronics",
    "electronics store": "commercial.elektronics",
    "electronics shops": "commercial.elektronics",
    "commercial.electronics": "commercial.elektronics",
    "book store": "commercial.books",
    "bookstore": "commercial.books",
    "book stores": "commercial.books",
    "books": "commercial.books",
    "furniture": "commercial.furniture_and_interior",
    "furniture store": "commercial.furniture_and_interior",

    # -------------------------------------------------------------------------
    # 6. ACCOMMODATION
    # -------------------------------------------------------------------------
    "accommodation": "accommodation",
    "hotel": "accommodation.hotel",
    "hotels": "accommodation.hotel",
    "resort": "accommodation.hotel",
    "resorts": "accommodation.hotel",
    "hostel": "accommodation.hostel",
    "hostels": "accommodation.hostel",
    "pg": "accommodation.hostel",
    "guest house": "accommodation.guest_house",
    "guesthouse": "accommodation.guest_house",
    "guest houses": "accommodation.guest_house",
    "guesthouses": "accommodation.guest_house",
    "motel": "accommodation.motel",
    "motels": "accommodation.motel",
    "apartment": "accommodation.apartment",
    "apartments": "accommodation.apartment",

    # -------------------------------------------------------------------------
    # 7. ACTIVITY / ENTERTAINMENT / SPORT
    # -------------------------------------------------------------------------
    "activity": "activity",
    "gym": "sport.fitness",
    "gyms": "sport.fitness",
    "fitness": "sport.fitness",
    "fitness center": "sport.fitness",
    "fitness centre": "sport.fitness",
    "fitness club": "sport.fitness",
    "workout": "sport.fitness",
    "activity.fitness_center": "sport.fitness",
    "sport.fitness_center": "sport.fitness",
    "cinema": "entertainment.cinema",
    "cinemas": "entertainment.cinema",
    "movie theatre": "entertainment.cinema",
    "movie theater": "entertainment.cinema",
    "theatre": "entertainment.cinema",
    "activity.cinema": "entertainment.cinema",
    "sport club": "activity.sport_club",
    "sports club": "activity.sport_club",
    "community center": "activity.community_center",
    "community centre": "activity.community_center",
    "entertainment": "entertainment",
    "leisure": "leisure",
    "office": "office",
}

# Known Geoapify taxonomy structural mismatches for dot-delimited queries
TAXONOMY_FIXES: Dict[str, str] = {
    "healthcare.clinic": "healthcare.clinic_or_praxis",
    "catering.bakery": "commercial.food_and_drink.bakery",
    "commercial.bakery": "commercial.food_and_drink.bakery",
    "service.beauty.salon": "service.beauty.hairdresser",
    "service.vehicle.car_repair": "service.vehicle.repair",
    "service.car_repair": "service.vehicle.repair",
    "commercial.electronics": "commercial.elektronics",
    "activity.fitness_center": "sport.fitness",
    "activity.sports_centre": "sport",
    "activity.cinema": "entertainment.cinema",
    "service.dry_cleaning": "service.cleaning.dry_cleaning",
}


def normalize_category(raw_category: str) -> str:
    """
    Normalize user or frontend category input into a valid Geoapify category key.

    - Strips whitespace and normalizes case
    - Replaces multi-whitespace with single space
    - Resolves natural language aliases (e.g. 'dental clinics' -> 'healthcare.dentist')
    - Corrects known taxonomy mismatches (e.g. 'healthcare.clinic' -> 'healthcare.clinic_or_praxis')
    - Passes through valid/unrecognized categories without corruption
    """
    if not raw_category:
        return ""

    cleaned = raw_category.strip().lower()
    # Normalize internal whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)

    # 1. Exact alias match
    if cleaned in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[cleaned]

    # 2. Dot-delimited taxonomy fix
    if cleaned in TAXONOMY_FIXES:
        return TAXONOMY_FIXES[cleaned]

    # 3. Check with hyphens or underscores replaced with spaces
    spaced = cleaned.replace("_", " ").replace("-", " ")
    if spaced in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[spaced]

    # 4. Safe fallback for direct / custom Geoapify category strings
    return cleaned


def get_category_display_label(category_key: str) -> str:
    """
    Convert a category key to a clean, user-friendly label.
    """
    LABEL_MAP = {
        "healthcare": "Healthcare (All)",
        "healthcare.dentist": "Dentists & Dental Clinics",
        "healthcare.clinic_or_praxis": "Clinics & Doctors",
        "healthcare.pharmacy": "Pharmacies & Chemists",
        "healthcare.hospital": "Hospitals & Medical Centers",
        "healthcare.veterinary": "Veterinary Clinics",

        "catering": "Food & Catering (All)",
        "catering.cafe": "Cafés & Coffee Shops",
        "catering.restaurant": "Restaurants & Dining",
        "catering.fast_food": "Fast Food & Quick Bites",
        "commercial.food_and_drink.bakery": "Bakeries & Cake Shops",
        "catering.bar": "Bars & Pubs",

        "service": "Local Services (All)",
        "service.beauty.hairdresser": "Hair Salons & Barbers",
        "service.beauty.spa": "Spas & Wellness",
        "service.vehicle.repair": "Auto Repair & Mechanics",
        "service.vehicle.car_wash": "Car Wash",
        "service.financial.bank": "Banks & Financial",
        "service.financial": "Banks & Financial",
        "service.cleaning.dry_cleaning": "Dry Cleaning & Laundry",
        "service.cleaning.laundry": "Dry Cleaning & Laundry",

        "education": "Education (All)",
        "education.school": "Schools",
        "education.college": "Colleges & Institutes",
        "education.university": "Universities",
        "education.library": "Libraries",
        "education.driving_school": "Driving Schools",

        "commercial": "Retail & Commercial (All)",
        "commercial.clothing": "Clothing & Boutiques",
        "commercial.supermarket": "Supermarkets & Groceries",
        "commercial.jewelry": "Jewellery Stores",
        "commercial.elektronics": "Electronics Stores",
        "commercial.books": "Book Stores",
        "commercial.furniture_and_interior": "Furniture & Home",

        "accommodation": "Accommodation (All)",
        "accommodation.hotel": "Hotels & Resorts",
        "accommodation.hostel": "Hostels & Student Housing",
        "accommodation.guest_house": "Guest Houses",
        "accommodation.motel": "Motels",
        "accommodation.apartment": "Serviced Apartments",

        "activity": "Activity & Leisure (All)",
        "sport.fitness": "Gyms & Fitness Centers",
        "entertainment.cinema": "Cinemas & Theatres",
        "activity.community_center": "Community Centers",
        "activity.sport_club": "Sports Clubs",
        "sport.sports_centre": "Sports Facilities",
    }

    return LABEL_MAP.get(category_key, category_key.replace(".", " › ").replace("_", " ").title())


# Structured Category Families for Scanner Family Selection
CATEGORY_FAMILIES: Dict[str, Dict[str, Any]] = {
    "healthcare": {
        "key": "healthcare",
        "label": "Healthcare & Medical",
        "description": "Dentists, Clinics, Doctors, Pharmacies, Hospitals & Medical Centers",
        "subcategories": [
            ("Dentists & Dental Clinics", "healthcare.dentist"),
            ("Clinics & Doctors", "healthcare.clinic_or_praxis"),
            ("Pharmacies & Chemists", "healthcare.pharmacy"),
            ("Hospitals & Medical Centers", "healthcare.hospital"),
            ("Veterinary & Pet Care", "healthcare.veterinary"),
            ("All Healthcare (Broad)", "healthcare"),
        ],
    },
    "catering": {
        "key": "catering",
        "label": "Food & Catering",
        "description": "Restaurants, Cafes, Bakeries, Fast Food & Dining",
        "subcategories": [
            ("Restaurants & Dining", "catering.restaurant"),
            ("Cafes & Coffee Shops", "catering.cafe"),
            ("Fast Food & Quick Bites", "catering.fast_food"),
            ("Bakeries & Cake Shops", "commercial.food_and_drink.bakery"),
            ("Bars & Pubs", "catering.bar"),
            ("All Food & Catering (Broad)", "catering"),
        ],
    },
    "service": {
        "key": "service",
        "label": "Local Services",
        "description": "Salons, Auto Repair, Car Wash, Spas, Financial & Cleaning",
        "subcategories": [
            ("Hair Salons & Barbers", "service.beauty.hairdresser"),
            ("Spas & Wellness", "service.beauty.spa"),
            ("Auto Repair & Mechanics", "service.vehicle.repair"),
            ("Car Wash & Detailing", "service.vehicle.car_wash"),
            ("Banks & Financial", "service.financial"),
            ("Dry Cleaning & Laundry", "service.cleaning.dry_cleaning"),
            ("All Local Services (Broad)", "service"),
        ],
    },
    "commercial": {
        "key": "commercial",
        "label": "Retail & Commercial",
        "description": "Supermarkets, Boutiques, Electronics, Jewellery & Stores",
        "subcategories": [
            ("Supermarkets & Groceries", "commercial.supermarket"),
            ("Clothing & Boutiques", "commercial.clothing"),
            ("Electronics & Appliances", "commercial.elektronics"),
            ("Jewellery Stores", "commercial.jewelry"),
            ("Book Stores", "commercial.books"),
            ("Furniture & Home", "commercial.furniture_and_interior"),
            ("All Commercial (Broad)", "commercial"),
        ],
    },
    "education": {
        "key": "education",
        "label": "Education & Learning",
        "description": "Schools, Colleges, Universities, Libraries & Training",
        "subcategories": [
            ("Schools (Primary & High)", "education.school"),
            ("Colleges & Institutes", "education.college"),
            ("Universities", "education.university"),
            ("Libraries", "education.library"),
            ("Driving Schools", "education.driving_school"),
            ("All Education (Broad)", "education"),
        ],
    },
    "accommodation": {
        "key": "accommodation",
        "label": "Accommodation & Hospitality",
        "description": "Hotels, Resorts, Hostels, Guest Houses & Lodging",
        "subcategories": [
            ("Hotels & Resorts", "accommodation.hotel"),
            ("Hostels & Student Housing", "accommodation.hostel"),
            ("Guest Houses", "accommodation.guest_house"),
            ("Motels & Lodging", "accommodation.motel"),
            ("Serviced Apartments", "accommodation.apartment"),
            ("All Accommodation (Broad)", "accommodation"),
        ],
    },
    "activity": {
        "key": "activity",
        "label": "Activity & Leisure",
        "description": "Gyms, Fitness Centers, Cinemas, Theatres & Clubs",
        "subcategories": [
            ("Gyms & Fitness Centers", "sport.fitness"),
            ("Cinemas & Theatres", "entertainment.cinema"),
            ("Sports Clubs & Facilities", "sport.sports_centre"),
            ("Community Centers", "activity.community_center"),
            ("All Activity & Leisure (Broad)", "activity"),
        ],
    },
}

# Alias map to category family key
_FAMILY_ALIASES: Dict[str, str] = {
    "healthcare": "healthcare",
    "health": "healthcare",
    "medical": "healthcare",
    "catering": "catering",
    "food": "catering",
    "food & catering": "catering",
    "service": "service",
    "services": "service",
    "local services": "service",
    "commercial": "commercial",
    "retail": "commercial",
    "retail & commercial": "commercial",
    "education": "education",
    "learning": "education",
    "accommodation": "accommodation",
    "hospitality": "accommodation",
    "hotels": "accommodation",
    "activity": "activity",
    "leisure": "activity",
    "sports": "activity",
    "activity & leisure": "activity",
}


def get_category_families() -> List[Dict[str, Any]]:
    """Return all available category families with their metadata and subcategories."""
    return [
        {
            "key": fam["key"],
            "label": fam["label"],
            "description": fam["description"],
            "subcategories": [
                {"label": label, "key": key} for label, key in fam["subcategories"]
            ],
        }
        for fam in CATEGORY_FAMILIES.values()
    ]


def expand_category_family(category_or_family: str) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Expand a category family or specific category into a list of (label, geoapify_key) search targets.

    Returns:
        Tuple of (family_name, list_of_subcategories).
        - If input matches a family (e.g. "healthcare"), returns ("Healthcare", [all subcategories]).
        - If input is a specific category (e.g. "dentist"), returns ("Healthcare", [("Dentists & Dental Clinics", "healthcare.dentist")]).
    """
    cleaned = (category_or_family or "").strip().lower()
    if not cleaned:
        cleaned = "commercial"

    # 1. Direct family match or alias match
    fam_key = _FAMILY_ALIASES.get(cleaned)
    if fam_key and fam_key in CATEGORY_FAMILIES:
        fam = CATEGORY_FAMILIES[fam_key]
        return fam["label"], list(fam["subcategories"])

    # 2. Normalized category resolution
    norm_cat = normalize_category(cleaned)

    # Check if norm_cat belongs to any family
    for fam in CATEGORY_FAMILIES.values():
        for label, key in fam["subcategories"]:
            if key == norm_cat:
                return fam["label"], [(label, key)]
        if norm_cat.startswith(fam["key"]):
            return fam["label"], [(get_category_display_label(norm_cat), norm_cat)]

    # 3. Fallback single category
    display_label = get_category_display_label(norm_cat)
    return display_label, [(display_label, norm_cat)]

