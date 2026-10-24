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


def test_the_live_server_sends_only_whole_lines_after_the_offset(tmp_path):
    import http.client
    import sys
    import threading
    from http.server import ThreadingHTTPServer

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewer"))
    import serve

    log = tmp_path / "run.jsonl"
    log.write_text('{"a": 1}\n{"b": 2}\n{"c"')
    handler = type("H", (serve.Handler,), {"log": log})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:

        def get(start):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
            conn.request("GET", f"/run.jsonl?from={start}")
            response = conn.getresponse()
            return response.status, response.read(), response.getheader("X-Log-Offset")

        assert get(0) == (200, b'{"a": 1}\n{"b": 2}\n', "0")
        assert get(9) == (200, b'{"b": 2}\n', "9")
        assert get(18)[1] == b""  # the half-written row is held back until its newline lands
    finally:
        server.shutdown()
