"""A map from one PMTiles (v3) file: tiles found through the archive's directories (also leaf
directories and runs of identical tiles), its credit from the metadata, and archives the map
cannot draw refused with the reason. The tile store reads from it as from the style folders.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_map_pmtiles.py
"""

import gzip
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402,F401

import map_pmtiles as P  # noqa: E402
import map_tiles as MT  # noqa: E402
from test_map_tiles import png, px, rgb565, PAL, STRIPES  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _directory(entries):
    """entries: [(tile_id, offset, length, run_length)], sorted by tile_id."""
    out = _varint(len(entries))
    last = 0
    for tid, _, _, _ in entries:
        out += _varint(tid - last)
        last = tid
    out += b"".join(_varint(e[3]) for e in entries)
    out += b"".join(_varint(e[2]) for e in entries)
    for i, (_, off, ln, _) in enumerate(entries):
        prev = entries[i - 1] if i else None
        out += _varint(0 if prev and off == prev[1] + prev[2] else off + 1)
    return out


def archive(tiles, tile_type=2, internal=2, metadata=None, leaf_after=None, version=3):
    """A PMTiles file. tiles: {(z, x, y): bytes}; equal neighbours become one run. With
    `leaf_after`, the entries from that index on go into a leaf directory."""
    pack = (lambda b: gzip.compress(b)) if internal == 2 else (lambda b: b)
    data = b""
    entries = []
    for key in sorted(tiles, key=lambda k: P.zxy_to_tileid(*k)):
        tid = P.zxy_to_tileid(*key)
        t = tiles[key]
        last = entries[-1] if entries else None
        if last and last[0] + last[3] == tid and data[last[1]:last[1] + last[2]] == t:
            entries[-1] = (last[0], last[1], last[2], last[3] + 1)
            continue
        entries.append((tid, len(data), len(t), 1))
        data += t
    leaves = b""
    if leaf_after is not None:
        leaf = pack(_directory(entries[leaf_after:]))
        entries = entries[:leaf_after] + [(entries[leaf_after][0], 0, len(leaf), 0)]
        leaves = leaf
    root = pack(_directory(entries))
    meta = pack(json.dumps(metadata or {}).encode())
    root_off = 127
    meta_off = root_off + len(root)
    leaf_off = meta_off + len(meta)
    data_off = leaf_off + len(leaves)
    head = b"PMTiles" + bytes([version])
    head += struct.pack("<QQQQQQQQQQQ", root_off, len(root), meta_off, len(meta),
                        leaf_off, len(leaves), data_off, len(data), len(tiles), 0, 0)
    head += bytes([1, internal, 1, tile_type, 0, 14])
    head += struct.pack("<iiii", 25000000, 495000000, 65000000, 515000000)
    head += bytes([8]) + struct.pack("<ii", 46700000, 506400000)
    assert len(head) == 127
    return head + root + meta + leaves + data


def reader(blob):
    reads = []

    def read_at(offset, length):
        reads.append((offset, length))
        return blob[offset:offset + length]
    read_at.reads = reads
    return read_at


def test_tile_ids_follow_the_hilbert_curve():
    # The spec's vectors, also what the reference pmtiles library gives.
    for zxy, tid in (((0, 0, 0), 0), ((1, 0, 0), 1), ((1, 0, 1), 2), ((1, 1, 1), 3),
                     ((1, 1, 0), 4), ((2, 0, 0), 5), ((12, 3423, 1763), 19078479),
                     ((20, 567890, 345678), 1302250485581)):
        _assert(P.zxy_to_tileid(*zxy) == tid, (zxy, P.zxy_to_tileid(*zxy), tid))


def test_tiles_are_found_and_a_missing_one_is_none():
    blob = archive({(0, 0, 0): b"world", (1, 0, 0): b"nw", (1, 1, 0): b"ne", (1, 1, 1): b"se"})
    a = P.PMTiles(reader(blob))
    _assert(a.problem is None, a.problem)
    _assert(a.tile(0, 0, 0) == b"world" and a.tile(1, 1, 0) == b"ne" and a.tile(1, 1, 1) == b"se")
    _assert(a.tile(1, 0, 1) is None and a.tile(9, 3, 3) is None)


def test_a_run_of_identical_tiles_is_one_entry():
    sea = b"sea tile"
    blob = archive({(2, 0, 0): sea, (2, 1, 0): sea, (2, 1, 1): sea, (2, 0, 1): b"land"})
    a = P.PMTiles(reader(blob))
    for xy in ((0, 0), (1, 0), (1, 1)):
        _assert(a.tile(2, *xy) == sea, xy)
    _assert(a.tile(2, 0, 1) == b"land")


def test_leaf_directories_are_followed_and_kept():
    tiles = {(3, x, y): b"t%d%d" % (x, y) for x in range(4) for y in range(4)}
    blob = archive(tiles, leaf_after=5)
    rd = reader(blob)
    a = P.PMTiles(rd)
    for key, t in tiles.items():
        _assert(a.tile(*key) == t, key)
    n = len(rd.reads)
    a.tile(3, 3, 3)
    _assert(len(rd.reads) == n + 1, "the leaf directory is read once, then only the tile")


def test_uncompressed_directories_and_the_credit():
    blob = archive({(0, 0, 0): b"w"}, internal=1,
                   metadata={"attribution": "<a href='x'>© OpenStreetMap</a> contributors"})
    a = P.PMTiles(reader(blob))
    _assert(a.tile(0, 0, 0) == b"w")
    _assert(a.credit() == "© OpenStreetMap contributors", a.credit())
    _assert(P.PMTiles(reader(archive({(0, 0, 0): b"w"}))).credit() is None)


def test_archives_the_map_cannot_draw_say_why():
    for blob, word in ((archive({(0, 0, 0): b"w"}, tile_type=1), "vector"),
                       (archive({(0, 0, 0): b"w"}, tile_type=3), "JPEG"),
                       (archive({(0, 0, 0): b"w"}, tile_type=4), "WebP"),
                       (archive({(0, 0, 0): b"w"}, internal=3), "brotli"),
                       (archive({(0, 0, 0): b"w"}, version=2), "version 2"),
                       (b"not an archive" * 20, "not a PMTiles file"),
                       (b"short", "not a PMTiles file")):
        a = P.PMTiles(reader(blob))
        _assert(a.problem and word in a.problem, (word, a.problem))
        _assert(a.tile(0, 0, 0) is None)


def test_the_store_reads_tiles_from_the_archive():
    blob = archive({(14, 8344, 5487): png(STRIPES, PAL)})
    s = MT.TileStore(None, None, source=P.PMTiles(reader(blob)))
    s.want([(14, 8344, 5487)])
    _assert(s.load_next(), "loaded one")
    kind, buf = s.get((14, 8344, 5487))
    _assert(kind == "rgb565" and px(buf, 130, 0) == rgb565(PAL[2]), kind)
    s.want([(15, 16689, 10975)])                  # past the archive: stretched from above
    _assert(s.load_next())
    _assert(s.get((15, 16689, 10975))[0] == "rgb565")


if __name__ == "__main__":
    fake_mpos.run_all(globals())
