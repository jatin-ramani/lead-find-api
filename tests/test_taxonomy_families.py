"""Tests for category families and taxonomy expansion."""

import pytest

from services.taxonomy import (
    CATEGORY_FAMILIES,
    expand_category_family,
    normalize_category,
)


def test_expand_category_family_healthcare():
    """Healthcare family must expand to Dentists, Clinics, Pharmacies, Hospitals."""
    label, subcategories = expand_category_family("healthcare")
    assert label == "Healthcare & Medical"
    keys = [k for _, k in subcategories]
    assert "healthcare.dentist" in keys
    assert "healthcare.clinic_or_praxis" in keys
    assert "healthcare.pharmacy" in keys
    assert "healthcare.hospital" in keys


def test_expand_category_family_catering():
    """Catering family must expand to cafes, restaurants, fast food, bakery."""
    label, subcategories = expand_category_family("catering")
    assert label == "Food & Catering"
    keys = [k for _, k in subcategories]
    assert "catering.cafe" in keys
    assert "catering.restaurant" in keys
    assert "catering.fast_food" in keys


def test_expand_custom_category():
    """Unlisted custom category should be normalized and wrapped as single search unit."""
    label, subcategories = expand_category_family("plumber")
    assert len(subcategories) == 1
    assert subcategories[0][1] == "plumber"


def test_normalize_category_aliases():
    assert normalize_category("dentist") == "healthcare.dentist"
    assert normalize_category("cafe") == "catering.cafe"
    assert normalize_category("gym") == "sport.fitness"

