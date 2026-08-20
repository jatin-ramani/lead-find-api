import pytest
from services.taxonomy import normalize_category, get_category_display_label

def test_normalize_healthcare_aliases():
    assert normalize_category("dentist") == "healthcare.dentist"
    assert normalize_category("Dentists") == "healthcare.dentist"
    assert normalize_category("dental clinic") == "healthcare.dentist"
    assert normalize_category("dental clinics") == "healthcare.dentist"
    assert normalize_category("clinic") == "healthcare.clinic_or_praxis"
    assert normalize_category("clinics") == "healthcare.clinic_or_praxis"
    assert normalize_category("healthcare.clinic") == "healthcare.clinic_or_praxis"
    assert normalize_category("doctor") == "healthcare.clinic_or_praxis"
    assert normalize_category("pharmacy") == "healthcare.pharmacy"
    assert normalize_category("hospital") == "healthcare.hospital"

def test_normalize_food_aliases():
    assert normalize_category("cafe") == "catering.cafe"
    assert normalize_category("cafés") == "catering.cafe"
    assert normalize_category("coffee shop") == "catering.cafe"
    assert normalize_category("restaurant") == "catering.restaurant"
    assert normalize_category("restaurants") == "catering.restaurant"
    assert normalize_category("fast food") == "catering.fast_food"
    assert normalize_category("bakery") == "commercial.food_and_drink.bakery"
    assert normalize_category("bakeries") == "commercial.food_and_drink.bakery"
    assert normalize_category("catering.bakery") == "commercial.food_and_drink.bakery"

def test_normalize_services_aliases():
    assert normalize_category("salon") == "service.beauty.hairdresser"
    assert normalize_category("salons") == "service.beauty.hairdresser"
    assert normalize_category("barber") == "service.beauty.hairdresser"
    assert normalize_category("service.beauty.salon") == "service.beauty.hairdresser"
    assert normalize_category("car repair") == "service.vehicle.repair"
    assert normalize_category("auto repair") == "service.vehicle.repair"
    assert normalize_category("mechanic") == "service.vehicle.repair"
    assert normalize_category("service.vehicle.car_repair") == "service.vehicle.repair"
    assert normalize_category("bank") == "service.financial.bank"
    assert normalize_category("dry cleaning") == "service.cleaning.dry_cleaning"

def test_normalize_education_aliases():
    assert normalize_category("school") == "education.school"
    assert normalize_category("schools") == "education.school"
    assert normalize_category("college") == "education.college"
    assert normalize_category("university") == "education.university"
    assert normalize_category("library") == "education.library"

def test_normalize_commercial_aliases():
    assert normalize_category("clothing") == "commercial.clothing"
    assert normalize_category("clothes") == "commercial.clothing"
    assert normalize_category("boutique") == "commercial.clothing"
    assert normalize_category("supermarket") == "commercial.supermarket"
    assert normalize_category("jewellery") == "commercial.jewelry"
    assert normalize_category("jewelry") == "commercial.jewelry"
    assert normalize_category("electronics") == "commercial.elektronics"
    assert normalize_category("commercial.electronics") == "commercial.elektronics"
    assert normalize_category("book store") == "commercial.books"

def test_normalize_accommodation_aliases():
    assert normalize_category("hotel") == "accommodation.hotel"
    assert normalize_category("hotels") == "accommodation.hotel"
    assert normalize_category("hostel") == "accommodation.hostel"
    assert normalize_category("guest house") == "accommodation.guest_house"
    assert normalize_category("motel") == "accommodation.motel"

def test_normalize_activity_aliases():
    assert normalize_category("gym") == "sport.fitness"
    assert normalize_category("gyms") == "sport.fitness"
    assert normalize_category("fitness") == "sport.fitness"
    assert normalize_category("fitness center") == "sport.fitness"
    assert normalize_category("activity.fitness_center") == "sport.fitness"
    assert normalize_category("cinema") == "entertainment.cinema"
    assert normalize_category("cinemas") == "entertainment.cinema"
    assert normalize_category("movie theatre") == "entertainment.cinema"
    assert normalize_category("community center") == "activity.community_center"

def test_pass_through_valid_and_unknown_categories():
    assert normalize_category("healthcare") == "healthcare"
    assert normalize_category("commercial") == "commercial"
    assert normalize_category("office") == "office"
    assert normalize_category("custom_niche_tag") == "custom_niche_tag"

def test_display_labels():
    assert get_category_display_label("healthcare.dentist") == "Dentists & Dental Clinics"
    assert get_category_display_label("sport.fitness") == "Gyms & Fitness Centers"
    assert get_category_display_label("commercial.food_and_drink.bakery") == "Bakeries & Cake Shops"
