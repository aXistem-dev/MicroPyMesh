"""A whole map in one PMTiles (version 3) file on the SD card, read in place.

The file starts with a 127-byte header; a root directory (and, for large maps, leaf
directories) maps tile ids to byte ranges of the tile data. A tile id numbers the tiles of all
zoom levels along a Hilbert curve per level. Directories are varint lists, usually gzipped;
the tiles themselves are PNGs here. Only the directories needed are read, and the leaf ones
are kept, so a tile costs one read once its directory is known.

`read_at(offset, length)` returns those bytes of the file. The map draws picture (PNG) tiles
only: an archive of vector, JPEG, WebP or AVIF tiles, or with brotli/zstd directories, gets a
`problem` saying so and gives no tiles."""

import struct

HEADER_LEN = 127
MAX_DEPTH = 4                   # root + leaves; the spec's writers use at most root + 1
LEAVES_KEPT = 8
_NONE, _GZIP = 1, 2
_TILE_TYPES = {0: "tiles of an unknown type", 1: "vector tiles", 3: "JPEG tiles",
               4: "WebP tiles", 5: "AVIF tiles"}
_COMPRESSIONS = {0: "an unknown compression", 3: "brotli", 4: "zstd"}


def zxy_to_tileid(z, x, y):
    """Tiles of the levels above z come first, then this level along its Hilbert curve."""
    tid = ((1 << (2 * z)) - 1) // 3
    n = 1 << z
    s = n >> 1
    while s:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        tid += s * s * ((3 * rx) ^ ry)
        if not ry:
            if rx:
                x = n - 1 - x
                y = n - 1 - y
            x, y = y, x
        s >>= 1
    return tid


def _gunzip(data):
    try:
        import deflate
        import io
        return deflate.DeflateIO(io.BytesIO(data), deflate.GZIP).read()
    except ImportError:         # CPython
        import zlib
        return zlib.decompress(bytes(data), 31)


def _directory(data):
    """The entries as four lists: tile ids, run lengths, lengths, offsets."""
    pos = 0

    def varint():
        nonlocal pos
        n = shift = 0
        while True:
            b = data[pos]
            pos += 1
            n |= (b & 0x7F) << shift
            if b < 0x80:
                return n
            shift += 7
    count = varint()
    ids = []
    last = 0
    for _ in range(count):
        last += varint()
        ids.append(last)
    runs = [varint() for _ in range(count)]
    lengths = [varint() for _ in range(count)]
    offsets = []
    for i in range(count):
        v = varint()
        offsets.append(offsets[i - 1] + lengths[i - 1] if v == 0 and i else v - 1)
    return ids, runs, lengths, offsets


def _find(d, tid):
    """Index of the entry holding `tid` (a run that covers it, or a leaf directory that may),
    or -1."""
    ids, runs = d[0], d[1]
    lo, hi = 0, len(ids) - 1
    while lo <= hi:
        mid = (lo + hi) >> 1
        if ids[mid] < tid:
            lo = mid + 1
        elif ids[mid] > tid:
            hi = mid - 1
        else:
            return mid
    if hi >= 0 and (runs[hi] == 0 or tid - ids[hi] < runs[hi]):
        return hi
    return -1


def _problem(head):
    if len(head) < HEADER_LEN or head[:7] != b"PMTiles":
        return "not a PMTiles file"
    if head[7] != 3:
        return "PMTiles version %d: only version 3 can be read" % head[7]
    if head[97] not in (_NONE, _GZIP):
        return "its directories use %s: only gzip or none can be read" % _COMPRESSIONS.get(
            head[97], "compression %d" % head[97])
    if head[99] != 2:
        return "%s: only picture (PNG) maps can be drawn" % _TILE_TYPES.get(
            head[99], "tile type %d" % head[99])
    if head[98] not in (0, _NONE, _GZIP):
        return "its tiles use %s: only gzip or none can be read" % _COMPRESSIONS.get(
            head[98], "compression %d" % head[98])
    return None


class PMTiles:
    def __init__(self, read_at):
        self.read_at = read_at
        self._root = None
        self._leaves = {}           # offset -> directory
        self._leaf_order = []
        try:
            head = read_at(0, HEADER_LEN)
        except OSError as e:
            head = b""
            self.problem = "could not be read (%s)" % (e,)
        else:
            self.problem = _problem(head)
        if self.problem:
            return
        (self.root_offset, self.root_length, self.meta_offset, self.meta_length,
         self.leaf_offset, _, self.data_offset) = struct.unpack_from("<QQQQQQQ", head, 8)
        self.internal = head[97]
        self.tile_compression = head[98]
        self.min_zoom, self.max_zoom = head[100], head[101]

    def _unpack(self, data):
        return _gunzip(data) if self.internal == _GZIP else data

    def _leaf(self, offset, length):
        d = self._leaves.get(offset)
        if d is None:
            d = _directory(self._unpack(self.read_at(self.leaf_offset + offset, length)))
            self._leaves[offset] = d
            self._leaf_order.append(offset)
            if len(self._leaf_order) > LEAVES_KEPT:
                self._leaves.pop(self._leaf_order.pop(0), None)
        return d

    def tile(self, z, x, y):
        """The tile's bytes, or None when the archive has no such tile."""
        if self.problem or not 0 <= x < (1 << z) or not 0 <= y < (1 << z):
            return None
        tid = zxy_to_tileid(z, x, y)
        if self._root is None:
            self._root = _directory(self._unpack(self.read_at(self.root_offset,
                                                              self.root_length)))
        d = self._root
        for _ in range(MAX_DEPTH):
            i = _find(d, tid)
            if i < 0:
                return None
            if d[1][i]:
                data = self.read_at(self.data_offset + d[3][i], d[2][i])
                return _gunzip(data) if self.tile_compression == _GZIP else data
            d = self._leaf(d[3][i], d[2][i])
        return None

    def credit(self):
        """The metadata's attribution without its HTML, or None."""
        if self.problem or not self.meta_length:
            return None
        try:
            import json
            meta = json.loads(self._unpack(self.read_at(self.meta_offset, self.meta_length)))
            text = meta.get("attribution") or ""
        except Exception:
            return None
        out = ""
        tag = False
        for ch in text:
            if ch == "<":
                tag = True
            elif ch == ">":
                tag = False
            elif not tag:
                out += ch
        return " ".join(out.split()) or None


def open_file(path):
    """A PMTiles reader on a file, kept open; close() lets it go."""
    f = open(path, "rb")

    def read_at(offset, length):
        f.seek(offset)
        return f.read(length)
    archive = PMTiles(read_at)
    archive.close = f.close
    return archive
