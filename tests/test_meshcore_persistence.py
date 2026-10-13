"""Persistence of channel and DM history, unread counts and mentions.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_persistence.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

APP = "eu.axistem.micropymesh"


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _group_frame(sender, text, ts, channel=None):
    from meshcore_channel import encode_group_text, PUBLIC_CHANNEL
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len
    from meshcore_packet import ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT
    payload = encode_group_text(channel or PUBLIC_CHANNEL, sender, text, ts)
    return MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                          encode_path_len(1), b"\x01", payload).to_bytes()


def _setup(env=None):
    env = env or fake_mpos.install()
    m = fake_mpos.new_manager(env)
    if not m.has_identity():
        m.generate_identity()
    return env, m


def _restart(env):
    """A new manager on the same stored prefs, as after a reboot."""
    return fake_mpos.new_manager(env)


def test_channel_history_survives_restart():
    env, m = _setup()
    for i in range(3):
        m._ingest(_group_frame("Sam", "msg %d" % i, 1700000000 + 10 * i), rssi=-90, snr=4)
    m._flush_dirty()
    m2 = _restart(env)
    msgs = m2.get_messages("Public")
    _assert([x["text"] for x in msgs] == ["msg 0", "msg 1", "msg 2"], msgs)
    _assert(msgs[0]["snr"] == 4 and msgs[0]["sender"] == "Sam", msgs[0])


def test_channel_history_capped():
    import meshcore_manager as mm
    env, m = _setup()
    for i in range(60):
        m._ingest(_group_frame("Sam", "msg %d" % i, 1700000000 + 10 * i), rssi=-90, snr=4)
    m._flush_dirty()
    stored = env.prefs(APP, "channel_history.json")["ch"]["Public"]
    _assert(len(stored) == mm.CH_HISTORY_CAP == 50, len(stored))
    _assert(stored[-1]["text"] == "msg 59" and stored[0]["text"] == "msg 10")


def test_corrupt_history_file_starts_empty():
    env = fake_mpos.install()
    env.store[(APP, "channel_history.json")] = {"ch": {"Public": "garbage", "#x": [1, None]}}
    env.store[(APP, "dm_history.json")] = {"h": 5}
    env.store[(APP, "unread.json")] = {"u": "nope", "m": 3}
    env, m = _setup(env)
    _assert(m.get_messages("Public") == [], m.get_messages("Public"))
    _assert(m.get_unread("Public") == 0)
    env.store[(APP, "channel_history.json")] = "not a dict"
    m2 = _restart(env)
    _assert(m2.get_messages("Public") == [])


def test_dm_history_survives_restart_and_migrates_from_config():
    env = fake_mpos.install()
    env, m = _setup(env)
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    key = peer_pub.hex()
    old = {"ts": 1700000000, "sender": "Alex", "text": "old", "incoming": True}
    env.prefs(APP)["dm_history"] = {key: [old]}
    m2 = _restart(env)
    _assert([x["text"] for x in m2.get_dm_messages(key)] == ["old"])
    m2._flush_dirty()
    _assert(not env.prefs(APP).get("dm_history"), env.prefs(APP).get("dm_history"))
    _assert(env.prefs(APP, "dm_history.json")["h"][key][0]["text"] == "old")
    m3 = _restart(env)
    _assert([x["text"] for x in m3.get_dm_messages(key)] == ["old"])


def test_pending_messages_after_restart_offer_resend():
    env, m = _setup()
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    m.send_group_text("Public", "lost in the reboot")
    m.send_dm(peer_pub.hex(), "also lost")
    m._flush_dirty()
    m2 = _restart(env)
    ch = m2.get_messages("Public")[-1]
    dm = m2.get_dm_messages(peer_pub.hex())[-1]
    _assert(ch["unheard"] is True, ch)
    _assert(dm["failed"] is True, dm)


def test_remove_channel_deletes_history():
    env, m = _setup()
    ok, err = m.add_channel("#test")
    _assert(ok, err)
    m.send_group_text("#test", "hi")
    m._flush_dirty()
    _assert("#test" in env.prefs(APP, "channel_history.json")["ch"])
    m.remove_channel("#test")
    m._flush_dirty()
    _assert("#test" not in env.prefs(APP, "channel_history.json")["ch"])


def test_unread_and_mention_persist():
    env, m = _setup()
    m.set_nickname("Kim")
    m._ingest(_group_frame("Sam", "hi @[Kim] signal check", 1700000000), rssi=-90, snr=4)
    _assert(m.get_unread("Public") == 1 and m.get_mention("Public") is True)
    m._flush_dirty()
    m2 = _restart(env)
    _assert(m2.get_unread("Public") == 1 and m2.get_mention("Public") is True)
    m2.clear_unread("Public")
    _assert(m2.get_unread("Public") == 0 and m2.get_mention("Public") is False)
    m2._flush_dirty()
    m3 = _restart(env)
    _assert(m3.get_unread("Public") == 0 and m3.get_mention("Public") is False)


def test_flush_is_rate_limited_while_the_worker_runs():
    env, m = _setup()
    m._worker_running = True
    m._ingest(_group_frame("Sam", "one", 1700000000), rssi=-90, snr=4)
    m._flush_due()
    writes = env.constructions
    m._ingest(_group_frame("Sam", "two", 1700000010), rssi=-90, snr=4)
    m._flush_due()
    _assert(env.constructions == writes, "flushed again within 5 s")
    env.now_ms += 5001
    m._flush_due()
    _assert(env.prefs(APP, "channel_history.json")["ch"]["Public"][-1]["text"] == "two")


if __name__ == "__main__":
    fake_mpos.run_all(globals())
