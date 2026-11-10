"""Buzzer sounds: off by default; when on, a tune for the kinds of traffic chosen (channel
messages, direct messages including room posts, adverts heard). Nothing for our own or
repeated packets.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_sounds.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

APP = "eu.axistem.micropymesh"


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    played = []
    m._play_tune = lambda kind: played.append(kind)
    return env, m, played


def _group(sender, text, ts):
    from meshcore_channel import encode_group_text, PUBLIC_CHANNEL
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len
    from meshcore_packet import ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT
    payload = encode_group_text(PUBLIC_CHANNEL, sender, text, ts)
    return MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                          encode_path_len(1), b"\x01", payload).to_bytes()


def _dm(env, m, text, ts=1790000000):
    import meshcore_dm as dm
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len, ROUTE_TYPE_DIRECT
    peer_pub, peer_prv, secret = fake_mpos.with_peer(env, m)
    our_pub, _ = m.get_identity()
    payload, _ = dm.encode_dm(secret, peer_pub, our_pub[0], text, ts)
    return MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, 0x02), encode_path_len(0), b"",
                          payload).to_bytes()


def test_off_by_default():
    env, m, played = _setup()
    _assert(m.sound_settings() == {"enabled": False, "all": False, "channel": True, "dm": True,
                                   "mention": True, "advert": False},
            m.sound_settings())
    m._ingest(_group("Sam", "hi", 1790000000), rssi=-90, snr=4)
    _assert(played == [], played)


def test_settings_persist():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True, advert=True, channel=False)
    m2 = fake_mpos.new_manager(env)
    _assert(m2.sound_settings() == {"enabled": True, "all": False, "channel": False, "dm": True,
                                    "mention": True, "advert": True},
            m2.sound_settings())


def test_each_kind_when_chosen():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True, channel=True, dm=True, advert=True)
    m._ingest(_group("Sam", "hi", 1790000000), rssi=-90, snr=4)
    env.now_ms += 5000
    m._ingest(_dm(env, m, "hello"), rssi=-90, snr=4)
    env.now_ms += 5000
    _, raw = fake_mpos.advert_frame(bytes([9]) * 32, "Robin", 1790000000)
    m._ingest(raw, rssi=-90, snr=4)
    _assert(played == ["channel", "dm", "advert"], played)


def test_only_the_chosen_kinds():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True, channel=False, dm=True, advert=False)
    m._ingest(_group("Sam", "hi", 1790000000), rssi=-90, snr=4)
    env.now_ms += 5000
    _, raw = fake_mpos.advert_frame(bytes([9]) * 32, "Robin", 1790000000)
    m._ingest(raw, rssi=-90, snr=4)
    env.now_ms += 5000
    m._ingest(_dm(env, m, "hello"), rssi=-90, snr=4)
    _assert(played == ["dm"], played)


def test_no_sound_for_a_repeat():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True)
    m._ingest(_group("Sam", "hi", 1790000000), rssi=-90, snr=4)
    m._ingest(_group("Sam", "hi", 1790000001), rssi=-90, snr=4)    # its resend
    _assert(played == ["channel"], played)


def test_no_sound_while_a_tune_is_playing_or_just_played():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True)
    m._ingest(_group("Sam", "one", 1790000000), rssi=-90, snr=4)
    m._ingest(_group("Robin", "two", 1790000010), rssi=-90, snr=4)
    _assert(played == ["channel"], played)
    env.now_ms += 5000
    m._ingest(_group("Robin", "three", 1790000020), rssi=-90, snr=4)
    _assert(played == ["channel", "channel"], played)


def test_each_kind_is_one_short_beep_of_its_own_pitch():
    import meshcore_manager as mm
    notes = []
    for k in ("channel", "mention", "dm", "advert"):
        name, defaults, body = mm.TUNES[k].split(":")
        _assert("," not in body, "one note only: %s" % mm.TUNES[k])
        notes.append(body.strip())
    _assert(len(set(mm.TUNES[k].split(":")[2] + mm.TUNES[k].split(":")[1] for k in
                    ("channel", "mention", "dm", "advert"))) == 4, notes)


def test_all_covers_every_kind_and_keeps_the_own_choice():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True, channel=False, dm=True, advert=False)
    m.set_sound_settings(all=True)
    _, raw = fake_mpos.advert_frame(bytes([9]) * 32, "Robin", 1790000000)
    m._ingest(raw, rssi=-90, snr=4)
    _assert(played == ["advert"], played)
    m.set_sound_settings(all=False)                 # back to the own choice
    st = m.sound_settings()
    _assert(not st["channel"] and st["dm"] and not st["advert"], st)


def test_per_channel_and_contact_override():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True, channel=True)
    m.set_sound_override("Public", "off")           # force off, whatever the settings say
    m._ingest(_group("Sam", "hi", 1790000000), rssi=-90, snr=4)
    _assert(played == [], played)
    _assert(m.sound_override("Public") == "off")
    m.set_sound_settings(enabled=False)
    peer = fake_mpos.with_peer(env, m)[0].hex()
    m.set_sound_override(peer, "on")                # force on, even with the buzzer off
    env.now_ms += 5000
    m._ingest(_dm(env, m, "hello"), rssi=-90, snr=4)
    _assert(played == ["dm"], played)
    m.set_sound_override(peer, "default")
    _assert(m.sound_override(peer) == "default")
    m2 = fake_mpos.new_manager(env)
    _assert(m2.sound_override("Public") == "off", "overrides are kept")


def test_quiet_hours_off_by_default_and_kept():
    env, m, played = _setup()
    _assert(m.quiet_hours() == {"enabled": False, "start": 22 * 60, "end": 7 * 60},
            m.quiet_hours())
    m.set_quiet_hours(enabled=True, start=23 * 60 + 30, end=6 * 60)
    m.set_quiet_hours(start=24 * 60)                       # not a time of day: ignored
    m2 = fake_mpos.new_manager(env)
    _assert(m2.quiet_hours() == {"enabled": True, "start": 23 * 60 + 30, "end": 6 * 60},
            m2.quiet_hours())


def _at(m, hh, mm=0):
    m._local_minute = lambda: hh * 60 + mm


def test_quiet_hours_silence_every_sound():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True, all=True)
    m.set_quiet_hours(enabled=True, start=22 * 60, end=7 * 60)
    m.set_sound_override("Public", "on")                   # even a channel set to always sound
    for hh in (22, 23, 0, 6):
        _at(m, hh, 59 if hh == 6 else 0)
        env.now_ms += 5000
        m._ingest(_group("Sam", "hi %d" % hh, 1790000000 + hh), rssi=-90, snr=4)
    _assert(played == [], played)
    _at(m, 7)
    env.now_ms += 5000
    m._ingest(_group("Sam", "morning", 1790000100), rssi=-90, snr=4)
    _assert(played == ["channel"], played)


def test_quiet_hours_within_one_day_and_unknown_clock():
    env, m, played = _setup()
    m.set_sound_settings(enabled=True, all=True)
    m.set_quiet_hours(enabled=True, start=13 * 60, end=14 * 60)
    _at(m, 13, 30)
    _assert(m._quiet_now())
    _at(m, 14)
    _assert(not m._quiet_now())
    m._local_minute = lambda: None                         # no clock yet: never quiet
    _assert(not m._quiet_now())
    m.set_quiet_hours(enabled=False)
    _at(m, 13, 30)
    _assert(not m._quiet_now())


if __name__ == "__main__":
    fake_mpos.run_all(globals())
