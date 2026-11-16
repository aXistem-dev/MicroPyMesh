"""Map maths for the Map tab (no LVGL): Web-Mercator pixels at a zoom level, the 256 px tiles
a view needs and where they go on screen, the ancestor tile to stretch where a zoom level is
missing on the card, the view that fits a set of points, and pin positions for nodes."""

import math

TILE = 256
MIN_ZOOM = 7            # Belgium fills the screen at 8
MAX_ZOOM = 18           # one level past the deepest tiles, stretched
ONE_POINT_ZOOM = 14
FIT_MARGIN = 32         # px kept free around fitted points (pins have a size)
_KINDS = {1: "chat", 2: "rptr", 3: "room", 4: "sensor"}


def world_px(lat, lon, z):
    """(x, y) in pixels of the whole world map at zoom z."""
    n = TILE * (1 << z)
    s = math.sin(math.radians(max(-85.0511, min(85.0511, lat))))
    return (lon + 180.0) / 360.0 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def latlon(x, y, z):
    n = TILE * (1 << z)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def clamp_zoom(z):
    return max(MIN_ZOOM, min(MAX_ZOOM, z))


def visible_tiles(cx, cy, z, w, h):
    """[(z, x, y, screen x, screen y)] for the tiles a w x h view centred on world pixel
    (cx, cy) shows. Rows off the top or bottom of the world are left out; columns wrap."""
    n = 1 << z
    left, top = cx - w / 2, cy - h / 2
    out = []
    for ty in range(int(math.floor(top / TILE)), int(math.floor((top + h - 1) / TILE)) + 1):
        if not 0 <= ty < n:
            continue
        for tx in range(int(math.floor(left / TILE)), int(math.floor((left + w - 1) / TILE)) + 1):
            out.append((z, tx % n, ty, int(round(tx * TILE - left)), int(round(ty * TILE - top))))
    return out


def best_tile(z, x, y, exists):
    """The tile itself or its nearest ancestor that `exists(z, x, y)`: (z, x, y, levels up),
    or None when not even zoom 0 is there."""
    for up in range(z + 1):
        if exists(z - up, x >> up, y >> up):
            return z - up, x >> up, y >> up, up
    return None


def ancestor_crop(z, x, y, up):
    """The square (left, top, size) of the ancestor `up` levels above tile (z, x, y) that
    covers this tile, in the ancestor's pixels."""
    size = TILE >> up
    mask = (1 << up) - 1
    return (x & mask) * size, (y & mask) * size, size


def positions(nodes):
    """(lat, lon) of the nodes that sent a position (0, 0 means none was sent)."""
    out = []
    for n in nodes:
        lat, lon = n.get("lat"), n.get("lon")
        if lat is None or lon is None or (lat == 0 and lon == 0):
            continue
        out.append((lat, lon))
    return out


def fit(points, w, h):
    """(lat, lon, zoom) of the closest view that shows every point, or None for no points."""
    if not points:
        return None
    if len(points) == 1:
        return points[0][0], points[0][1], ONE_POINT_ZOOM
    xs = [world_px(p[0], p[1], 0)[0] for p in points]
    ys = [world_px(p[0], p[1], 0)[1] for p in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    z = MAX_ZOOM - 1
    while z > MIN_ZOOM and (dx * (1 << z) > w - 2 * FIT_MARGIN or dy * (1 << z) > h - 2 * FIT_MARGIN):
        z -= 1
    lat, lon = latlon(cx, cy, 0)
    return lat, lon, z


def pins(nodes, cx, cy, z, w, h, margin=16):
    """Screen positions of the nodes with a position inside the view (plus a margin):
    [{"pubkey", "name", "kind", "sx", "sy"}]."""
    left, top = cx - w / 2, cy - h / 2
    out = []
    for n in nodes:
        lat, lon = n.get("lat"), n.get("lon")
        if lat is None or lon is None or (lat == 0 and lon == 0):
            continue
        x, y = world_px(lat, lon, z)
        sx, sy = x - left, y - top
        if -margin <= sx <= w + margin and -margin <= sy <= h + margin:
            out.append({"pubkey": n.get("pubkey"), "name": n.get("name") or "?",
                        "kind": _KINDS.get(n.get("type"), "other"), "sx": sx, "sy": sy})
    return out


def tile_path(root, z, x, y):
    return "%s/%d/%d/%d.png" % (root, z, x, y)


class LRU:
    """A small least-recently-used map (MicroPython dicts keep no order)."""

    def __init__(self, size):
        self.size = size
        self._d = {}
        self._order = []

    def __len__(self):
        return len(self._d)

    def get(self, key):
        if key not in self._d:
            return None
        self._order.remove(key)
        self._order.append(key)
        return self._d[key]

    def put(self, key, value):
        if key in self._d:
            self._order.remove(key)
        self._d[key] = value
        self._order.append(key)
        while len(self._order) > self.size:
            del self._d[self._order.pop(0)]
