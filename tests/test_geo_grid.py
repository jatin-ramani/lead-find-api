"""Tests for geographic radial grid subdivision engine."""

import math
import pytest

from services.geo_grid import (
    ScanCell,
    destination_point,
    generate_city_scan_grid,
    _bearing_to_cardinal,
)


def test_destination_point_north():
    """Moving 11.132 km North should increase latitude by ~0.1 degrees."""
    lat, lon = 23.0225, 72.5714
    d_lat, d_lon = destination_point(lat, lon, distance_km=11.132, bearing_deg=0)
    assert abs(d_lat - (lat + 0.1)) < 0.005
    assert abs(d_lon - lon) < 0.001


def test_destination_point_east():
    """Moving East should increase longitude."""
    lat, lon = 23.0225, 72.5714
    d_lat, d_lon = destination_point(lat, lon, distance_km=10.0, bearing_deg=90)
    assert abs(d_lat - lat) < 0.005
    assert d_lon > lon


def test_bearing_to_cardinal():
    assert _bearing_to_cardinal(0) == "North"
    assert _bearing_to_cardinal(45) == "North-East"
    assert _bearing_to_cardinal(90) == "East"
    assert _bearing_to_cardinal(180) == "South"
    assert _bearing_to_cardinal(270) == "West"


def test_generate_city_scan_grid_centroid():
    """Grid generation must always produce Cell 0 at city centroid."""
    cells = generate_city_scan_grid(23.0225, 72.5714, max_radius_km=10.0, cell_radius_km=3.0)
    assert len(cells) > 1
    centroid_cell = cells[0]
    assert centroid_cell.cell_index == 0
    assert centroid_cell.distance_km == 0.0
    assert centroid_cell.latitude == 23.0225
    assert centroid_cell.longitude == 72.5714
    assert "Center" in centroid_cell.label


def test_generate_city_scan_grid_radius_rings():
    """Larger max_radius_km must produce multiple concentric rings with overlapping cells."""
    small_cells = generate_city_scan_grid(23.0225, 72.5714, max_radius_km=5.0)
    large_cells = generate_city_scan_grid(23.0225, 72.5714, max_radius_km=25.0)

    assert len(large_cells) > len(small_cells)
    assert any(c.distance_km > 0 for c in large_cells)

    # All cells must have valid coordinates and non-zero radius
    for c in large_cells:
        assert -90 <= c.latitude <= 90
        assert -180 <= c.longitude <= 180
        assert c.radius_meters > 0
        assert len(c.label) > 0

