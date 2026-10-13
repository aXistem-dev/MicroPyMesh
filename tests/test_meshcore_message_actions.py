"""Deleting one message, or a whole chat's history, from a channel or a DM; both are saved.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_message_actions.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    events = []
    m.add_subscriber(lambda e, d: events.append((e, d[0] if isinstance(d, tuple) else d)))
    return env, m, events


def _restart(env):
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    return fake_mpos.new_manager(env)


def test_delete_one_channel_message():
    env, m, events = _setup()
    for t in ("one", "two", "three"):
        m._add_message("Public", {"ts": 1790000000, "sender": "Sam", "text": t, "incoming": True})
    target = m.get_messages("Public")[1]
    _assert(m.delete_message("Public", target), "deleted")
    _assert([x["text"] for x in m.get_messages("Public")] == ["one", "three"], "gone")
    _assert(("message", "Public") in events, events)
    m._flush_dirty(force=True)
    _assert([x["text"] for x in _restart(env).get_messages("Public")] == ["one", "three"], "saved")


def test_delete_a_dm_and_clear_a_chat():
    env, m, events = _setup()
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    pk = peer_pub.hex()
    m.add_contact(pk, "Alex")
    for t in ("a", "b"):
        m._add_dm(pk, {"ts": 1790000000, "sender": "Alex", "text": t, "incoming": True})
    _assert(m.delete_message(pk, m.get_dm_messages(pk)[0]), "dm deleted")
    _assert([x["text"] for x in m.get_dm_messages(pk)] == ["b"], "dm gone")
    m.clear_history(pk)
    _assert(m.get_dm_messages(pk) == [], "cleared")
    m._flush_dirty(force=True)
    _assert(_restart(env).get_dm_messages(pk) == [], "saved")


def test_deleting_an_unknown_message_is_false():
    env, m, events = _setup()
    _assert(not m.delete_message("Public", {"text": "nope"}), "unknown")


if __name__ == "__main__":
    fake_mpos.run_all(globals())
