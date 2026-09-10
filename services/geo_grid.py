"""
Geographic Grid & Radial Subdivision Engine for Continuous City Scanning.

Divides a target city into a deterministic, comprehensive grid of circular search cells
covering from 0 to N km radius (default: 25 km).

Guarantees:
1. Complete geographic coverage across the target metropolitan/city area.
2. Deterministic, reproducible cell ordering from city center outwards.
3. Human-readable descriptive labels for live frontend progress display.
4. Mathematical geodesy coordinates (WGS-84 / spherical approximation).
"""

from dataclasses import dataclass
import math
from typing import List, Tuple

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class ScanCell:
    cell_index: int
    latitude: float
    longitude: float
    radius_meters: int
    distance_km: float
    bearing_deg: float
    label: str


def _bearing_to_cardinal(bearing_deg: float) -> str:
    """Convert bearing in degrees (0-360) to 8-point cardinal compass direction."""
    normalized = (bearing_deg % 360 + 360) % 360
    directions = ["North", "North-East", "East", "South-East", "South", "South-West", "West", "North-West"]
    idx = int((normalized + 22.5) / 45.0) % 8
    return directions[idx]


def destination_point(
    lat: float,
    lon: float,
    distance_km: float,
    bearing_deg: float,
) -> Tuple[float, float]:
    """
    Calculate the destination GPS point given starting point, distance (km), and bearing (degrees).
    Uses standard Great Circle / Haversine formula.
    """
    if distance_km <= 0:
        return lat, lon

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    bearing_rad = math.radians(bearing_deg)
    d_div_r = distance_km / EARTH_RADIUS_KM

    lat2_rad = math.asin(
        math.sin(lat_rad) * math.cos(d_div_r)
        + math.cos(lat_rad) * math.sin(d_div_r) * math.cos(bearing_rad)
    )

    lon2_rad = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(d_div_r) * math.cos(lat_rad),
        math.cos(d_div_r) - math.sin(lat_rad) * math.sin(lat2_rad),
    )

    return math.degrees(lat2_rad), math.degrees(lon2_rad)


def generate_city_scan_grid(
    center_lat: float,
    center_lon: float,
    max_radius_km: float = 25.0,
    cell_radius_km: float = 3.0,
) -> List[ScanCell]:
    """
    Generate a list of overlapping circular ScanCells covering from city center out to max_radius_km.

    Args:
        center_lat: City center latitude.
        center_lon: City center longitude.
        max_radius_km: Overall coverage radius (e.g. 25 km).
        cell_radius_km: Radius of each individual search cell (default: 3 km ~ 3000m).

    Returns:
        Ordered list of ScanCells starting at Center (0 km) followed by expanding concentric rings.
    """
    cells: List[ScanCell] = []
    cell_radius_meters = int(cell_radius_km * 1000)

    # 1. Cell 0: City Center
    cells.append(
        ScanCell(
            cell_index=0,
            latitude=round(center_lat, 6),
            longitude=round(center_lon, 6),
            radius_meters=cell_radius_meters,
            distance_km=0.0,
            bearing_deg=0.0,
            label="City Center (0 km)",
        )
    )

    # Step distance between concentric rings (e.g. ~75% of cell diameter to ensure overlap)
    ring_step_km = max(2.0, cell_radius_km * 1.5)
    current_radius_km = ring_step_km
    ring_number = 1

    while current_radius_km <= (max_radius_km + 0.5):
        # Circumference = 2 * pi * r
        circumference = 2.0 * math.pi * current_radius_km
        # Calculate number of cells required along this ring to ensure adjacent overlap
        num_cells = max(6, int(math.ceil(circumference / (cell_radius_km * 1.4))))
        angle_step = 360.0 / num_cells

        for i in range(num_cells):
            bearing = round(i * angle_step, 1)
            cell_lat, cell_lon = destination_point(center_lat, center_lon, current_radius_km, bearing)
            cardinal = _bearing_to_cardinal(bearing)

            cell_idx = len(cells)
            label = f"Ring {ring_number} ({current_radius_km:.1f} km) - {cardinal}"

            cells.append(
                ScanCell(
                    cell_index=cell_idx,
                    latitude=round(cell_lat, 6),
                    longitude=round(cell_lon, 6),
                    radius_meters=cell_radius_meters,
                    distance_km=round(current_radius_km, 2),
                    bearing_deg=bearing,
                    label=label,
                )
            )

        current_radius_km += ring_step_km
        ring_number += 1

    return cells
