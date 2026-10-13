"""Map tiles from the SD card (no LVGL): our 8-bit palette PNGs decoded straight to RGB565,
other PNGs handed on as they are, a missing zoom level stretched from the nearest ancestor,
and a store that loads the tiles a view asks for, most wanted first.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_map_tiles.py
"""

import errno
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402,F401

import map_tiles as MT  # noqa: E402

ROOT = "/sdcard/maps/dark"


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(indices, palette, w=256, h=256, depth=8, filt=0):
    """A palette PNG; indices is a function (x, y) -> palette index."""
    rows = b"".join(bytes([filt]) + bytes(indices(x, y) for x in range(w)) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, 3, 0, 0, 0))
            + _chunk(b"PLTE", b"".join(bytes(c) for c in palette))
            + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


def rgb565(c):
    return ((c[0] >> 3) << 11) | ((c[1] >> 2) << 5) | (c[2] >> 3)


def px(buf, x, y):
    i = 2 * (y * 256 + x)
    return buf[i] | (buf[i + 1] << 8)


PAL = [(20, 27, 35), (233, 238, 243), (244, 169, 59), (11, 33, 51)]
STRIPES = lambda x, y: (x // 64) % 4          # four vertical bands


class Files:
    def __init__(self, files=None, error=None):
        self.files = files or {}
        self.reads = []
        self.error = error

    def __call__(self, path):
        self.reads.append(path)
        if self.error is not None:
            raise OSError(self.error)
        if path not in self.files:
            raise OSError(errno.ENOENT)
        return self.files[path]


def test_our_png_decodes_to_rgb565():
    out = bytearray(MT.TILE_BYTES)
    _assert(MT.decode_png(png(STRIPES, PAL), out), "decoded")
    for x, band in ((0, 0), (70, 1), (130, 2), (255, 3)):
        _assert(px(out, x, 9) == rgb565(PAL[band]), (x, hex(px(out, x, 9))))


def test_decoding_gives_way_between_pieces():
    calls = []
    out = bytearray(MT.TILE_BYTES)
    _assert(MT.decode_png(png(STRIPES, PAL), out, pause=lambda: calls.append(1)), "decoded")
    _assert(len(calls) >= 4, len(calls))             # inflate pieces and expand bands
    _assert(px(out, 255, 255) == rgb565(PAL[3]), "same result")


def test_a_paused_store_loads_nothing():
    files = Files({ROOT + "/9/1/2.png": png(STRIPES, PAL)})
    s = MT.TileStore(ROOT, files)
    s.want([(9, 1, 2)])
    s.pause(True)
    _assert(not s.load_next() and files.reads == [], "paused")
    s.pause(False)
    _assert(s.load_next(), "resumed")


def test_other_pngs_are_left_to_lvgl():
    out = bytearray(MT.TILE_BYTES)
    _assert(not MT.decode_png(png(STRIPES, PAL, filt=1), out), "filtered rows")
    _assert(not MT.decode_png(png(lambda x, y: 0, PAL, w=128, h=128), out), "not 256 px")
    _assert(not MT.decode_png(b"not a png at all", out), "garbage")


def test_store_loads_a_tile():
    files = Files({ROOT + "/14/8344/5487.png": png(STRIPES, PAL)})
    s = MT.TileStore(ROOT, files)
    s.want([(14, 8344, 5487)])
    _assert(s.load_next(), "loaded one")
    kind, buf = s.get((14, 8344, 5487))
    _assert(kind == "rgb565" and px(buf, 130, 0) == rgb565(PAL[2]), kind)
    _assert(not s.load_next(), "nothing left")


def test_a_missing_zoom_is_stretched_from_the_nearest_ancestor():
    # quadrant colours in the z16 tile; its z17 child (x odd, y even) is its top-right quarter
    quad = lambda x, y: (1 if x >= 128 else 0) + (2 if y >= 128 else 0)
    files = Files({ROOT + "/16/100/200.png": png(quad, PAL)})
    s = MT.TileStore(ROOT, files)
    s.want([(17, 201, 400)])
    s.load_next()
    kind, buf = s.get((17, 201, 400))
    _assert(kind == "rgb565", kind)
    _assert(px(buf, 0, 0) == rgb565(PAL[1]) and px(buf, 255, 255) == rgb565(PAL[1]), "top right")
    s.want([(17, 200, 401)])                       # bottom-left quarter, ancestor now cached
    n = len(files.reads)
    s.load_next()                                  # a preview from the cached ancestor
    s.load_next()                                  # the card has no such tile: stretched
    _assert(not s.is_preview((17, 200, 401)), "final")
    _assert(px(s.get((17, 200, 401))[1], 10, 10) == rgb565(PAL[2]), "bottom left")
    _assert(files.reads[n:] == [ROOT + "/17/200/401.png"], files.reads[n:])


def test_zooming_in_shows_a_stretched_preview_before_the_real_tile():
    quad = lambda x, y: (1 if x >= 128 else 0) + (2 if y >= 128 else 0)
    files = Files({ROOT + "/16/100/200.png": png(quad, PAL),
                   ROOT + "/17/201/400.png": png(lambda x, y: 3, PAL)})
    s = MT.TileStore(ROOT, files)
    s.want([(16, 100, 200)])
    s.load_next()
    n = len(files.reads)
    s.want([(17, 201, 400), (17, 200, 401)])
    s.load_next()                                  # previews first, without reading the card
    s.load_next()
    _assert(len(files.reads) == n, files.reads[n:])
    kind, buf = s.get((17, 201, 400))[:2]
    _assert(kind == "rgb565" and s.is_preview((17, 201, 400)), s.get((17, 201, 400)))
    _assert(px(buf, 0, 0) == rgb565(PAL[1]), "stretched top-right quarter")
    s.load_next()                                  # then the real tile
    _assert(files.reads[n:] == [ROOT + "/17/201/400.png"], files.reads[n:])
    _assert(not s.is_preview((17, 201, 400)), "real tile now")
    _assert(px(s.get((17, 201, 400))[1], 0, 0) == rgb565(PAL[3]), "real colour")


def test_a_tile_with_nothing_above_it_is_none_and_not_looked_for_again():
    files = Files({})
    s = MT.TileStore(ROOT, files)
    s.want([(3, 1, 1)])
    s.load_next()
    _assert(s.get((3, 1, 1)) == ("none", None), s.get((3, 1, 1)))
    n = len(files.reads)
    s.want([(3, 1, 1), (3, 1, 0)])                 # a sibling: same ancestors, known missing
    s.load_next()
    _assert(files.reads[n:] == [ROOT + "/3/1/0.png"], files.reads[n:])


def test_a_png_in_another_format_is_handed_on_as_is():
    data = png(STRIPES, PAL, filt=2)
    s = MT.TileStore(ROOT, Files({ROOT + "/9/1/2.png": data}))
    s.want([(9, 1, 2)])
    s.load_next()
    _assert(s.get((9, 1, 2)) == ("png", data), "png")


def test_no_card_is_reported_once():
    files = Files(error=errno.ENODEV)
    s = MT.TileStore(ROOT, files)
    s.want([(9, 1, 2), (9, 1, 3)])
    s.load_next()
    _assert(s.status == "nocard", s.status)
    _assert(not s.load_next() and len(files.reads) == 1, files.reads)
    files.error = None
    files.files[ROOT + "/9/1/3.png"] = png(STRIPES, PAL)
    s.retry()
    s.load_next()
    _assert(s.status == "ok", s.status)


def test_a_newer_view_replaces_what_was_wanted():
    files = Files({ROOT + "/9/%d/0.png" % i: png(STRIPES, PAL) for i in range(4)})
    s = MT.TileStore(ROOT, files)
    s.want([(9, 0, 0), (9, 1, 0)])
    s.want([(9, 3, 0)])
    s.load_next()
    _assert(files.reads == [ROOT + "/9/3/0.png"], files.reads)


def test_buffers_of_evicted_tiles_are_reused():
    files = Files({ROOT + "/9/%d/0.png" % i: png(STRIPES, PAL) for i in range(6)})
    s = MT.TileStore(ROOT, files, capacity=3)
    seen = []
    for i in range(6):
        s.want([(9, i, 0)])
        s.load_next()
        seen.append(id(s.get((9, i, 0))[1]))
    _assert(len(set(seen)) <= 4, len(set(seen)))      # 3 cached + 1 in flight at most
    _assert(s.get((9, 0, 0)) is None and s.get((9, 5, 0))[0] == "rgb565", "lru")


def test_the_ui_may_use_the_store_from_inside_the_loader():
    # MicroPython runs scheduled LVGL work between any two bytecodes of whichever thread is
    # running, the loader included, even in the middle of the store's own bookkeeping: the
    # map's timer then calls get() and want() right there
    s = None
    calls = []

    class Interrupting(dict):
        def get(self, key, default=None):
            if not calls:
                calls.append("ui")
                s.get((9, 9, 9))
                s.want([(9, 1, 2)])
                s.pause(False)
            return dict.get(self, key, default)

        def __contains__(self, key):
            self.get(key)
            return dict.__contains__(self, key)

    files = Files({ROOT + "/9/1/2.png": png(STRIPES, PAL)})
    s = MT.TileStore(ROOT, files)
    s._cache = Interrupting()
    s.want([(9, 1, 2)])
    _assert(s.load_next(), "loaded")
    _assert(calls == ["ui"] and s.get((9, 1, 2))[0] == "rgb565", calls)


def test_ready_callback_names_the_tile():
    got = []
    files = Files({ROOT + "/9/1/2.png": png(STRIPES, PAL)})
    s = MT.TileStore(ROOT, files, on_ready=got.append)
    s.want([(9, 1, 2)])
    s.load_next()
    _assert(got == [(9, 1, 2)], got)


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print("%d map tile tests passed" % n)
