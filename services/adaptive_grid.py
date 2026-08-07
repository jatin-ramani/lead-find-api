from dataclasses import dataclass
from typing import List


@dataclass
class GridCell:
    center_lat: float
    center_lon: float
    radius: int


def split_cell(cell: GridCell) -> List[GridCell]:
    """
    Split one grid cell into 4 smaller cells.
    """

    new_radius = cell.radius // 2

    offset = new_radius / 111320

    return [
        GridCell(
            cell.center_lat + offset,
            cell.center_lon - offset,
            new_radius,
        ),
        GridCell(
            cell.center_lat + offset,
            cell.center_lon + offset,
            new_radius,
        ),
        GridCell(
            cell.center_lat - offset,
            cell.center_lon - offset,
            new_radius,
        ),
        GridCell(
            cell.center_lat - offset,
            cell.center_lon + offset,
            new_radius,
        ),
    ]