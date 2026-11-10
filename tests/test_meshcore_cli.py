"""Remote commands (the repeater and room server CLI): admin only, sent as TXT_MSG with
txt_type 1 and a "NN|" prefix the server echoes, so a reply finds its command. Replies go to
the console, never into the chat, and are never acked (simple_repeater MyMesh.cpp).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_cli.py
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402
from test_meshcore_sessions import FakeServer, REPEATER  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _commands(srv):
    """The TXT_MSGs we sent the server: [(txt_type, text, timestamp)]."""
    import meshcore_dm as dm
    from meshcore_packet import MeshCorePacket
    fake_mpos.drain(srv.m)
    out = []
    for raw in srv.m.chip.sent:
        pkt = MeshCorePacket.parse(raw)
        p = bytes(pkt.payload)
        if pkt.payload_type == 0x02 and p[0] == srv.pub[0]:
            pt = dm.mac_then_decrypt(srv.secret, p[2:4], p[4:])
            ts, flags = struct.unpack("<IB", pt[:5])
            out.append((flags >> 2, pt[5:].rstrip(b"\x00").decode(), ts))
    srv.m.chip.sent.clear()
    return out


def _reply(srv, text, txt_type=1):
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len, ROUTE_TYPE_DIRECT
    pt = struct.pack("<IB", 1790000100, txt_type << 2) + text.encode()
    pkt = MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, 0x02), encode_path_len(0), b"",
                         srv._envelope(pt))
    srv.m._ingest(pkt.to_bytes(), rssi=-80, snr=7)


def _admin():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    srv = FakeServer(env, m, REPEATER)
    m.add_contact(srv.hex, node_type=REPEATER)
    m.get_contact(srv.hex).update(path=b"\x3a", path_raw=1)
    m.login(srv.hex, "hunter2")
    srv.answer_login()
    srv.take()
    return env, m, srv


def test_commands_need_an_admin_login():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    srv = FakeServer(env, m, REPEATER)
    m.login(srv.hex)
    srv.answer_login(flood_path=b"\x3a")
    ok, err = m.send_cli(srv.hex, "ver")
    _assert(not ok and "admin" in err, err)


def test_a_command_goes_out_as_cli_data_with_a_prefix():
    env, m, srv = _admin()
    ok, err = m.send_cli(srv.hex, "ver")
    _assert(ok, err)
    (txt_type, text, ts), = _commands(srv)
    _assert(txt_type == 1, txt_type)
    _assert(len(text) == 6 and text[2] == "|" and text[3:] == "ver", text)
    log = m.cli_log(srv.hex)
    _assert(log[-1]["dir"] == "out" and log[-1]["text"] == "ver" and log[-1]["state"] == "sent", log)


def test_the_reply_goes_to_the_console_not_the_chat_and_is_not_acked():
    env, m, srv = _admin()
    m.send_cli(srv.hex, "ver")
    (_, text, _), = _commands(srv)
    _reply(srv, text[:3] + "v1.18.0 (Build: 15 Oct 2026)")
    log = m.cli_log(srv.hex)
    _assert(log[-1] == dict(log[-1], dir="in", text="v1.18.0 (Build: 15 Oct 2026)"), log)
    _assert(log[-2]["state"] == "answered", log)
    _assert(m.get_dm_messages(srv.hex) == [], "not in the chat")
    fake_mpos.drain(m)
    env.now_ms += 5000
    fake_mpos.drain(m)
    _assert([raw for raw in m.chip.sent if raw[0] >> 2 & 0x0F == 0x03] == [], "no ack")


def test_one_command_at_a_time_and_no_answer_is_said():
    env, m, srv = _admin()
    m.send_cli(srv.hex, "clock")
    ok, err = m.send_cli(srv.hex, "ver")
    _assert(not ok and "waiting" in err, err)
    env.now_ms += 60000
    m._server_tick()
    _assert(m.cli_log(srv.hex)[-1]["state"] == "no answer", m.cli_log(srv.hex))
    _assert(m.send_cli(srv.hex, "ver")[0], "free again")


def test_a_command_must_fit_one_message():
    env, m, srv = _admin()
    ok, err = m.send_cli(srv.hex, "set owner.info " + "x" * 150)
    _assert(not ok and "long" in err, err)
    ok, err = m.send_cli(srv.hex, "   ")
    _assert(not ok, err)


def test_timestamps_keep_rising_across_requests_and_commands():
    env, m, srv = _admin()
    m.send_cli(srv.hex, "clock")
    (_, text, ts1), = _commands(srv)
    _reply(srv, text[:3] + "12:00 - 21/10/2026 UTC")
    m.request_server(srv.hex, "status")
    reqs = [x for x in srv.take() if x[0] == "req"]
    tag = struct.unpack("<I", reqs[0][2][:4])[0]
    _assert(tag > ts1, (tag, ts1))



def test_clock_sync_sets_a_clock_that_is_behind():
    env, m, srv = _admin()
    m.server_session(srv.hex)["clock_skew_s"] = -600
    ok, err = m.clock_sync(srv.hex)
    _assert(ok, err)
    (_, text, ts), = _commands(srv)
    _assert(text[3:] == "clock sync", text)
    _reply(srv, text[:3] + "OK - clock set: 12:00:00 - 21/10/2026 UTC")
    _assert(m.server_session(srv.hex)["clock_skew_s"] is None, "fixed")


def test_clock_sync_needs_our_clock():
    env, m, srv = _admin()
    mm = sys.modules["meshcore_manager"]
    real = mm.unix_time
    mm.unix_time = lambda: 3600                 # never synced: we would set it to 1970
    try:
        ok, err = m.clock_sync(srv.hex)
        _assert(not ok and "our clock" in err, err)
    finally:
        mm.unix_time = real



def test_reading_settings_asks_one_get_after_the_other():
    env, m, srv = _admin()
    _assert(m.read_settings(srv.hex, ["name", "repeat", "tx"]) == (True, None))
    answers = {"get name": "> Gent-Noord", "get repeat": "> on", "get tx": "> 20"}
    seen = []
    for _ in range(3):
        (_, text, _), = _commands(srv)
        seen.append(text[3:])
        _reply(srv, text[:3] + answers[text[3:]])
    _assert(seen == ["get name", "get repeat", "get tx"], seen)
    _assert(m.remote_settings(srv.hex) == {"name": "Gent-Noord", "repeat": "on", "tx": "20"},
            m.remote_settings(srv.hex))


def test_a_setting_is_checked_before_it_is_sent_and_kept_when_ok():
    env, m, srv = _admin()
    for key, bad in (("flood.max", "65"), ("advert.interval", "30"), ("name", "a:b"),
                     ("guest.password", "x" * 16), ("lat", "91"), ("repeat", "maybe"),
                     ("flood.advert.interval", "200"), ("tx", "40")):
        ok, err = m.set_remote_setting(srv.hex, key, bad)
        _assert(not ok and err, (key, bad, err))
    _assert(_commands(srv) == [], "nothing sent")
    _assert(m.set_remote_setting(srv.hex, "advert.interval", "120") == (True, None))
    (_, text, _), = _commands(srv)
    _assert(text[3:] == "set advert.interval 120", text)
    _reply(srv, text[:3] + "OK")
    _assert(m.remote_settings(srv.hex)["advert.interval"] == "120")


def test_a_refused_setting_is_not_kept():
    env, m, srv = _admin()
    m.set_remote_setting(srv.hex, "flood.max", "10")
    (_, text, _), = _commands(srv)
    _reply(srv, text[:3] + "unknown config: flood.max")
    _assert("flood.max" not in m.remote_settings(srv.hex))


if __name__ == "__main__":
    fake_mpos.run_all(globals())
