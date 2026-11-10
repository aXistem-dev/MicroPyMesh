"""Backup of contacts and channels to a file (the SD card), and adding them back: what is
missing comes back, what is there stays as it is. The private key is not in it.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_backup.py
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


GENT = "f1" + "33" * 31
BOB = "b0" + "22" * 31


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    m.add_contact(BOB, "Bob")
    m.add_contact(GENT, "Gent-Noord", node_type=2)
    m.set_favourite(GENT, True)
    m.add_channel("#test")
    m.add_channel("Family", "00112233445566778899aabbccddeeff")
    return env, m


def _fresh(env):
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    env2 = fake_mpos.install()
    m = fake_mpos.new_manager(env2)
    m.generate_identity()
    return m


def test_backup_holds_contacts_and_channels_but_no_private_key():
    env, m = _setup()
    folder = tempfile.mkdtemp()
    ok, path = m.export_backup(folder)
    _assert(ok and os.path.basename(path).startswith("micropymesh-backup-"), path)
    with open(path) as f:
        data = json.load(f)
    _assert(sorted(c["pubkey"] for c in data["contacts"]) == sorted([BOB, GENT]), data)
    names = [c["name"] for c in data["channels"]]
    _assert("#test" in names and "Family" in names, names)
    _assert("private_key" not in json.dumps(data) and m.get_identity()[1].hex() not in json.dumps(data))
    _assert(m.backup_files(folder) == [os.path.basename(path)])


def test_restore_adds_what_is_missing():
    env, m = _setup()
    folder = tempfile.mkdtemp()
    ok, path = m.export_backup(folder)
    m2 = _fresh(env)
    m2.add_contact(BOB, "Bobby")                  # already there: stays as it is
    ok, info = m2.import_backup(path)
    _assert(ok, info)
    _assert(info == {"contacts": 1, "channels": 2}, info)
    _assert(m2.is_contact(GENT) and m2.get_contact(GENT)["type"] == 2 and m2.is_favourite(GENT))
    _assert(m2.get_contact(BOB)["name"] == "Bobby", "kept")
    _assert(m2.channel_key_hex("Family") == "00112233445566778899aabbccddeeff")
    _assert(m2.get_channel("#test") is not None)
    ok, info = m2.import_backup(path)
    _assert(ok and info == {"contacts": 0, "channels": 0}, info)


def test_restore_refuses_other_files():
    env, m = _setup()
    path = os.path.join(tempfile.mkdtemp(), "x.json")
    with open(path, "w") as f:
        f.write('{"hello": 1}')
    ok, err = m.import_backup(path)
    _assert(not ok and err, err)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
