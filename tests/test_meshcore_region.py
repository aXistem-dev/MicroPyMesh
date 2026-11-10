"""Region scopes (MeshCore TransportKeyStore): a region's key is SHA256("#" + name)[:16];
a scoped flood carries HMAC-SHA256(key, payload type + payload)[:2] as its first transport
code, so repeaters that know the region can tell it is theirs.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_region.py
"""

import hashlib
import hmac
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402,F401

import meshcore_region as R  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def test_key_is_sha256_of_hash_name():
    _assert(R.region_key("be-wvl") == hashlib.sha256(b"#be-wvl").digest()[:16], "key")
    _assert(R.region_key("#be-wvl") == R.region_key("be-wvl"), "no double #")
    _assert(R.region_key("BE") != R.region_key("be"), "case matters")


def test_transport_code_is_the_first_two_hmac_bytes():
    key = R.region_key("be")
    payload = bytes(range(40))
    mac = hmac.new(key, bytes([5]) + payload, hashlib.sha256).digest()
    _assert(R.transport_code(key, 5, payload) == struct.unpack("<H", mac[:2])[0], "code")


def test_the_two_reserved_codes_are_avoided():
    _assert(R._fix(0) == 1 and R._fix(0xFFFF) == 0xFFFE and R._fix(1234) == 1234, "fix")


def test_names_are_cleaned():
    _assert(R.clean_name("  #Be-WVL ") == "Be-WVL", R.clean_name("  #Be-WVL "))
    for bad in ("", "#", "a b", "x" * 31, "$private"):
        _assert(R.clean_name(bad) is None, bad)


def test_the_region_of_a_received_packet_is_found_by_trying_known_keys():
    payload = b"hello mesh"
    code = R.transport_code(R.region_key("be-wvl"), 5, payload)
    _assert(R.match_region(code, 5, payload, ["be", "be-wvl", "fr"]) == "be-wvl", "match")
    _assert(R.match_region(code, 5, payload, ["be", "fr"]) is None, "unknown")


if __name__ == "__main__":
    fake_mpos.run_all(globals())
