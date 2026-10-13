"""Blocking: a contact by key (its direct messages), or a sender name (channel messages and
room posts, which carry only a name). A blocked sender's messages are not shown, counted or
sounded; direct messages and room posts are still acknowledged, so nobody resends them.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_blocking.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402
from test_meshcore_sounds import _group, _dm  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    played = []
    m._play_tune = lambda kind: played.append(kind)
    m.set_sound_settings(enabled=True, all=True)
    return env, m, played


def _texts(msgs):
    return [x["text"] for x in msgs]


def test_a_blocked_contact_is_acked_but_not_shown():
    env, m, played = _setup()
    raw = _dm(env, m, "buy my stuff")
    pk = list(m._contacts)[0]
    m.block_key(pk)
    m.chip.sent.clear()
    m._ingest(raw, rssi=-90, snr=4)
    env.now_ms += 5000
    fake_mpos.drain(m)
    _assert(m.get_dm_messages(pk) == [] and m.get_unread(pk) == 0 and played == [],
            (m.get_dm_messages(pk), played))
    _assert(m.chip.sent, "still acked, so it is not sent again")


def test_a_blocked_name_in_a_channel():
    env, m, played = _setup()
    m._ingest(_group("Spammer", "first", 1790000000), rssi=-90, snr=4)
    m._ingest(_group("Sam", "hello", 1790000001), rssi=-90, snr=4)
    m.block_name("Spammer")
    env.now_ms += 5000
    played.clear()
    m._ingest(_group("Spammer", "again", 1790000002), rssi=-90, snr=4)
    _assert(_texts(m.get_messages("Public")) == ["hello"], "earlier ones are hidden too")
    _assert(played == [], played)
    m.unblock_name("Spammer")
    _assert(_texts(m.get_messages("Public")) == ["first", "hello"],
            "unblocked: what was kept shows again")


def test_the_block_list_is_kept():
    env, m, played = _setup()
    m.block_key("ab" * 32, "Mallory")
    m.block_name("Spammer")
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    m2 = fake_mpos.new_manager(env)
    _assert(m2.blocked() == [{"kind": "key", "value": "ab" * 32, "label": "Mallory"},
                             {"kind": "name", "value": "Spammer", "label": "Spammer"}],
            m2.blocked())
    _assert(m2.is_blocked_key("ab" * 32) and m2.is_blocked_name("Spammer"))
    m2.unblock_key("ab" * 32)
    _assert(not m2.is_blocked_key("ab" * 32))


def test_a_blocked_name_in_a_room():
    env, m, played = _setup()
    room = "7c" + "55" * 31
    m.add_contact(room, "Gent BBS", 3)
    m._add_dm(room, {"ts": 1790000000, "sender": "Spammer", "text": "old post", "incoming": True})
    m._add_dm(room, {"ts": 1790000001, "sender": "Sam", "text": "hi", "incoming": True})
    m.block_name("Spammer")
    _assert(_texts(m.get_dm_messages(room)) == ["hi"], m.get_dm_messages(room))


if __name__ == "__main__":
    fake_mpos.run_all(globals())
