"""Region scopes (MeshCore TransportKeyStore / RegionMap).

A public region "#name" has the key SHA256("#name")[:16]. A flood sent in that region is
route TRANSPORT_FLOOD with transport codes (code, 0), where code is the first two bytes of
HMAC-SHA256(key, payload type + payload) read as little-endian. Repeaters forward a scoped
flood only for a region they allow. Private "$" regions need a key store and are not used."""

import hashlib
import struct

MAX_NAME = 30                   # the firmware keeps names in 31 bytes, NUL included
_OK = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."


def clean_name(text):
    """A region name without its "#", or None when it cannot be one."""
    name = (text or "").strip()
    if name.startswith("#"):
        name = name[1:]
    if not name or len(name) > MAX_NAME or any(c not in _OK for c in name):
        return None
    return name


def region_key(name):
    name = name if name.startswith("#") else "#" + name
    return hashlib.sha256(name.encode()).digest()[:16]


def _hmac_sha256(key, msg):
    block = 64
    key = key + bytes(block - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key) + msg).digest()
    return hashlib.sha256(bytes(b ^ 0x5C for b in key) + inner).digest()


def _fix(code):
    # 0000 and FFFF are reserved: the firmware moves them one step inwards
    return 1 if code == 0 else 0xFFFE if code == 0xFFFF else code


def transport_code(key, payload_type, payload):
    mac = _hmac_sha256(key, bytes([payload_type]) + bytes(payload))
    return _fix(struct.unpack("<H", mac[:2])[0])


def match_region(code, payload_type, payload, names):
    """The one known region whose key gives `code`; None when none or several do."""
    hits = [n for n in names if transport_code(region_key(n), payload_type, payload) == code]
    return hits[0] if len(hits) == 1 else None
