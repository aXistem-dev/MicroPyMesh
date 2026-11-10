"""Payloads for talking to repeaters and room servers: login, requests (status, neighbours,
telemetry, owner info), their replies, and trace. Layouts follow the MeshCore firmware
(simple_repeater, simple_room_server, Mesh.cpp); integers are little-endian.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_server.py
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

fake_mpos.install()
import meshcore_dm as dm          # noqa: E402
import meshcore_server as srv     # noqa: E402

SECRET = bytes(range(32))
OUR_PUB = bytes([0xAB]) + bytes(31)


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _open(payload, header=2):
    """Decrypt an envelope [header bytes][MAC 2][cipher] with SECRET."""
    body = payload[header:]
    return dm.mac_then_decrypt(SECRET, body[:2], body[2:])


# --- login ------------------------------------------------------------------- #

def test_login_to_a_repeater():
    p = srv.build_login(SECRET, OUR_PUB, 0x3A, 1790000000, "secret")
    _assert(p[0] == 0x3A and p[1:33] == OUR_PUB, p[:33])
    pt = _open(p, 33)
    _assert(pt[:4] == struct.pack("<I", 1790000000), pt)
    _assert(pt[4:10] == b"secret" and pt[10:].strip(b"\x00") == b"", pt)


def test_guest_login_to_a_room_carries_sync_since():
    p = srv.build_login(SECRET, OUR_PUB, 0x7C, 1790000000, "", sync_since=1789990000)
    pt = _open(p, 33)
    _assert(pt[:8] == struct.pack("<II", 1790000000, 1789990000), pt)
    _assert(pt[8:].strip(b"\x00") == b"", pt)


def test_password_is_cut_to_fifteen_bytes():
    p = srv.build_login(SECRET, OUR_PUB, 0x3A, 1, "x" * 20)
    _assert(_open(p, 33)[4:].rstrip(b"\x00") == b"x" * 15)


def test_login_reply():
    body = struct.pack("<IBBBB", 1790000005, 0, 0, 1, 3) + b"\x01\x02\x03\x04" + b"\x02"
    r = srv.parse_login_reply(body)
    _assert(r == {"ok": True, "server_ts": 1790000005, "admin": True, "permissions": 3,
                  "role": "admin", "fw_level": 2, "keep_alive_s": 0}, r)
    old = srv.parse_login_reply(struct.pack("<IBBBB", 5, 0, 8, 0, 0) + bytes(5))
    _assert(old["keep_alive_s"] == 128, old)    # older firmware: suggested interval / 16
    guest = srv.parse_login_reply(struct.pack("<IBBBB", 5, 0, 0, 2, 0) + bytes(5))
    _assert(guest["role"] == "guest" and not guest["admin"], guest)
    rw = srv.parse_login_reply(struct.pack("<IBBBB", 5, 0, 0, 0, 2) + bytes(5))
    _assert(rw["role"] == "read-write", rw)
    _assert(srv.parse_login_reply(b"\x00" * 4) is None)


def test_legacy_ok_reply():
    _assert(srv.parse_login_reply(struct.pack("<I", 9) + b"OK")["ok"])


# --- requests and replies --------------------------------------------------- #

def test_request_envelope():
    p = srv.build_request(SECRET, OUR_PUB, 0x3A, 1790000001, srv.REQ_GET_STATUS)
    _assert(p[0] == 0x3A and p[1] == OUR_PUB[0], p[:2])
    pt = _open(p)
    _assert(pt[:4] == struct.pack("<I", 1790000001) and pt[4] == srv.REQ_GET_STATUS, pt)
    _assert(len(pt.rstrip(b"\x00")) <= 13)


def test_neighbours_request_params():
    p = srv.build_request(SECRET, OUR_PUB, 0x3A, 7, srv.REQ_GET_NEIGHBOURS,
                          srv.neighbours_params(count=20, offset=0, order=2, prefix_len=4))
    pt = _open(p)
    _assert(pt[4] == 0x06 and pt[5:11] == bytes([0, 20, 0, 0, 2, 4]), pt[:11])


def test_response_tag_and_body():
    tag, body = srv.parse_response(struct.pack("<I", 77) + b"\x10\x20" + bytes(10))
    _assert(tag == 77 and body[:2] == b"\x10\x20")


REPEATER_STATS = struct.pack(
    "<HHhhIIIIIIIIHhHHII",
    4020, 0, -112, -96, 18233, 9120, 1800, 1054800, 5000, 4120, 12000, 6233,
    3, -14, 7, 41, 5500, 2)


def test_repeater_status():
    st = srv.parse_status(REPEATER_STATS)
    _assert(st["battery_mv"] == 4020 and st["noise_floor"] == -112, st)
    _assert(st["last_rssi"] == -96 and st["last_snr"] == -3.5, st)
    _assert(st["packets_rx"] == 18233 and st["packets_tx"] == 9120, st)
    _assert(st["uptime_s"] == 1054800 and st["airtime_s"] == 1800 and st["rx_airtime_s"] == 5500, st)
    _assert(st["receive_errors"] == 2 and "posted" not in st, st)


def test_room_status_and_short_or_padded_bodies():
    room = REPEATER_STATS[:48] + struct.pack("<HH", 12, 30) + bytes(12)   # 52 + padding
    st = srv.parse_status(room, room=True)
    _assert(st["posted"] == 12 and st["post_pushes"] == 30 and "rx_airtime_s" not in st, st)
    short = srv.parse_status(REPEATER_STATS[:24])
    _assert(short["uptime_s"] == 1054800 and "flood_tx" not in short, short)
    _assert(srv.parse_status(b"\x01") == {}, "too short for anything")


def test_neighbours():
    body = struct.pack("<hh", 3, 2)
    body += bytes.fromhex("f1a73333") + struct.pack("<Ib", 120, -14)
    body += bytes.fromhex("3a444444") + struct.pack("<Ib", 3600, 17)
    total, rows = srv.parse_neighbours(body + bytes(5), prefix_len=4)
    _assert(total == 3, total)
    _assert(rows == [{"prefix": "f1a73333", "secs_ago": 120, "snr": -3.5},
                     {"prefix": "3a444444", "secs_ago": 3600, "snr": 4.25}], rows)


def test_telemetry_lpp():
    body = bytes([1, 116]) + struct.pack(">H", 402)           # ch1 voltage 4.02 V
    body += bytes([1, 103]) + struct.pack(">h", -45)          # ch1 temperature -4.5 C
    body += bytes([1, 136]) + bytes.fromhex("07caf0" "00913c" "000064")   # GPS
    body += bytes([2, 104, 101])                              # ch2 humidity 50.5 %
    body += bytes([2, 115]) + struct.pack(">H", 10132)        # ch2 pressure 1013.2 hPa
    rows = srv.parse_lpp(body + bytes(3))
    _assert(rows[0] == {"channel": 1, "kind": "voltage", "value": 4.02}, rows[0])
    _assert(rows[1] == {"channel": 1, "kind": "temperature", "value": -4.5}, rows[1])
    gps = rows[2]
    _assert(gps["kind"] == "gps" and abs(gps["value"][0] - 51.0704) < 1e-6
            and abs(gps["value"][1] - 3.718) < 1e-6 and abs(gps["value"][2] - 1.0) < 1e-6, gps)
    _assert(rows[3] == {"channel": 2, "kind": "humidity", "value": 50.5}, rows[3])
    _assert(rows[4] == {"channel": 2, "kind": "pressure", "value": 1013.2}, rows[4])
    _assert(len(rows) == 5, rows)


def test_telemetry_stops_at_an_unknown_type():
    body = bytes([1, 116]) + struct.pack(">H", 402) + bytes([3, 250, 1, 2, 3])
    _assert([r["kind"] for r in srv.parse_lpp(body)] == ["voltage"])


def test_owner_info():
    _assert(srv.parse_owner_info(b"v1.9.1\nGent-Noord\nRobin\x00\x00")
            == {"firmware": "v1.9.1", "name": "Gent-Noord", "owner": "Robin"})


def test_owner_info_keeps_every_line_of_the_owner_text():
    r = srv.parse_owner_info(b"v1.18.0\nGent-Noord\nRadio-Actief\nmeshwiki.radio-actief.be\x00")
    _assert(r["owner"] == "Radio-Actief\nmeshwiki.radio-actief.be", r)


def test_telemetry_reads_on_past_the_firmware_extras():
    body = bytes([2, 100]) + struct.pack(">I", 51234)          # BME680 gas resistance
    body += bytes([2, 113]) + struct.pack(">hhh", 10, -20, 1000)   # accelerometer, G/1000
    body += bytes([2, 132]) + struct.pack(">H", 270)           # direction
    body += bytes([2, 133]) + struct.pack(">I", 1790000000)    # unix time
    body += bytes([2, 134]) + struct.pack(">hhh", 150, 0, -150)   # gyrometer, /100
    body += bytes([2, 135, 255, 128, 0])                       # colour
    body += bytes([2, 142, 1])                                 # switch
    body += bytes([1, 116]) + struct.pack(">H", 402)
    rows = srv.parse_lpp(body)
    kinds = [r["kind"] for r in rows]
    _assert(kinds == ["generic", "accelerometer", "direction", "unixtime", "gyrometer",
                      "colour", "switch", "voltage"], kinds)
    _assert(rows[0]["value"] == 51234 and rows[1]["value"] == (0.01, -0.02, 1.0), rows[:2])
    _assert(rows[4]["value"] == (1.5, 0.0, -1.5) and rows[5]["value"] == (255, 128, 0), rows[4:6])


def test_status_fields_older_firmware_does_not_send():
    padded = REPEATER_STATS[:52] + bytes(8)          # v1.9-v1.11: no receive_errors
    st = srv.parse_status(padded, fw_level=1)
    _assert(st["rx_airtime_s"] == 5500 and "receive_errors" not in st, st)
    old = REPEATER_STATS[:48] + bytes(12)            # < v1.8 decrypts to the same length
    st = srv.parse_status(old, fw_level=0)
    _assert("rx_airtime_s" not in st and "receive_errors" not in st, st)
    st = srv.parse_status(REPEATER_STATS, fw_level=2)
    _assert(st["receive_errors"] == 2, st)


def test_admin_login_on_firmware_before_v1_9():
    r = srv.parse_login_reply(struct.pack("<IBBBB", 5, 0, 0, 1, 0) + bytes(4))   # 12 bytes
    _assert(r["admin"] and r["role"] == "admin" and r["fw_level"] == 0, r)


def test_what_each_node_type_answers():
    _assert(srv.capabilities(2, 2) == ("status", "neighbours", "telemetry", "owner", "access"))
    _assert(srv.capabilities(2, 1) == ("status", "neighbours", "telemetry", "access"))
    _assert(srv.capabilities(2, 0) == ("status", "telemetry", "access"))
    _assert(srv.capabilities(3, 1) == ("status", "telemetry", "access"))
    _assert(srv.capabilities(4, 1) == ("telemetry", "access"))


def test_access_list():
    body = bytes.fromhex("a1b2c3d4e5f6") + bytes([3]) + bytes.fromhex("0102030405ff") + bytes([2])
    rows = srv.parse_access_list(body + bytes(7) + bytes(5))       # zero filler from padding
    _assert(rows == [{"prefix": "a1b2c3d4e5f6", "role": "admin", "permissions": 3},
                     {"prefix": "0102030405ff", "role": "read-write", "permissions": 2}], rows)


# --- trace ------------------------------------------------------------------ #

def test_trace_payload_and_reply():
    p = srv.build_trace(0x11223344, 0, bytes([0xF1, 0x3A, 0xF1]))
    _assert(p == struct.pack("<II", 0x11223344, 0) + b"\x00" + bytes([0xF1, 0x3A, 0xF1]), p)
    r = srv.parse_trace(p, snr_path=bytes([0xF2, 0x10, 0x0E]), final_snr=6.25)
    _assert(r["tag"] == 0x11223344 and r["hashes"] == [0xF1, 0x3A, 0xF1], r)
    _assert(r["hop_snrs"] == [-3.5, 4.0, 3.5] and r["final_snr"] == 6.25, r)


def test_two_byte_trace_hashes():
    p = srv.build_trace(5, 0, bytes([0xF1, 0xA7, 0x3A, 0x44]), hash_size=2)
    _assert(p[8] == 1, p)
    _assert(srv.parse_trace(p, snr_path=b"\x04\x08")["hashes"] == [0xF1A7, 0x3A44])


# --- timeouts ----------------------------------------------------------------- #

def test_timeouts_follow_the_firmware():
    _assert(srv.timeout_ms(airtime_ms=480, hops=None) == 500 + 16 * 480)
    _assert(srv.timeout_ms(airtime_ms=480, hops=2) == 500 + (6 * 480 + 250) * 3)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
