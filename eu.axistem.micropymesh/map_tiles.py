"""Map tiles from the SD card, decoded off the UI thread.

The tiles are 256 px 8-bit palette PNGs with filter type 0 on every row, as the tile renderer
writes them. Such a tile decodes here in about 100 ms: one zlib inflate in C, then a viper
loop through the palette straight to RGB565, which LVGL draws as a plain copy. LVGL's own PNG decoder takes 300-450 ms per tile and leaves 32-bit pixels
that are slow to draw, so it only gets the PNGs this module does not understand.

A tile missing on the card (zoom levels past the street-level area) is stretched from its
nearest ancestor that is there."""

import errno
import struct

TILE = 256
TILE_BYTES = TILE * TILE * 2            # RGB565
_ROW = TILE + 1                         # a filter byte, then 256 palette indices
_SIG = b"\x89PNG\r\n\x1a\n"
BAND = 128                              # rows expanded per call (~7 ms on the device)
PIECE = 16384                           # bytes inflated per call (~20 ms)
PREVIEW_UP = 2                          # stretch a cached tile up to this many levels for a preview

# Compiled from source so that a build without the native emitter (the desktop port) still
# imports this module and takes the plain loops below.
_VIPER_SRC = """
@micropython.viper
def _expand(dst: ptr16, src: ptr8, pal: ptr16, r0: int, r1: int):
    d = r0 * 256
    for r in range(r0, r1):
        s = r * 257 + 1             # past the row's filter byte (0)
        for _c in range(256):
            dst[d] = pal[src[s]]
            d += 1
            s += 1

@micropython.viper
def _stretch(dst: ptr16, src: ptr16, left: int, top: int, up: int, y0: int, y1: int):
    d = y0 * 256
    for y in range(y0, y1):
        row = (top + (y >> up)) * 256 + left
        for x in range(256):
            dst[d] = src[row + (x >> up)]
            d += 1
"""


def _expand(dst, src, pal, r0, r1):
    d = memoryview(dst).cast("H")
    p = memoryview(pal).cast("H")
    for r in range(r0, r1):
        base = r * _ROW + 1
        for c in range(TILE):
            d[r * TILE + c] = p[src[base + c]]


def _stretch(dst, src, left, top, up, y0, y1):
    d = memoryview(dst).cast("H")
    s = memoryview(src).cast("H")
    for y in range(y0, y1):
        row = (top + (y >> up)) * TILE + left
        for x in range(TILE):
            d[y * TILE + x] = s[row + (x >> up)]


def _bands(fn, pause, *args):
    """fn(*args, first row, end row) over the tile in BAND-row pieces, giving way between."""
    for r in range(0, TILE, BAND):
        fn(*args, r, r + BAND)
        if pause is not None:
            pause()


VIPER = False
try:
    import micropython
    _ns = {"micropython": micropython}
    exec(_VIPER_SRC, _ns)
    _expand, _stretch = _ns["_expand"], _ns["_stretch"]
    VIPER = True
except Exception:          # CPython, or MicroPython without viper
    pass


def _inflate(data, out, pause=None):
    """zlib stream `data` -> exactly len(out) bytes in `out`, PIECE bytes per call with
    pause() between; False if it does not fit."""
    try:
        import deflate
        import io
        stream = deflate.DeflateIO(io.BytesIO(data), deflate.ZLIB)
        read = lambda mv: stream.readinto(mv)
    except ImportError:         # CPython
        import zlib
        d = zlib.decompressobj()
        rest = [bytes(data)]

        def read(mv):
            chunk = d.decompress(rest[0], len(mv))
            rest[0] = d.unconsumed_tail
            mv[:len(chunk)] = chunk
            return len(chunk)
    n = 0
    mv = memoryview(out)
    while n < len(out):
        got = read(mv[n:n + PIECE])
        if not got:
            return False
        n += got
        if pause is not None:
            pause()
    return True


def decode_png(data, out, scratch=None, pause=None):
    """Decode one of our tiles into `out` (TILE_BYTES, RGB565). False when `data` is not a
    256 x 256 8-bit palette PNG with filter 0 on every row. `pause()` runs between pieces of
    the work, so a thread decoding tiles lets the UI thread in every few milliseconds."""
    try:
        if data[:8] != _SIG:
            return False
        mv = memoryview(data)
        i = 8
        pal = None
        idat = []
        while i + 8 <= len(data):
            n = struct.unpack(">I", data[i:i + 4])[0]
            kind = bytes(data[i + 4:i + 8])
            body = mv[i + 8:i + 8 + n]
            i += 12 + n
            if kind == b"IHDR":
                w, h, depth, ctype, _c, _f, lace = struct.unpack(">IIBBBBB", body)
                if (w, h, depth, ctype, lace) != (TILE, TILE, 8, 3, 0):
                    return False
            elif kind == b"PLTE":
                pal = bytearray(512)
                for k in range(len(body) // 3):
                    r, g, b = body[3 * k], body[3 * k + 1], body[3 * k + 2]
                    v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                    pal[2 * k] = v & 0xFF
                    pal[2 * k + 1] = v >> 8
            elif kind == b"IDAT":
                idat.append(bytes(body))
            elif kind == b"IEND":
                break
        if pal is None or not idat:
            return False
        if scratch is None:
            scratch = bytearray(_ROW * TILE)
        if not _inflate(idat[0] if len(idat) == 1 else b"".join(idat), scratch, pause):
            return False
        for r in range(TILE):
            if scratch[r * _ROW]:
                return False            # a filtered row: not our format
        _bands(_expand, pause, out, scratch, pal)
        return True
    except (ValueError, IndexError, OSError):
        return False


class TileStore:
    """Tiles by (z, x, y): ("rgb565", buffer), ("png", file bytes) for LVGL to decode, or
    ("none", None) when neither the tile nor an ancestor is on the card. want() names the
    tiles a view needs, most wanted first; load_next() does one piece of work (the loader
    thread calls it in a loop, tests call it directly); on_ready(key) follows each tile that
    came in. After a zoom in, the wanted tiles first get a preview stretched from a cached
    ancestor (no card read, ~15 ms each), then their real tiles.

    `read_file(path)` returns a file's bytes or raises OSError (ENOENT: no such tile).

    No lock: MicroPython runs scheduled LVGL work (the map's timer, which calls get()) between
    any two bytecodes of whichever thread runs, the loader included, so a lock held by the
    loader would be waited for by its own thread. State shared with the UI changes in single
    steps (one assignment, one list or dict call), which the GIL keeps whole; the worst a race
    does is drop a tile early, and the view asks for it again."""

    def __init__(self, root, read_file, capacity=16, on_ready=None):
        self.root = root
        self.read_file = read_file
        self.capacity = capacity
        self.on_ready = on_ready
        self.status = "ok"              # "nocard" once the card is gone; retry() to go on
        self._cache = {}                # key -> entry
        self._order = []                # keys, least recent first (the loader evicts)
        self._missing = set()           # keys with no file on the card (loader only)
        self._wanted = []               # replaced whole by want(), never edited in place
        self._free = []                 # RGB565 buffers of evicted tiles (loader only)
        self._scratch = bytearray(_ROW * TILE)
        self._running = False
        self._paused = False
        self._give_way = None           # set by start(): let the UI thread run

    # --- for the view -------------------------------------------------------- #
    def want(self, keys):
        cache = self._cache
        self._wanted = [k for k in keys if k not in cache]

    def is_preview(self, key):
        entry = self._cache.get(key)
        return entry is not None and len(entry) > 2

    def get(self, key):
        entry = self._cache.get(key)
        if entry is not None:
            order = self._order
            try:
                order.remove(key)
            except ValueError:
                pass
            order.append(key)
        return entry

    def pause(self, on):
        """Paused (a finger on the map), the loader takes nothing new."""
        self._paused = on

    def retry(self):
        """The card may be back: look again for what is wanted."""
        self._missing = set()
        self.status = "ok"

    def start(self):
        import _thread
        import time
        if not self._running:
            self._running = True
            self._give_way = lambda: time.sleep_ms(0)
            _thread.start_new_thread(self._loop, ())

    def stop(self):
        self._running = False

    # --- loading ------------------------------------------------------------- #
    def _loop(self):
        import time
        while self._running:
            try:
                if not self.load_next():
                    time.sleep_ms(30)
            except Exception as e:
                print("map tiles: loader error:", repr(e))
                time.sleep_ms(200)

    def load_next(self):
        if self.status != "ok" or self._paused:
            return False
        wanted, cache = self._wanted, self._cache
        for k in wanted:
            if k not in cache and self._preview(k):
                return True
        key = None
        for k in wanted:
            entry = cache.get(k)
            if entry is None or len(entry) > 2:
                key = k
                break
        if key is None:
            return False
        try:
            entry = self._load(key)
        except OSError as e:
            if e.args and e.args[0] == errno.ENODEV:
                self.status = "nocard"
            else:
                self._wanted = [k for k in self._wanted if k != key]   # passing error
            return True
        self._put(key, entry)
        if self.on_ready is not None:
            self.on_ready(key)
        return True

    def _preview(self, key):
        z, x, y = key
        for up in range(1, min(PREVIEW_UP, z) + 1):
            entry = self._cache.get((z - up, x >> up, y >> up))
            if entry is not None and entry[0] == "rgb565":
                self._put(key, ("rgb565", self._stretched(entry[1], x, y, up), True))
                if self.on_ready is not None:
                    self.on_ready(key)
                return True
        return False

    def _stretched(self, src, x, y, up):
        mask = (1 << up) - 1
        size = TILE >> up
        buf = self._free.pop() if self._free else bytearray(TILE_BYTES)
        _bands(_stretch, self._give_way, buf, src, (x & mask) * size, (y & mask) * size, up)
        return buf

    def _read(self, key):
        """The tile's file bytes, or None when the card has no such tile."""
        if key in self._missing:
            return None
        try:
            return self.read_file("%s/%d/%d/%d.png" % (self.root, key[0], key[1], key[2]))
        except OSError as e:
            if e.args and e.args[0] == errno.ENOENT:
                self._missing.add(key)
                return None
            raise

    def _decoded(self, data):
        buf = self._free.pop() if self._free else bytearray(TILE_BYTES)
        if decode_png(data, buf, self._scratch, self._give_way):
            return ("rgb565", buf)
        self._free.append(buf)
        return ("png", data)

    def _load(self, key):
        data = self._read(key)
        if data is not None:
            return self._decoded(data)
        z, x, y = key
        for up in range(1, z + 1):
            akey = (z - up, x >> up, y >> up)
            entry = self._cache.get(akey)
            if entry is None:
                data = self._read(akey)
                if data is None:
                    continue
                entry = self._decoded(data)
                self._put(akey, entry)
            if entry[0] != "rgb565":
                return ("none", None)   # a PNG only LVGL can read: not stretched
            return ("rgb565", self._stretched(entry[1], x, y, up))
        return ("none", None)

    def _put(self, key, entry):
        old = self._cache.get(key)
        if old is not None and old[0] == "rgb565" and old[1] is not entry[1]:
            self._free.append(old[1])   # the preview this tile replaces
        self._cache[key] = entry
        self.get(key)                   # most recent
        order = self._order
        while len(order) > self.capacity:
            old = self._cache.pop(order.pop(0), None)
            if old is not None and old[0] == "rgb565":
                self._free.append(old[1])
