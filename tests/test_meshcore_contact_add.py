"""Adding a contact by hand: a 64-hex public key, or a meshcore:// contact card (as the
MeshCore apps share them).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_contact_add.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402,F401

import meshcore_advert as A  # noqa: E402

KEY = "5e1f0c3a9b8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f"


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def test_a_bare_key():
    _assert(A.parse_contact_text(KEY) == (KEY, None, 1), A.parse_contact_text(KEY))
    spaced = " ".join(KEY[i:i + 8].upper() for i in range(0, 64, 8))
    _assert(A.parse_contact_text(spaced) == (KEY, None, 1), "groups and capitals")


def test_a_contact_card_round_trips():
    uri = A.contact_share_uri("Repeater Noord \\u263c", KEY, 2)
    _assert(A.parse_contact_text(uri) == (KEY, "Repeater Noord \\u263c", 2), A.parse_contact_text(uri))


def test_not_a_contact():
    for text in ("", "hello", KEY[:-2], KEY + "00", "meshcore://contact/add?name=x",
                 "meshcore://channel/add?name=x&public_key=" + KEY):
        _assert(A.parse_contact_text(text) is None, text)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
