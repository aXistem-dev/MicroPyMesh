"""Talking to repeaters and room servers: login, requests (status, neighbours, telemetry,
owner info) and their replies, and trace. Payload layouts follow the MeshCore firmware
(examples/simple_repeater, examples/simple_room_server, src/Mesh.cpp); integers are
little-endian except CayenneLPP values. Pure functions; the manager does the radio side.
"""

import os
import struct

from meshcore_dm import encrypt_then_mac

REQ_GET_STATUS = 0x01
REQ_KEEP_ALIVE = 0x02
REQ_GET_TELEMETRY = 0x03
REQ_GET_ACCESS_LIST = 0x05
REQ_GET_NEIGHBOURS = 0x06
REQ_GET_OWNER_INFO = 0x07

RESP_LOGIN_OK = 0
PERM_ROLE_MASK = 3
_ROLES = ("guest", "read-only", "read-write", "admin")
MAX_PASSWORD = 15
PATH_EXTRA_RESPONSE = 0x01    # a login reply riding in a PATH return (== PAYLOAD_TYPE_RESPONSE)


# --- login ------------------------------------------------------------------- #

def build_login(secret, our_pub, dest_hash, timestamp, password, sync_since=None):
    """ANON_REQ payload: dest hash, our full public key (the server derives the secret from
    it), then the encrypted timestamp, [room: sync_since] and password. A blank password is
    a guest login (or a client the server already knows)."""
    pt = struct.pack("<I", timestamp & 0xFFFFFFFF)
    if sync_since is not None:
        pt += struct.pack("<I", sync_since & 0xFFFFFFFF)
    pt += (password or "").encode("utf-8")[:MAX_PASSWORD]
    return bytes([dest_hash & 0xFF]) + bytes(our_pub) + encrypt_then_mac(secret, pt)


def parse_login_reply(body):
    """13-byte login reply (or the legacy "OK" text). None if it is not a login reply."""
    body = bytes(body)
    if len(body) >= 6 and body[4:6] == b"OK":
        return {"ok": True, "server_ts": struct.unpack("<I", body[:4])[0], "admin": False,
                "permissions": 0, "role": "guest", "fw_level": 0, "keep_alive_s": 0}
    if len(body) < 8 or body[4] != RESP_LOGIN_OK:
        return None
    ts, _, keep_alive, flag, perms = struct.unpack("<IBBBB", body[:8])
    role = _ROLES[perms & PERM_ROLE_MASK]
    if flag == 2:                  # room: guest (no permissions)
        role = "guest"
    return {"ok": True, "server_ts": ts, "admin": flag == 1, "permissions": perms,
            "role": role, "fw_level": body[12] if len(body) > 12 else 0,
            "keep_alive_s": keep_alive * 16}    # 0 from current firmware (a legacy field)


# --- requests ------------------------------------------------------------------ #

def build_request(secret, our_pub, dest_hash, tag, req_type, params=None):
    """REQ payload: dest + src hash, encrypted tag (a fresh timestamp), type and params.
    Requests without params carry 4 zero and 4 random bytes, as the companion firmware does."""
    if params is None:
        params = bytes(4) + os.urandom(4)
    pt = struct.pack("<IB", tag & 0xFFFFFFFF, req_type) + bytes(params)
    return bytes([dest_hash & 0xFF, our_pub[0]]) + encrypt_then_mac(secret, pt)


def neighbours_params(count=20, offset=0, order=0, prefix_len=4):
    """order: 0 newest, 1 oldest, 2 strongest, 3 weakest."""
    return struct.pack("<BBHBB", 0, count, offset, order, prefix_len) + os.urandom(4)


def parse_response(plaintext):
    """RESPONSE plaintext -> (tag, body)."""
    pt = bytes(plaintext)
    if len(pt) < 4:
        return None, b""
    return struct.unpack("<I", pt[:4])[0], pt[4:]


_STATUS = (  # (offset, format, name); room servers stop after "flood_dups" and add two fields
    (0, "H", "battery_mv"), (2, "H", "tx_queue"), (4, "h", "noise_floor"),
    (6, "h", "last_rssi"), (8, "I", "packets_rx"), (12, "I", "packets_tx"),
    (16, "I", "airtime_s"), (20, "I", "uptime_s"), (24, "I", "flood_tx"),
    (28, "I", "direct_tx"), (32, "I", "flood_rx"), (36, "I", "direct_rx"),
    (40, "H", "errors"), (42, "h", "last_snr"), (44, "H", "direct_dups"),
    (46, "H", "flood_dups"))
_REPEATER_TAIL = ((48, "I", "rx_airtime_s"), (52, "I", "receive_errors"))
_ROOM_TAIL = ((48, "H", "posted"), (50, "H", "post_pushes"))


def parse_status(body, room=False):
    """GET_STATUS reply body -> dict of the fields it holds (older firmware sends fewer)."""
    body = bytes(body)
    out = {}
    for off, fmt, name in _STATUS + (_ROOM_TAIL if room else _REPEATER_TAIL):
        size = struct.calcsize("<" + fmt)
        if off + size > len(body):
            break
        out[name] = struct.unpack("<" + fmt, body[off:off + size])[0]
    if "last_snr" in out:
        out["last_snr"] = out["last_snr"] / 4
    return out


def parse_neighbours(body, prefix_len=4):
    """GET_NEIGHBOURS reply -> (total, [{prefix, secs_ago, snr}])."""
    body = bytes(body)
    if len(body) < 4:
        return 0, []
    total, n = struct.unpack("<hh", body[:4])
    rows = []
    i = 4
    for _ in range(max(0, n)):
        if i + prefix_len + 5 > len(body):
            break
        prefix = body[i:i + prefix_len].hex()
        secs, snr = struct.unpack("<Ib", body[i + prefix_len:i + prefix_len + 5])
        rows.append({"prefix": prefix, "secs_ago": secs, "snr": snr / 4})
        i += prefix_len + 5
    return total, rows


# CayenneLPP: type -> (name, size, decoder)
def _u(b):
    return int.from_bytes(b, "big")


def _s(b):
    v = int.from_bytes(b, "big")
    return v - (1 << (8 * len(b))) if v & (1 << (8 * len(b) - 1)) else v


_LPP = {
    0: ("digital_in", 1, lambda b: b[0]),
    1: ("digital_out", 1, lambda b: b[0]),
    2: ("analog_in", 2, lambda b: _s(b) / 100),
    3: ("analog_out", 2, lambda b: _s(b) / 100),
    101: ("illuminance", 2, _u),
    102: ("presence", 1, lambda b: b[0]),
    103: ("temperature", 2, lambda b: _s(b) / 10),
    104: ("humidity", 1, lambda b: b[0] / 2),
    115: ("pressure", 2, lambda b: _u(b) / 10),
    116: ("voltage", 2, lambda b: _u(b) / 100),
    117: ("current", 2, lambda b: _u(b) / 1000),
    118: ("frequency", 4, _u),
    120: ("percentage", 1, lambda b: b[0]),
    121: ("altitude", 2, _s),
    125: ("concentration", 2, _u),
    128: ("power", 2, _u),
    130: ("distance", 4, lambda b: _u(b) / 1000),
    131: ("energy", 4, lambda b: _u(b) / 1000),
    136: ("gps", 9, lambda b: (_s(b[0:3]) / 10000, _s(b[3:6]) / 10000, _s(b[6:9]) / 100)),
}


def parse_lpp(body):
    """CayenneLPP telemetry -> [{channel, kind, value}]. Stops at padding or an unknown type
    (its size is not known, so nothing after it can be read)."""
    body = bytes(body)
    rows = []
    i = 0
    while i + 2 <= len(body):
        ch, typ = body[i], body[i + 1]
        spec = _LPP.get(typ)
        if spec is None or (ch == 0 and typ == 0):
            break
        name, size, dec = spec
        if i + 2 + size > len(body):
            break
        value = dec(body[i + 2:i + 2 + size])
        if isinstance(value, float):
            value = round(value, 4)
        rows.append({"channel": ch, "kind": name, "value": value})
        i += 2 + size
    return rows


def parse_owner_info(body):
    parts = bytes(body).split(b"\x00")[0].decode("utf-8").split("\n")
    parts += [""] * (3 - len(parts))
    return {"firmware": parts[0], "name": parts[1], "owner": parts[2]}


# --- trace ------------------------------------------------------------------- #

_HASH_FLAGS = {1: 0, 2: 1, 4: 2, 8: 3}


def build_trace(tag, auth, hashes, hash_size=1):
    """TRACE payload: tag, auth (unchecked by the firmware), flags (hash size), the hop
    hashes the packet must follow. Sent direct with an empty header path."""
    return struct.pack("<IIB", tag & 0xFFFFFFFF, auth & 0xFFFFFFFF,
                       _HASH_FLAGS[hash_size]) + bytes(hashes)


def parse_trace(payload, snr_path=b"", final_snr=None):
    """A TRACE that came back: payload + the header path (one SNR×4 byte per hop)."""
    payload = bytes(payload)
    tag, auth, flags = struct.unpack("<IIB", payload[:9])
    size = 1 << (flags & 3)
    raw = payload[9:]
    hashes = [int.from_bytes(raw[i:i + size], "big") for i in range(0, len(raw) - size + 1, size)]
    snrs = [(b - 256 if b > 127 else b) / 4 for b in bytes(snr_path)]
    return {"tag": tag, "auth": auth, "hash_size": size, "hashes": hashes,
            "hop_snrs": snrs, "final_snr": final_snr}


# --- timing -------------------------------------------------------------------- #

def timeout_ms(airtime_ms, hops=None):
    """How long to wait for a reply (companion firmware): flood when hops is None."""
    if hops is None:
        return 500 + 16 * airtime_ms
    return 500 + (6 * airtime_ms + 250) * (hops + 1)
