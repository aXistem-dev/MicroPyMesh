"""Pure view models behind the screens (no LVGL).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_ui_model.py
"""

import calendar
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

NOW = calendar.timegm((2026, 10, 2, 14, 32, 0))     # a Friday, 14:32 UTC


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _ui():
    import ui_model
    return ui_model


def _seeded():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    m.set_nickname("Kim")
    m.add_channel("#test")
    peer_pub, _, _ = fake_mpos.with_peer(env, m, name="Alex")
    m._add_message("Public", {"ts": NOW - 180, "sender": "Sam", "text": "anyone near the Gent repeater tonight?",
                              "incoming": True})
    m._bump_unread("Public")
    m._add_message("#test", {"ts": NOW - 60, "sender": "Robin", "text": "@[Kim] signal check",
                             "incoming": True})
    m._bump_unread("#test", mention=True)
    m._add_dm(peer_pub.hex(), {"ts": NOW - 120, "sender": "Kim", "text": "see you at three",
                               "incoming": False, "tx": True, "delivered": True})
    return m, peer_pub.hex()


def test_display_drops_emoji_without_an_image():
    ui = _ui()
    ui.set_emoji_filter(lambda cp: cp == 0x1F44D)
    try:
        _assert(ui.display("Scribe\U0001F4DC") == "Scribe", ui.display("Scribe\U0001F4DC"))
        _assert(ui.display("ok \U0001F44D") == "ok \U0001F44D")
        flag = "\U0001F1E7\U0001F1EA"           # regional indicators: the font handles flags
        _assert(ui.display(flag + "ON1CV") == flag + "ON1CV")
        _assert(ui.display("\U0001F6F8UFO") == "UFO")
        _assert(ui.display("Привет") == "Привет")      # other scripts are left alone
    finally:
        ui.set_emoji_filter(None)
    _assert(ui.display("Scribe\U0001F4DC") == "Scribe\U0001F4DC")     # no filter: unchanged


def test_initials_skip_emoji():
    ui = _ui()
    _assert(ui.initials("\U0001F6F8UFO") == "UF", ui.initials("\U0001F6F8UFO"))
    _assert(ui.initials("\U0001F1E7\U0001F1EA Benito") == "BE", ui.initials("\U0001F1E7\U0001F1EA Benito"))
    _assert(ui.initials("Alex Smith") == "AS")


def test_names_and_previews_pass_through_display():
    m, alex = _seeded()
    ui = _ui()
    m.set_nickname("Kim")
    ui.set_emoji_filter(lambda cp: False)
    try:
        m._add_message("Public", {"ts": NOW, "sender": "Scribe\U0001F4DC", "text": "hi",
                                  "incoming": True})
        row = [r for r in ui.chat_rows(m, NOW) if r["key"] == "Public"][0]
        _assert(row["preview"] == "Scribe: hi", row)
        nodes = [{"pubkey": "a3" * 32, "id": "a3", "type": 2, "name": "\U0001F6F8UFO", "hops": 0,
                  "heard_ms": 0}]
        _assert(ui.node_rows(nodes, 0)[0]["name"] == "UFO")
        _assert(ui.node_detail(nodes[0], 0)["title"] == "UFO")
    finally:
        ui.set_emoji_filter(None)


def test_chat_rows_sorted_and_filtered():
    m, alex = _seeded()
    ui = _ui()
    rows = ui.chat_rows(m, NOW)
    _assert([r["title"] for r in rows] == ["#test", "Alex", "Public"], rows)
    _assert([r["key"] for r in ui.chat_rows(m, NOW, "direct")] == [alex])
    _assert([r["title"] for r in ui.chat_rows(m, NOW, "channels")] == ["#test", "Public"])
    _assert([r["title"] for r in ui.chat_rows(m, NOW, "unread")] == ["#test", "Public"])
    t = {r["title"]: r for r in rows}
    _assert(t["#test"]["mention"] is True and t["#test"]["unread"] == 1)
    _assert(t["Public"]["kind"] == "channel" and t["Alex"]["kind"] == "dm")
    _assert(t["Public"]["initials"] == "#" and t["Alex"]["initials"] == "AL")
    _assert(t["Public"]["time"] == "14:29", t["Public"]["time"])


def test_chat_row_without_messages():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    rows = _ui().chat_rows(m, NOW)
    _assert(rows[0]["title"] == "Public" and rows[0]["preview"] == "No messages yet" and rows[0]["time"] == "")


def test_preview_prefixes():
    m, alex = _seeded()
    t = {r["title"]: r for r in _ui().chat_rows(m, NOW)}
    _assert(t["Public"]["preview"] == "Sam: anyone near the Gent repeater tonight?")
    _assert(t["Alex"]["preview"] == "You: see you at three", t["Alex"]["preview"])
    m._add_dm(alex, {"ts": NOW - 10, "sender": "Alex", "text": "ok!", "incoming": True})
    t = {r["title"]: r for r in _ui().chat_rows(m, NOW)}
    _assert(t["Alex"]["preview"] == "ok!", t["Alex"]["preview"])


def test_time_text():
    ui = _ui()
    _assert(ui.time_text(NOW - 60, NOW) == "14:31")
    _assert(ui.time_text(NOW - 60, NOW, tz_s=7200) == "16:31")
    _assert(ui.time_text(NOW - 2 * 86400, NOW) == "Wed")
    _assert(ui.time_text(NOW - 20 * 86400, NOW) == "12 Sep")


def test_tz_offset():
    ui = _ui()
    _assert(ui.tz_offset((2026, 10, 2, 16, 32, 0), (2026, 10, 2, 14, 32, 0)) == 7200)
    _assert(ui.tz_offset((2026, 10, 3, 1, 0, 0), (2026, 10, 2, 23, 0, 0)) == 7200)
    _assert(ui.tz_offset((2026, 10, 2, 20, 0, 0), (2026, 10, 3, 1, 0, 0)) == -5 * 3600)
    _assert(ui.tz_offset((2026, 10, 2, 14, 32, 0), (2026, 10, 2, 14, 32, 0)) == 0)


def test_delivery_states():
    ui = _ui()
    ts = NOW - 60                                   # 14:31 UTC
    d = ui.delivery({"incoming": False, "tx": False, "ts": ts}, 0)
    _assert(d == {"icon": None, "text": "14:31 · sending", "color": ui.MUTED, "dots": 0}, d)
    d = ui.delivery({"incoming": False, "tx": True, "ts": ts, "heard": 0}, 0)
    _assert(d["text"] == "14:31 · sent" and d["dots"] == 0, d)
    d = ui.delivery({"incoming": False, "tx": True, "ts": ts, "heard": 4}, 0)
    _assert(d == {"icon": None, "text": "14:31 · heard ×4", "color": ui.MUTED, "dots": 4}, d)
    _assert(ui.delivery({"incoming": False, "tx": True, "ts": ts, "heard": 9}, 0)["dots"] == 6)
    d = ui.delivery({"incoming": False, "tx": True, "ts": ts, "heard": 0, "unheard": True}, 0)
    _assert(d["text"] == "not heard by a repeater · tap to resend" and d["color"] == ui.WARN, d)
    d = ui.delivery({"incoming": False, "tx": True, "ts": ts, "ack": "ab", "delivered": True,
                     "ack_ts": NOW - 30, "ack_snr": 7.0}, 0)
    _assert(d == {"icon": "check", "text": "delivered 14:31 · SNR 7.0", "color": ui.MUTED,
                  "dots": 0, "icon_color": ui.DELIVERED}, d)
    d = ui.delivery({"incoming": False, "tx": True, "ts": ts, "ack": "ab", "delivered": True}, 0)
    _assert(d["text"] == "delivered 14:31", d)
    d = ui.delivery({"incoming": False, "tx": True, "ts": ts, "ack": "ab", "failed": True}, 0)
    _assert(d == {"icon": "retry", "text": "no ack after 4 tries · tap to resend",
                  "color": ui.FAIL_TEXT, "dots": 0, "icon_color": ui.FAIL_TEXT}, d)


def test_budget_counts_utf8_bytes():
    ui = _ui()
    _assert(ui.budget("é" * 10) == 140)
    _assert(ui.budget("x" * 161) == -1)


def test_channel_budget_includes_sender_prefix():
    _assert(_ui().budget("hi", "Public", "Kim") == 160 - 5 - 2)


def test_node_rows_meta_and_age_colors():
    ui = _ui()
    now_ms = 10 * 3600 * 1000
    nodes = [
        {"pubkey": "a3" * 32, "id": "a3", "type": 1, "name": "Alex", "snr": 8.0, "hops": 0,
         "heard_ms": now_ms - 120 * 1000},
        {"pubkey": "f1" * 32, "id": "f1", "type": 2, "name": "Gent-Noord", "snr": -3.5, "hops": 2,
         "heard_ms": now_ms - 3600 * 1000},
        {"pubkey": "7c" * 32, "id": "7c", "type": 3, "name": "Gent BBS", "snr": None, "hops": 3,
         "heard_ms": now_ms - 7 * 3600 * 1000},
    ]
    rows = ui.node_rows(nodes, now_ms)
    _assert([(r["hex"], r["kind"]) for r in rows] == [("A3", "chat"), ("F1", "rptr"), ("7C", "room")])
    _assert(rows[0]["meta"] == "direct · SNR 8.0" and rows[0]["age"] == "2 min", rows[0])
    _assert(rows[1]["meta"] == "2 hops · SNR −3.5" and rows[1]["age"] == "1 h", rows[1])
    _assert(rows[2]["meta"] == "3 hops", rows[2]["meta"])
    _assert([r["age_color"] for r in rows] == [ui.OK, ui.WARN, ui.MUTED])
    _assert([r["name"] for r in ui.node_rows(nodes, now_ms, "rptr")] == ["Gent-Noord"])
    _assert([r["name"] for r in ui.node_rows(nodes, now_ms, "new", contacts={"a3" * 32})] == [])
    _assert(len(ui.node_rows(nodes, now_ms, "new")) == 1)


def test_node_detail():
    ui = _ui()
    now_ms = 10 * 3600 * 1000
    pk = "f1a7" + "33" * 28 + "9c2e"
    d = ui.node_detail({"pubkey": pk, "id": "f1", "type": 2, "name": "Gent-Noord", "snr": -3.5,
                        "hops": 2, "heard_ms": now_ms - 840 * 1000, "verified": True,
                        "lat": 51.05, "lon": 3.72, "path": "3af1"}, now_ms)
    _assert(d["title"] == "Gent-Noord")
    _assert(d["subtitle"] == "repeater · F1A7…9C2E", d["subtitle"])
    _assert(d["info"] == "2 hops · SNR −3.5 dB · heard 14 min ago", d["info"])
    _assert(d["route"] == ("2 hops via ", "3A › F1", " · SNR −3.5 dB · heard 14 min ago"), d["route"])
    _assert(("Location", "51.0500, 3.7200") in d["fields"], d["fields"])
    _assert(("Last SNR", "−3.5 dB") in d["fields"], d["fields"])
    _assert(d["pubkey"] == pk, d)
    _assert(("Signature", "verified") in d["fields"])
    d2 = ui.node_detail({"pubkey": pk, "type": 1, "name": "", "snr": None, "hops": 0,
                         "heard_ms": now_ms, "verified": False}, now_ms)
    _assert(d2["subtitle"].startswith("companion · ") and d2["info"] == "direct · heard now", d2)
    _assert(d2["route"] == ("direct", "", " · heard now"), d2["route"])
    _assert(("Signature", "not checked") in d2["fields"])


def test_radio_texts():
    ui = _ui()
    st = {"rx_on": True, "last_rx_s": 40, "noise_dbm": -106.0, "noise_series": [-106.0],
          "peak_rssi_30m": -74.0, "packets_per_h": 6, "tx_air_pct": 0.1}
    t = ui.radio_texts(st)
    _assert(t == {"subtitle": "RX on · last packet 40 s ago", "noise": "−106",
                  "stats": [("Peak", "−74"), ("Packets", "6/h"), ("TX air", "0.1 %")]}, t)
    t = ui.radio_texts({"rx_on": False, "last_rx_s": None, "noise_dbm": None, "noise_series": [],
                        "peak_rssi_30m": None, "packets_per_h": 0, "tx_air_pct": 0.0})
    _assert(t["subtitle"] == "RX off" and t["noise"] == "—" and t["stats"][0] == ("Peak", "—"), t)
    _assert(ui.radio_texts(dict(st, last_rx_s=None))["subtitle"] == "RX on · nothing heard yet")
    _assert(ui.radio_texts(dict(st, last_rx_s=7200))["subtitle"] == "RX on · last packet 2 h ago")


def test_node_rows_contacts_and_search():
    ui = _ui()
    now_ms = 10 * 3600 * 1000
    nodes = [{"pubkey": "a3" * 32, "id": "a3", "type": 1, "name": "Alex", "hops": 0, "heard_ms": now_ms},
             {"pubkey": "f1" * 32, "id": "f1", "type": 2, "name": "Gent-Noord", "hops": 2, "heard_ms": now_ms}]
    _assert([r["name"] for r in ui.node_rows(nodes, now_ms, "contacts", contacts={"a3" * 32})] == ["Alex"])
    _assert([r["name"] for r in ui.node_rows(nodes, now_ms, query="gent")] == ["Gent-Noord"])
    _assert([r["name"] for r in ui.node_rows(nodes, now_ms, query="A3")] == ["Alex"])


def test_age_text():
    ui = _ui()
    _assert([ui.age_text(s) for s in (5, 120, 3700, 3 * 86400)] == ["now", "2 min", "1 h", "3 d"])


def test_preset_summary_airtime():
    fake_mpos.install()
    import meshcore_presets
    title, detail, air = _ui().preset_summary(meshcore_presets.by_id("eu-narrow"), 22)
    _assert(title == "EU/UK (Narrow)")
    _assert(detail == "869.618 MHz · 62.5 kHz · SF8 · CR 4/8 · 22 dBm", detail)
    _assert(air == "≈ 0.54 s on air per 40-byte message", air)


def test_signal_report_and_quick_replies():
    ui = _ui()
    _assert(ui.QUICK_REPLIES == ("copy", "on my way", "ETA 10 min", "signal report"))
    _assert(ui.signal_report({"snr": 6.5, "hops": 3}) == "SNR 6.5 · 3 hops")
    _assert(ui.signal_report({"snr": -2.0, "hops": 1}) == "SNR −2.0 · 1 hop")


# --- repeaters and room servers -------------------------------------------- #

def test_status_rows():
    ui = _ui()
    data = {"battery_mv": 4020, "uptime_s": 1051200, "noise_floor": -112, "last_snr": -3.5,
            "airtime_s": 22151, "rx_airtime_s": 67507, "packets_rx": 18233, "packets_tx": 9120}
    rows = ui.status_rows(data)
    _assert(rows == [("Battery", "4.02 V"), ("Uptime", "12 d 4 h"), ("Noise floor", "\u2212112 dBm"),
                     ("Last SNR", "\u22123.5 dB"), ("TX airtime", "2.1 %"), ("RX airtime", "6.4 %"),
                     ("Packets RX", "18 233"), ("Packets TX", "9 120")], rows)
    room = ui.status_rows(dict(data, posted=12, rx_airtime_s=None), room=True)
    _assert(("Posts", "12") in room and not any(k == "RX airtime" for k, v in room), room)
    _assert(ui.status_rows({"uptime_s": 3700}) == [("Uptime", "1 h 1 min")])


def test_neighbour_rows_resolve_names():
    ui = _ui()
    nodes = {"3a" + "44" * 31: {"name": "Aalst-Kerk"}}
    rows = ui.neighbour_rows([{"prefix": "3a444444", "secs_ago": 120, "snr": 4.25},
                              {"prefix": "c0ffee00", "secs_ago": 7200, "snr": -2.0}], nodes)
    _assert(rows == [{"name": "Aalst-Kerk", "hex": "3A", "detail": "SNR 4.2 \u00b7 2 min ago"},
                     {"name": "C0FFEE00", "hex": "C0", "detail": "SNR \u22122.0 \u00b7 2 h ago"}], rows)


def test_telemetry_rows():
    ui = _ui()
    rows = ui.telemetry_rows([{"channel": 1, "kind": "voltage", "value": 4.02},
                              {"channel": 1, "kind": "temperature", "value": 23.5},
                              {"channel": 1, "kind": "gps", "value": (51.0704, 3.718, 12.0)},
                              {"channel": 2, "kind": "humidity", "value": 50.5}])
    _assert(rows == [("Voltage", "4.02 V"), ("Temperature", "23.5 \u00b0C"),
                     ("Location", "51.0704, 3.7180"), ("Humidity \u00b7 2", "50.5 %")], rows)


def test_ping_and_trace_lines():
    ui = _ui()
    ping = {"hop_snrs": [6.0], "final_snr": 5.5, "rtt_ms": 350, "hashes": [0xF1]}
    _assert(ui.trace_text(ping, "ping") == "Ping 350 ms \u00b7 SNR there 6.0 \u00b7 back 5.5")
    tr = {"hop_snrs": [6.0, 4.0, 5.5], "final_snr": 7.0, "rtt_ms": 1200, "hashes": [0x3A, 0xF1, 0x3A]}
    _assert(ui.trace_text(tr, "trace") == "3A 6.0 \u203a F1 4.0 \u203a 3A 5.5 \u203a you 7.0 \u00b7 1.2 s")


def test_login_line():
    ui = _ui()
    _assert(ui.login_line({"state": "idle"}) == ("Not logged in", ui.MUTED))
    _assert(ui.login_line({"state": "pending", "pending": {"kind": "login"}}) == ("Logging in\u2026", ui.MUTED))
    _assert(ui.login_line({"state": "ok", "role": "guest"}) == ("Guest login", ui.MUTED))
    _assert(ui.login_line({"state": "ok", "role": "admin"}) == ("Admin login", ui.OK))
    _assert(ui.login_line({"state": "failed", "error": "no answer from X"}) == ("no answer from X", ui.FAIL_TEXT))


def test_chats_leave_repeaters_out_and_show_rooms():
    m, alex = _seeded()
    ui = _ui()
    m.add_contact("f1" + "33" * 31, "Gent-Noord", 2)
    m.add_contact("7c" + "55" * 31, "Gent BBS", 3)
    m._add_dm("7c" + "55" * 31, {"ts": NOW - 60, "sender": "Sam", "text": "welcome",
                                 "incoming": True})          # a room with posts is a chat
    rows = {r["title"]: r for r in ui.chat_rows(m, NOW)}
    _assert("Gent-Noord" not in rows, rows.keys())
    _assert(rows["Gent BBS"]["kind"] == "room", rows["Gent BBS"])
    _assert([r["title"] for r in ui.chat_rows(m, NOW, "direct")] == ["Alex", "Gent BBS"] or
            [r["title"] for r in ui.chat_rows(m, NOW, "direct")] == ["Gent BBS", "Alex"])


def test_recently_heard_rows():
    ui = _ui()
    rows = ui.recent_rows([{"age_s": 30, "kind": "GRP", "rssi": -54, "snr": 10.25, "hops": 11},
                           {"age_s": 125, "kind": "ADV", "rssi": -66, "snr": -2.0, "hops": 0},
                           {"age_s": 4000, "kind": "TXT", "rssi": None, "snr": None, "hops": 1}])
    _assert(rows == [("now", "GRP", "\u221254 dBm", "10.2", "11 hops"),
                     ("2m", "ADV", "\u221266 dBm", "\u22122.0", "direct"),
                     ("1h", "TXT", "\u2014", "\u2014", "1 hop")], rows)
    _assert(ui.rx_rate_text(6.0) == "~6/min" and ui.rx_rate_text(0.4) == "~0.4/min")



def test_typed_coordinates():
    ui = _ui()
    for text in ("50.8279, 3.2649", "50.8279 3.2649", " 50.8279,3.2649 ", "50,8279; 3,2649",
                 "50,8279 3,2649"):
        _assert(ui.parse_coords(text) == (50.8279, 3.2649), (text, ui.parse_coords(text)))
    _assert(ui.parse_coords("-33.9, 151.2") == (-33.9, 151.2), "south east")
    for text in ("", "50.8", "a, b", "50.8, 3.2, 1"):
        _assert(ui.parse_coords(text) is None, text)


def test_position_texts():
    ui = _ui()
    _assert(ui.position_text(None) == ("Not set", ""), ui.position_text(None))
    _assert(ui.position_text({"lat": 50.8279, "lon": 3.2649, "source": "manual"})
            == ("50.82790, 3.26490", "set by hand"), "manual")
    _assert(ui.position_text({"lat": -33.9, "lon": 151.2, "source": "gps"})[1] == "from the GPS", "gps")
    _assert(ui.gps_text({"enabled": False, "state": "off"}) == "Off", "off")
    _assert(ui.gps_text({"enabled": False, "state": "absent"}) == "No GPS found, switched off", "absent")
    _assert(ui.gps_text({"enabled": True, "state": "searching"}) == "Looking for the GPS…", "searching")
    _assert(ui.gps_text({"enabled": True, "state": "no_fix"}) == "Waiting for a fix", "no fix")
    _assert(ui.gps_text({"enabled": True, "state": "fix"}) == "Position from the GPS", "fix")


def test_route_pill_texts():
    ui = _ui()
    _assert(ui.route_pill("auto", 0, False) == "flood", "no path")
    _assert(ui.route_pill("flood", 0x02, True) == "flood (forced)", "forced")
    _assert(ui.route_pill("auto", 0x00, True) == "direct", "zero hop")
    _assert(ui.route_pill("auto", 0x42, True) == "2 hops", "two")
    _assert(ui.route_pill("manual", 0x01, True) == "1 hop (set)", "manual")


def test_path_shown_hop_by_hop():
    ui = _ui()
    _assert(ui.hops_text("a1b2c3", 1) == "A1 \u2192 B2 \u2192 C3", ui.hops_text("a1b2c3", 1))
    _assert(ui.hops_text("a1b2c3d4", 2) == "A1B2 \u2192 C3D4", "two bytes")
    _assert(ui.hops_text("", 1) == "", "none")


def test_details_of_a_received_message():
    ui = _ui()
    msg = {"ts": NOW, "sender": "Sam", "text": "hi", "incoming": True, "snr": 6.5, "rssi": -91,
           "hops": 2, "path": "a1b2", "hsize": 1, "region": "be-wvl"}
    rows = dict(ui.message_details(msg))
    _assert(rows["From"] == "Sam", rows)
    _assert(rows["Sent"] == "Fri 2 Oct 14:32", rows["Sent"])
    _assert(rows["Hops"] == "2" and rows["Path"] == "A1 \u2192 B2", rows)
    _assert(rows["Path hash"] == "1 byte" and rows["Region"] == "#be-wvl", rows)
    _assert(rows["SNR"] == "6.5 dB" and rows["RSSI"] == "\u221291 dBm", rows)
    direct = dict(ui.message_details({"ts": NOW, "sender": "Sam", "text": "x", "incoming": True,
                                      "hops": 0, "path": "", "region": "?"}))
    _assert(direct["Hops"] == "0 (direct)" and direct["Region"] == "unknown", direct)
    _assert("Path" not in direct, direct)


def test_details_of_a_sent_message():
    ui = _ui()
    rows = dict(ui.message_details({"ts": NOW, "text": "hi", "incoming": False, "tx": True,
                                    "heard": 3}))
    _assert(rows["Status"] == "heard by 3 repeaters" and "From" not in rows, rows)
    rows = dict(ui.message_details({"ts": NOW, "text": "hi", "incoming": False, "tx": True,
                                    "delivered": True, "ack_ts": NOW + 2}))
    _assert(rows["Status"] == "delivered 14:32", rows)
    rows = dict(ui.message_details({"ts": NOW, "text": "hi", "incoming": False, "failed": True}))
    _assert(rows["Status"] == "no ack after 4 tries", rows)


def test_path_hops_are_named_after_known_repeaters():
    ui = _ui()
    nodes = [{"pubkey": "a1" + "00" * 31, "name": "BE-KOR-Beekstraat", "type": 2},
             {"pubkey": "c3" + "11" * 31, "name": "Room", "type": 3},
             {"pubkey": "c3" + "22" * 31, "name": "Other", "type": 2}]
    hops = ui.path_hops("a1b2c3", 1, nodes)
    _assert(hops == [("A1", "BE-KOR-Beekstraat"), ("B2", None), ("C3", None)], hops)
    msg = {"ts": NOW, "sender": "Sam", "text": "x", "incoming": True, "hops": 3,
           "path": "a1b2c3", "hsize": 1}
    rows = dict(ui.message_details(msg, 0, nodes))
    _assert(rows["Path"] == "A1 BE-KOR-Beekstraat \u2192 B2 \u2192 C3", rows["Path"])


def test_a_path_not_recorded_is_said_so():
    ui = _ui()
    rows = dict(ui.message_details({"ts": NOW, "sender": "Sam", "text": "x", "incoming": True,
                                    "hops": 2}))
    _assert(rows["Path"] == "not recorded", rows)


def test_auto_advert_text():
    t = _ui().auto_advert_text
    _assert(t({"flood_h": 0, "zero_hop_min": 0}) == "Off")
    _assert(t({"flood_h": 12, "zero_hop_min": 0}) == "12 h")
    _assert(t({"flood_h": 0, "zero_hop_min": 30}) == "nearby 30 min")
    _assert(t({"flood_h": 3, "zero_hop_min": 120}) == "3 h · nearby 2 h",
            t({"flood_h": 3, "zero_hop_min": 120}))

def test_quiet_text():
    t = _ui().quiet_text
    _assert(t({"enabled": False, "start": 1320, "end": 420}) == "Off")
    _assert(t({"enabled": True, "start": 1410, "end": 420}) == "23:30\u201307:00",
            t({"enabled": True, "start": 1410, "end": 420}))


def test_login_line_while_logging_in_again():
    ui = _ui()
    s = {"state": "pending", "error": "no answer from Gent, logging in again",
         "pending": {"kind": "login", "auto": True}}
    _assert(ui.login_line(s) == ("no answer from Gent, logging in again", ui.WARN),
            ui.login_line(s))
    _assert(ui.login_line({"state": "pending", "pending": {"kind": "login"}})[0]
            == "Logging in\u2026")


def test_clock_warning():
    ui = _ui()
    s = {"state": "ok", "role": "guest", "clock_skew_s": 2 * 3600 + 300}
    _assert(ui.clock_warning(s).startswith("Its clock is 2 h ahead of ours."), ui.clock_warning(s))
    s["clock_skew_s"] = -600
    _assert(ui.clock_warning(s).startswith("Its clock is 10 min behind"), ui.clock_warning(s))
    _assert(ui.clock_warning({"state": "ok", "clock_skew_s": None}) is None)
    _assert(ui.clock_warning({"state": "failed", "clock_skew_s": 900}) is None)

def _located_nodes():
    now = 10 ** 6
    return now, [
        {"pubkey": "aa" * 32, "id": "aa", "type": 1, "name": "Zed", "heard_ms": now - 1000,
         "lat": 50.859, "lon": 4.35},                       # ~1.0 km north
        {"pubkey": "bb" * 32, "id": "bb", "type": 1, "name": "Amy", "heard_ms": now - 5000,
         "lat": 50.94, "lon": 4.35},                        # ~10 km north
        {"pubkey": "cc" * 32, "id": "cc", "type": 2, "name": "Bob", "heard_ms": now - 9000}]


def test_node_rows_show_the_distance_from_our_position():
    now, nodes = _located_nodes()
    rows = _ui().node_rows(nodes, now, here=(50.85, 4.35))
    _assert(rows[0]["meta"].endswith(" \u00b7 1.0 km"), rows[0]["meta"])
    _assert(rows[1]["meta"].endswith(" \u00b7 10 km"), rows[1]["meta"])
    _assert("km" not in rows[2]["meta"], rows[2]["meta"])
    _assert("km" not in _ui().node_rows(nodes, now)[0]["meta"], "no own position: no distance")
    _assert(_ui().distance_text(850) == "850 m" and _ui().distance_text(42400) == "42 km")


def test_node_rows_sorting_and_the_location_filter():
    ui = _ui()
    now, nodes = _located_nodes()
    names = lambda rows: [r["name"] for r in rows]
    _assert(names(ui.node_rows(nodes, now)) == ["Zed", "Amy", "Bob"], "as heard")
    _assert(names(ui.node_rows(nodes, now, sort="name")) == ["Amy", "Bob", "Zed"])
    _assert(names(ui.node_rows(nodes, now, sort="nearest", here=(50.85, 4.35)))
            == ["Zed", "Amy", "Bob"])
    _assert(names(ui.node_rows(nodes, now, sort="nearest")) == ["Zed", "Amy", "Bob"],
            "no own position: as heard")
    _assert(names(ui.node_rows(nodes, now, sort="message",
                               last_msg={"bb" * 32: 200, "cc" * 32: 100})) == ["Amy", "Bob", "Zed"])
    _assert(names(ui.node_rows(nodes, now, filt="located")) == ["Zed", "Amy"])


def test_favourites_come_first_and_have_a_filter():
    ui = _ui()
    now, nodes = _located_nodes()
    nodes[2]["fav"] = True                                  # Bob, heard last
    names = lambda rows: [r["name"] for r in rows]
    _assert(names(ui.node_rows(nodes, now)) == ["Bob", "Zed", "Amy"])
    _assert(names(ui.node_rows(nodes, now, sort="name")) == ["Bob", "Amy", "Zed"])
    _assert(names(ui.node_rows(nodes, now, filt="fav")) == ["Bob"])
    _assert([r["fav"] for r in ui.node_rows(nodes, now)] == [True, False, False])


def test_day_text():
    t = _ui().day_text
    _assert(t(NOW - 3600, NOW) == "Today")                       # NOW: Fri 2 Oct 2026, 14:32 UTC
    _assert(t(NOW - 15 * 3600, NOW) == "Yesterday", t(NOW - 15 * 3600, NOW))
    _assert(t(NOW - 3 * 86400, NOW) == "Tuesday", t(NOW - 3 * 86400, NOW))
    _assert(t(NOW - 20 * 86400, NOW) == "12 Sep", t(NOW - 20 * 86400, NOW))
    _assert(t(NOW - 400 * 86400, NOW) == "28 Aug 2025", t(NOW - 400 * 86400, NOW))
    _assert(t(NOW - 15 * 3600, NOW, tz_s=3600) == "Today", "local midnight, not UTC")


KEY = "9cd8fcf22a47333b591d96a2b848b73f457b1bb1a3ea2453a885f9e5787765b1"


def test_message_links():
    links = _ui().message_links
    got = links("add me: meshcore://contact/add?name=Example+Contact&public_key=%s&type=1 ok" % KEY)
    _assert(got == [{"kind": "contact", "uri": "meshcore://contact/add?name=Example+Contact"
                     "&public_key=%s&type=1" % KEY, "name": "Example Contact", "key": KEY,
                     "type": 1}], got)
    got = links("join meshcore://channel/add?name=Ops&secret=8b3387e9c5cdea6ac9e5edbaa115cd72.")
    _assert(got == [{"kind": "channel", "uri": "meshcore://channel/add?name=Ops&secret="
                     "8b3387e9c5cdea6ac9e5edbaa115cd72", "name": "Ops"}], got)
    _assert(links("see #test and #Gent-Noord!") == [{"kind": "hashtag", "name": "#test"},
                                                    {"kind": "hashtag", "name": "#gent-noord"}])
    _assert(links("meet at 50.8503, 4.3517 at noon") ==
            [{"kind": "map", "lat": 50.8503, "lon": 4.3517}], links("meet at 50.8503, 4.3517"))
    _assert(links("geo:51.05,3.72") == [{"kind": "map", "lat": 51.05, "lon": 3.72}])
    _assert(links("https://maps.google.com/?q=-33.86,151.21") ==
            [{"kind": "map", "lat": -33.86, "lon": 151.21}])


def test_message_links_ignores_what_is_not_a_link():
    links = _ui().message_links
    for text in ("version 1.2.3", "50, 4", "123.45, 678.9", "price 3.50,4.20 eur",
                 "a # alone", "meshcore://contact/add?name=x&public_key=zz", "C# and F#", ""):
        _assert(links(text) == [], (text, links(text)))
    _assert(len(links("#a #b #c #d #e")) == 3, "at most three")


def test_link_text():
    t = _ui().link_text
    _assert(t({"kind": "contact", "name": "Bob", "key": KEY}, False) == "Add Bob")
    _assert(t({"kind": "contact", "name": None, "key": KEY}, True) == "Open 9CD8FCF2")
    _assert(t({"kind": "channel", "name": "Ops"}, False) == "Join Ops")
    _assert(t({"kind": "hashtag", "name": "#test"}, True) == "Open #test")
    _assert(t({"kind": "map", "lat": 50.8503, "lon": 4.3517}, False) == "Map 50.8503, 4.3517")


def test_emoji_order_puts_the_common_ones_first():
    order = _ui().emoji_order
    got = order(["\U0001F346", "\u2764\ufe0f", "\U0001F44D", "\U0001F351"])
    _assert(got == ["\U0001F44D", "\u2764\ufe0f", "\U0001F346", "\U0001F351"], got)
    _assert(order([]) == [])


def test_a_position_of_zero_zero_is_no_position():
    now, nodes = _located_nodes()
    nodes[2].update(lat=0.0, lon=0.0)
    rows = _ui().node_rows(nodes, now, filt="located", here=(50.85, 4.35))
    _assert([r["name"] for r in rows] == ["Zed", "Amy"], rows)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
