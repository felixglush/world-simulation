"""The starting map: a rice valley beside the river and a wheat upland with a shared well."""

from __future__ import annotations

from .world import CanalNode, Channel, Farmer, FarmWorld, Plot

WIDTH, HEIGHT = 36, 22
START_DAY = 140  # 20 May: the wheat is in the barn and the monsoon is three weeks away


def _tiles() -> list[list[str]]:
    grid = [["grass"] * WIDTH for _ in range(HEIGHT)]
    for y in range(HEIGHT):
        for x in range(0, 3):
            grid[y][x] = "river"
        for x in range(24, WIDTH):
            grid[y][x] = "hill" if y < 2 or y > 19 else "upland"
    return grid


def default_world(seed: int = 1, start_day: int = START_DAY) -> FarmWorld:
    tiles = _tiles()
    plots: list[Plot] = []
    farmers: list[Farmer] = []

    # Riverbend grows rice on three terraces stepping down from the canal intake.
    rice_rows = [(4, 2, 4400.0), (4, 8, 4250.0), (4, 14, 4100.0)]
    for i, (x, y, elev) in enumerate(rice_rows, start=1):
        plots.append(
            Plot(
                id=f"paddy_{i}",
                crop_kind="rice",
                owner=f"rice_{i}",
                community="riverbend",
                x=x + 6,
                y=y,
                w=5,
                h=5,
                elevation_mm=elev,
                bund_mm=120.0,
                nitrogen=60.0,
                spill_to=f"paddy_{i + 1}" if i < 3 else None,
            )
        )
        farmers.append(
            Farmer(
                id=f"rice_{i}",
                community="riverbend",
                plot_id=f"paddy_{i}",
                home=(x + 13, y + 2),
                inventory={
                    "rice": 650.0,
                    "rice_seed": 60.0,
                    "manure": 6.0,
                    "pest_control": 2.0,
                    "coin": 50.0,
                },
            )
        )
        for dx in range(5):
            for dy in range(5):
                tiles[y + dy][x + 6 + dx] = "paddy"
        tiles[y + 2][x + 13] = "house"

    # Highfield grows winter wheat on dry upland fields and shares one well.
    for i, y in enumerate((3, 9, 15), start=1):
        plots.append(
            Plot(
                id=f"field_{i}",
                crop_kind="wheat",
                owner=f"wheat_{i}",
                community="highfield",
                x=26,
                y=y,
                w=5,
                h=5,
                elevation_mm=7000.0,
                bund_mm=180.0,
                nitrogen=55.0,
                water_mm=70.0,
            )
        )
        farmers.append(
            Farmer(
                id=f"wheat_{i}",
                community="highfield",
                plot_id=f"field_{i}",
                home=(33, y + 2),
                inventory={
                    "flour": 900.0,
                    "wheat_seed": 90.0,
                    "manure": 8.0,
                    "pest_control": 2.0,
                    "coin": 50.0,
                },
            )
        )
        for dx in range(5):
            for dy in range(5):
                tiles[y + dy][26 + dx] = "field"
        tiles[y + 2][33] = "house"
    tiles[10][23] = "well"
    tiles[11][19] = "well"

    canals = [
        CanalNode("canal_1", 4450.0),
        CanalNode("canal_2", 4300.0),
        CanalNode("canal_3", 4150.0),
    ]
    channels = [
        Channel("intake", "river", "canal_1", 90.0, path=((3, 4), (4, 4), (5, 4))),
        Channel("turnout_1", "canal_1", "paddy_1", 70.0, path=((6, 4), (8, 4), (10, 4))),
        Channel("carry_1", "canal_1", "canal_2", 80.0, path=((5, 5), (5, 8), (5, 10))),
        Channel("turnout_2", "canal_2", "paddy_2", 70.0, path=((6, 10), (8, 10), (10, 10))),
        Channel("carry_2", "canal_2", "canal_3", 80.0, path=((5, 11), (5, 14), (5, 16))),
        Channel("turnout_3", "canal_3", "paddy_3", 70.0, path=((6, 16), (8, 16), (10, 16))),
        Channel("outlet_1", "paddy_1", "drain", 40.0, path=((15, 4), (16, 4))),
        Channel("outlet_2", "paddy_2", "drain", 40.0, path=((15, 10), (16, 10))),
        Channel("outlet_3", "paddy_3", "drain", 40.0, path=((15, 16), (16, 16))),
    ]
    return FarmWorld(seed, start_day, plots, farmers, canals, channels, tiles)
