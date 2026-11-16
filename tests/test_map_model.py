"""Map maths behind the Map tab (no LVGL): Web-Mercator pixels, the tiles a view needs, the
ancestor tile to stretch where a zoom level is missing, fitting the nodes, pin positions.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_map_model.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402,F401

import map_model as M  # noqa: E402

BRUSSELS = (50.8467, 4.3525)          # Grand-Place


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _near(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1, abs(b))


def test_world_pixels():
    x, y = M.world_px(0, 0, 0)
    _assert((x, y) == (128, 128), (x, y))
    x, y = M.world_px(BRUSSELS[0], BRUSSELS[1], 15)
    _assert(_near(x, 4295724.601) and _near(y, 2813984.217), (x, y))
    lat, lon = M.latlon(x, y, 15)
    _assert(_near(lat, BRUSSELS[0]) and _near(lon, BRUSSELS[1]), (lat, lon))


def test_visible_tiles_cover_the_view_and_place_each_tile():
    # centred on a tile corner, a 480 x 424 view needs the 2 x 2 tiles around it
    cx, cy = 100 * 256, 50 * 256
    tiles = M.visible_tiles(cx, cy, 10, 480, 424)
    xs = sorted(set(t[1] for t in tiles))
    ys = sorted(set(t[2] for t in tiles))
    _assert(xs == [99, 100] and ys == [49, 50], (xs, ys))
    first = [t for t in tiles if t[1] == 100 and t[2] == 50][0]
    _assert(first[3:] == (240, 212), first)         # screen position of that tile's corner
    # every tile overlaps the view
    for z, x, y, sx, sy in tiles:
        _assert(z == 10 and sx > -256 and sx < 480 and sy > -256 and sy < 424, (sx, sy))
    # shifted by 100 px it needs a third column
    tiles = M.visible_tiles(cx + 100, cy, 10, 480, 424)
    _assert(sorted(set(t[1] for t in tiles)) == [99, 100, 101], tiles)


def test_tiles_off_the_world_are_left_out_and_x_wraps():
    tiles = M.visible_tiles(128, 10, 0, 480, 424)     # zoom 0: one tile for the whole world
    _assert(all(t[2] == 0 for t in tiles), tiles)
    _assert(all(t[1] == 0 for t in tiles), tiles)
    tiles = M.visible_tiles(5, 300, 1, 480, 424)
    _assert(all(0 <= t[1] < 2 for t in tiles), tiles)


def test_missing_tile_falls_back_to_the_nearest_ancestor():
    have = {(14, 8344, 5487), (8, 130, 85)}
    exists = lambda z, x, y: (z, x, y) in have
    # z17 tile inside that z14 tile: three levels up, scale 8, offset in z14 pixels
    _assert(M.best_tile(17, 8344 * 8 + 5, 5487 * 8 + 2, exists) == (14, 8344, 5487, 3), "z14")
    _assert(M.best_tile(14, 8344, 5487, exists) == (14, 8344, 5487, 0), "itself")
    _assert(M.best_tile(12, 2086, 1371, exists) == (8, 130, 85, 4), "z8")
    _assert(M.best_tile(12, 0, 0, exists) is None, "nothing")


def test_ancestor_crop_is_the_part_of_the_ancestor_that_covers_the_tile():
    # a z17 tile three levels below its z14 ancestor: a 32 px square at (5*32, 2*32)
    _assert(M.ancestor_crop(17, 8344 * 8 + 5, 5487 * 8 + 2, 3) == (160, 64, 32), "crop")
    _assert(M.ancestor_crop(14, 1, 1, 0) == (0, 0, 256), "self")


def test_fit_shows_every_point():
    pts = [(50.80, 3.20), (50.90, 3.40)]
    lat, lon, z = M.fit(pts, 480, 424)
    _assert(_near(lat, 50.85, 1e-3) and _near(lon, 3.30, 1e-6), (lat, lon))
    for p in pts:
        x, y = M.world_px(p[0], p[1], z)
        cx, cy = M.world_px(lat, lon, z)
        _assert(abs(x - cx) < 240 and abs(y - cy) < 212, (z, x - cx, y - cy))
    # one zoom further in, they no longer fit
    x0, y0 = M.world_px(pts[0][0], pts[0][1], z + 1)
    x1, y1 = M.world_px(pts[1][0], pts[1][1], z + 1)
    _assert(abs(x1 - x0) > 480 - 2 * M.FIT_MARGIN or abs(y1 - y0) > 424 - 2 * M.FIT_MARGIN, z)


def test_fit_of_one_point_and_of_none():
    _assert(M.fit([BRUSSELS], 480, 424) == (BRUSSELS[0], BRUSSELS[1], M.ONE_POINT_ZOOM), "one")
    _assert(M.fit([], 480, 424) is None, "none")


def test_far_points_fit_at_least_the_lowest_zoom():
    lat, lon, z = M.fit([(51.0, 3.0), (-33.9, 151.2)], 480, 424)
    _assert(z == M.MIN_ZOOM, z)


def test_pins_for_nodes_with_a_position_in_view():
    nodes = [
        {"pubkey": "aa" * 32, "name": "Repeater Noord", "type": 2, "lat": 50.8467, "lon": 4.3525},
        {"pubkey": "bb" * 32, "name": "Gent", "type": 1, "lat": 51.0543, "lon": 3.7174},
        {"pubkey": "cc" * 32, "name": "Nowhere", "type": 1, "lat": 0.0, "lon": 0.0},
        {"pubkey": "dd" * 32, "name": "Unknown", "type": 3},
    ]
    cx, cy = M.world_px(BRUSSELS[0], BRUSSELS[1], 14)
    pins = M.pins(nodes, cx, cy, 14, 480, 424)
    _assert([p["pubkey"] for p in pins] == ["aa" * 32], pins)
    p = pins[0]
    _assert(p["kind"] == "rptr" and p["name"] == "Repeater Noord", p)
    _assert(abs(p["sx"] - 240) < 1 and abs(p["sy"] - 212) < 1, p)
    # zoomed out, Gent is in view too
    cx, cy = M.world_px(BRUSSELS[0], BRUSSELS[1], 9)
    _assert(len(M.pins(nodes, cx, cy, 9, 480, 424)) == 2, "z9")


def test_positions_of_nodes_skip_missing_and_null_island():
    nodes = [{"lat": 50.8, "lon": 3.2}, {"lat": 0, "lon": 0}, {"name": "x"},
             {"lat": None, "lon": 4.0}]
    _assert(M.positions(nodes) == [(50.8, 3.2)], M.positions(nodes))


def test_tile_path():
    _assert(M.tile_path("/sdcard/maps/dark", 14, 8344, 5487) == "/sdcard/maps/dark/14/8344/5487.png", "")


def test_zoom_stays_in_range():
    _assert(M.clamp_zoom(3) == M.MIN_ZOOM and M.clamp_zoom(30) == M.MAX_ZOOM, "")
    _assert(M.clamp_zoom(12) == 12, "")


def test_lru_keeps_the_newest():
    c = M.LRU(2)
    c.put("a", 1)
    c.put("b", 2)
    _assert(c.get("a") == 1, "a")       # a is now the newest
    c.put("c", 3)
    _assert(c.get("b") is None and c.get("a") == 1 and c.get("c") == 3, "evict b")
    _assert(len(c) == 2, len(c))


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print("%d map model tests passed" % n)
