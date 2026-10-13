"""Our identity: exporting the key pair to a file (the SD card), and replacing it with a new
one, which forgets everything derived from the old key.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_identity.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    return env, m


def test_export_writes_the_key_pair_to_a_file():
    env, m = _setup()
    pub, prv = m.get_identity()
    folder = tempfile.mkdtemp()
    ok, path = m.export_identity(folder)
    _assert(ok and path == os.path.join(folder, "meshcore-identity-%s.json" % pub.hex()[:8]), path)
    data = json.load(open(path))
    _assert(data["public_key"] == pub.hex() and data["private_key"] == prv.hex(), data)
    _assert(data["name"] == m.nickname(), data)


def test_export_without_a_card_says_why():
    env, m = _setup()
    ok, err = m.export_identity("/no/such/card")
    _assert(not ok and err, err)


def test_a_new_identity_forgets_what_the_old_key_made():
    env, m = _setup()
    old_pub, _ = m.get_identity()
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    m.add_contact(peer_pub.hex(), "Alex")
    m._contacts[peer_pub.hex()]["secret"] = b"x" * 32
    m._sessions["ab" * 32] = {"state": "in"}
    _assert(m.nickname() == "MC-%s" % old_pub.hex()[:4].upper(), m.nickname())
    new_pub = m.new_identity()
    _assert(new_pub and new_pub != old_pub and m.get_identity()[0] == new_pub, "new key")
    _assert(m._contacts[peer_pub.hex()]["secret"] is None, "secret forgotten")
    _assert(m._sessions == {}, "logins forgotten")
    _assert(m.nickname() == "MC-%s" % new_pub.hex()[:4].upper(), "automatic name follows")


def test_a_chosen_name_stays_with_a_new_identity():
    env, m = _setup()
    m.set_nickname("Kim")
    m.new_identity()
    _assert(m.nickname() == "Kim", m.nickname())


def test_the_sd_card_folder_comes_from_the_os():
    import meshcore_manager as mm
    env, m = _setup()
    _assert(mm.sd_root() == "/sdcard", "fallback without an SD card manager")

    class SDCardManager:
        @staticmethod
        def get_mount_point():
            return "/sd/"

    sys.modules["mpos"].SDCardManager = SDCardManager
    try:
        _assert(mm.sd_root() == "/sd", mm.sd_root())
    finally:
        del sys.modules["mpos"].SDCardManager


if __name__ == "__main__":
    fake_mpos.run_all(globals())
