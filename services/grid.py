import math


EARTH_RADIUS = 6371000  # meters


def meters_to_latitude(meters: float) -> float:
    return meters / 111320


def meters_to_longitude(meters: float, latitude: float) -> float:
    return meters / (111320 * math.cos(math.radians(latitude)))


def generate_grid(
    center_lat: float,
    center_lon: float,
    radius: int = 5000,
    cell_size: int = 500,
):
    """
    Generate grid points inside a circular area.

    radius = scan radius in meters
    cell_size = distance between grid points
    """

    lat_step = meters_to_latitude(cell_size)
    lon_step = meters_to_longitude(cell_size, center_lat)

    radius_lat = meters_to_latitude(radius)
    radius_lon = meters_to_longitude(radius, center_lat)

    grid = []

    lat = center_lat - radius_lat

    while lat <= center_lat + radius_lat:

        lon = center_lon - radius_lon

        while lon <= center_lon + radius_lon:

            grid.append({
                "lat": round(lat, 6),
                "lon": round(lon, 6),
            })

            lon += lon_step

        lat += lat_step

    return grid