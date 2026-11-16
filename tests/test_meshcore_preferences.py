"""User preferences and the radio monitor: editable quick replies, contacts added
automatically per node type, a sound for messages that mention us, and the list of
recently heard packets.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_preferences.py
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
    m.set_nickname("Kim")
    return env, m


def _group(sender, text, ts):
    from meshcore_channel import encode_group_text, PUBLIC_CHANNEL
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len
    from meshcore_packet import ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT
    payload = encode_group_text(PUBLIC_CHANNEL, sender, text, ts)
    return MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                          encode_path_len(2), b"\x01\x02", payload).to_bytes()


# --- quick replies ----------------------------------------------------------- #

def test_quick_replies_default_and_edit():
    env, m = _setup()
    _assert(m.quick_replies() == ["copy", "on my way", "ETA 10 min", "signal report"], m.quick_replies())
    m.set_quick_replies(["wilco", "  ", "stand by", "x" * 60])
    _assert(m.quick_replies() == ["wilco", "stand by", "x" * 40], m.quick_replies())
    _assert(fake_mpos.new_manager(env).quick_replies() == ["wilco", "stand by", "x" * 40])


def test_at_most_eight_quick_replies():
    env, m = _setup()
    m.set_quick_replies(["r%d" % i for i in range(12)])
    _assert(len(m.quick_replies()) == 8)


# --- auto-add ------------------------------------------------------------------ #

def test_auto_add_is_off_by_default():
    env, m = _setup()
    _assert(m.auto_add_settings() == {"enabled": False, "all": False, "chat": False, "rptr": False,
                                      "room": False, "sensor": False, "max_hops": None},
            m.auto_add_settings())
    pub, raw = fake_mpos.advert_frame(bytes([9]) * 32, "Robin", 1790000000, node_type=1)
    m._ingest(raw, rssi=-90, snr=4)
    _assert(not m.is_contact(pub.hex()))


def test_auto_add_per_type():
    env, m = _setup()
    m.set_auto_add(rptr=True, room=True)
    rptr, raw = fake_mpos.advert_frame(bytes([7]) * 32, "Gent-Noord", 1790000000, node_type=2)
    m._ingest(raw, rssi=-90, snr=4)
    chat, raw = fake_mpos.advert_frame(bytes([9]) * 32, "Robin", 1790000000, node_type=1)
    m._ingest(raw, rssi=-90, snr=4)
    _assert(m.is_contact(rptr.hex()) and m.get_contact(rptr.hex())["type"] == 2)
    _assert(not m.is_contact(chat.hex()))
    _assert(fake_mpos.new_manager(env).auto_add_settings()["rptr"])


def _advert(m, seed, name, node_type=1, hops=0):
    pub, raw = fake_mpos.advert_frame(bytes([seed]) * 32, name, 1790000000, node_type=node_type,
                                      path=bytes(range(1, hops + 1)))
    m._ingest(raw, rssi=-90, snr=4)
    return pub.hex()


def test_auto_add_switched_off_adds_nothing():
    env, m = _setup()
    m.set_auto_add(all=True, enabled=False)
    _assert(not m.is_contact(_advert(m, 9, "Robin")), "off")
    m.set_auto_add(enabled=True)
    _assert(m.is_contact(_advert(m, 8, "Sam")), "on")


def test_all_adds_every_type_and_keeps_the_own_choice():
    env, m = _setup()
    m.set_auto_add(enabled=True, rptr=True)
    m.set_auto_add(all=True)
    _assert(m.is_contact(_advert(m, 9, "Robin", node_type=1)), "companion via all")
    cfg = m.set_auto_add(all=False)
    _assert(cfg["rptr"] and not cfg["chat"], cfg)
    _assert(not m.is_contact(_advert(m, 6, "Kai", node_type=1)), "own choice again")


def test_max_hops_limits_what_is_added():
    env, m = _setup()
    m.set_auto_add(enabled=True, all=True, max_hops=2)
    _assert(m.is_contact(_advert(m, 9, "Near", hops=2)), "2 hops")
    _assert(not m.is_contact(_advert(m, 8, "Far", hops=3)), "3 hops")
    m.set_auto_add(max_hops=None)
    _assert(m.is_contact(_advert(m, 7, "Farther", hops=5)), "no limit")
    _assert(fake_mpos.new_manager(env).auto_add_settings()["max_hops"] is None, "saved")


def test_max_hops_must_be_a_hop_count():
    env, m = _setup()
    for bad in (-1, 65, "x"):
        _assert(m.set_auto_add(max_hops=bad)["max_hops"] is None, bad)
    _assert(m.set_auto_add(max_hops="3")["max_hops"] == 3, "text number")


def test_an_older_setting_without_the_main_switch_keeps_working():
    env, m = _setup()
    env.prefs("eu.axistem.micropymesh")["auto_add"] = {"chat": True, "rptr": True}
    cfg = fake_mpos.new_manager(env).auto_add_settings()
    _assert(cfg["enabled"] and not cfg["all"] and cfg["chat"] and cfg["rptr"], cfg)


# --- mention sound -------------------------------------------------------------- #

def test_mention_has_its_own_sound():
    env, m = _setup()
    played = []
    m._play_tune = played.append
    m.set_sound_settings(enabled=True, channel=False, mention=True)
    m._ingest(_group("Sam", "hello all", 1790000000), rssi=-90, snr=4)
    env.now_ms += 5000
    m._ingest(_group("Sam", "@[Kim] are you there?", 1790000010), rssi=-90, snr=4)
    _assert(played == ["mention"], played)


def test_mention_sound_can_be_off():
    env, m = _setup()
    played = []
    m._play_tune = played.append
    m.set_sound_settings(enabled=True, channel=True, mention=False)
    m._ingest(_group("Sam", "@[Kim] hi", 1790000000), rssi=-90, snr=4)
    _assert(played == ["channel"], played)


def test_mention_is_in_the_sound_settings():
    env, m = _setup()
    _assert(m.sound_settings()["mention"] is True)
    import meshcore_manager as mm
    _assert(mm.TUNES["mention"] not in (mm.TUNES["channel"], mm.TUNES["dm"]))


# --- recently heard -------------------------------------------------------------- #

def test_recently_heard_packets():
    env, m = _setup()
    m._ingest(_group("Sam", "one", 1790000000), rssi=-54, snr=10.25)
    env.now_ms += 61000
    _, raw = fake_mpos.advert_frame(bytes([9]) * 32, "Robin", 1790000000)
    m._ingest(raw, rssi=-66, snr=12.0)
    recent = m.radio_stats()["recent"]
    _assert(recent[0] == {"age_s": 0, "kind": "ADV", "rssi": -66, "snr": 12.0, "hops": 0}, recent[0])
    _assert(recent[1] == {"age_s": 61, "kind": "GRP", "rssi": -54, "snr": 10.25, "hops": 2}, recent[1])


def test_recent_list_is_bounded_and_counts_per_minute():
    env, m = _setup()
    for i in range(30):
        m._ingest(_group("Sam", "msg %d" % i, 1790000000 + i), rssi=-60, snr=5)
        env.now_ms += 10000
    st = m.radio_stats()
    _assert(len(st["recent"]) == 20, len(st["recent"]))
    _assert(st["rx_per_min"] == 6.0, st["rx_per_min"])      # one every 10 s over 10 min


if __name__ == "__main__":
    fake_mpos.run_all(globals())
