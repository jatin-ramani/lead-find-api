from services.adaptive_grid import GridCell, split_cell

cell = GridCell(
    center_lat=23.0225,
    center_lon=72.5714,
    radius=1000,
)

cells = split_cell(cell)

print(cells)