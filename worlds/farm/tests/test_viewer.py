import json
from pathlib import Path

from farm_world.layout import default_world, layout_dict

VIEWER = Path(__file__).resolve().parents[1] / "viewer"


def test_the_bundled_layout_matches_the_world():
    bundled = json.loads((VIEWER / "layout.json").read_text())
    assert bundled == json.loads(json.dumps(layout_dict(default_world(1))))


def test_the_layout_places_every_plot_and_farmer_on_the_map():
    layout = layout_dict(default_world(1))
    assert len(layout["tiles"]) == layout["height"]
    assert all(len(row) == layout["width"] for row in layout["tiles"])
    for plot in layout["plots"].values():
        x, y, w, h = plot["rect"]
        assert x + w <= layout["width"] and y + h <= layout["height"]
    for farmer in layout["farmers"].values():
        x, y = farmer["home"]
        assert layout["tile_codes"][layout["tiles"][y][x]] == "house"
