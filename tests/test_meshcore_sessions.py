"""Sessions with repeaters and room servers: login (guest/admin, flooded or direct), requests
and their replies, timeouts, ping and trace, and room posts. A fake server plays the
firmware's side: it decrypts what we send and answers like simple_repeater/simple_room_server.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_sessions.py
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

SERVER_SEED = bytes([0x42]) * 32
ROOM = 3
REPEATER = 2


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


class FakeServer:
    """The other end: a repeater (or room server) that answers logins and requests."""

    def __init__(self, env, m, kind=REPEATER, admin_pw="hunter2", name="Gent-Noord"):
        import meshcore_crypto as mc
        self.env, self.m, self.kind, self.admin_pw = env, m, kind, admin_pw
        self.pub, self.prv = mc.generate_keypair(seed=SERVER_SEED)
        self.hex = self.pub.hex()
        pub, raw = fake_mpos.advert_frame(SERVER_SEED, name, 1790000000, node_type=kind)
        m._ingest(raw, rssi=-90, snr=6)
        our_pub, _ = m.get_identity()
        self.our_pub = our_pub
        self.secret = mc.shared_secret(self.prv, our_pub)
        self.last = None            # last decoded request
        self.posts = []

    # --- reading what we sent -------------------------------------------- #
    def take(self):
        """Drain the manager's TX queue; return the decoded packets addressed to us (server)."""
        import meshcore_dm as dm
        from meshcore_packet import MeshCorePacket
        fake_mpos.drain(self.m)
        out = []
        for raw in self.m.chip.sent:
            pkt = MeshCorePacket.parse(raw)
            p = bytes(pkt.payload)
            if pkt.payload_type == 0x07 and p[0] == self.pub[0]:
                pt = dm.mac_then_decrypt(self.secret, p[33:35], p[35:])
                out.append(("login", pkt, pt))
            elif pkt.payload_type == 0x00 and p[0] == self.pub[0]:
                pt = dm.mac_then_decrypt(self.secret, p[2:4], p[4:])
                out.append(("req", pkt, pt))
            elif pkt.payload_type == 0x09:
                out.append(("trace", pkt, p))
            elif pkt.payload_type == 0x08 and p[:1] == bytes([self.pub[0]]):
                out.append(("path", pkt, p))
            elif pkt.payload_type == 0x03:          # a bare ACK: just the ack hash
                out.append(("ack", pkt, p))
        self.m.chip.sent.clear()
        return out

    # --- answering ------------------------------------------------------- #
    def _envelope(self, plaintext):
        import meshcore_dm as dm
        return bytes([self.our_pub[0], self.pub[0]]) + dm.encrypt_then_mac(self.secret, plaintext)

    def response(self, plaintext, flood_path=None):
        """A RESPONSE (direct), or a flooded PATH carrying it as the extra (reply to a flood)."""
        import meshcore_dm as dm
        from meshcore_packet import (MeshCorePacket, make_header, encode_path_len,
                                     ROUTE_TYPE_FLOOD, ROUTE_TYPE_DIRECT)
        if flood_path is not None:
            payload = dm.build_path_return(self.secret, self.our_pub[0], self.pub[0], flood_path,
                                           encode_path_len(len(flood_path)), extra_type=0x01,
                                           extra=plaintext)
            pkt = MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, 0x08), encode_path_len(1),
                                 b"\x99", payload)
        else:
            pkt = MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, 0x01), encode_path_len(0), b"",
                                 self._envelope(plaintext))
        self.m._ingest(pkt.to_bytes(), rssi=-80, snr=7)

    def login_reply(self, pt, admin, perms, flood_path=None):
        ts = struct.unpack("<I", pt[:4])[0]
        body = struct.pack("<IBBBB", ts + 5, 0, 0, 1 if admin else 0, perms) + b"\x01\x02\x03\x04\x02"
        self.response(body, flood_path)

    def answer_login(self, flood_path=None):
        (kind, pkt, pt), = [x for x in self.take() if x[0] == "login"]
        self.last = pt
        pw_at = 8 if self.kind == ROOM else 4
        pw = pt[pw_at:].rstrip(b"\x00").decode()
        if pw == self.admin_pw:
            self.login_reply(pt, True, 3, flood_path)
        elif pw == "":
            self.login_reply(pt, False, 0, flood_path)
        return pt   # a wrong password gets no reply

    def answer_request(self, body_for):
        reqs = [x for x in self.take() if x[0] == "req"]
        (kind, pkt, pt), = reqs
        tag, rtype = struct.unpack("<IB", pt[:5])
        self.last = pt
        body = body_for(rtype, pt[5:])
        if body is not None:
            self.response(struct.pack("<I", tag) + body)
        return tag, rtype

    def post(self, ts, author_prefix, text, flood=False):
        """A room post pushed to us: SIGNED_PLAIN with the author's key prefix."""
        from meshcore_packet import (MeshCorePacket, make_header, encode_path_len,
                                     ROUTE_TYPE_FLOOD, ROUTE_TYPE_DIRECT)
        pt = struct.pack("<IB", ts, 2 << 2) + author_prefix + text.encode()
        route = ROUTE_TYPE_FLOOD if flood else ROUTE_TYPE_DIRECT
        pkt = MeshCorePacket(make_header(route, 0x02), encode_path_len(0), b"", self._envelope(pt))
        self.m._ingest(pkt.to_bytes(), rssi=-80, snr=7)
        return pt


STATUS = struct.pack("<HHhhIIIIIIIIHhHHII", 4020, 0, -112, -96, 18233, 9120, 1800, 1054800,
                     5000, 4120, 12000, 6233, 3, -14, 7, 41, 5500, 2)


def _setup(kind=REPEATER):
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    events = []
    m.add_subscriber(lambda ev, data: events.append((ev, data)))
    return env, m, FakeServer(env, m, kind), events


# --- login ------------------------------------------------------------------ #

def test_guest_login_by_flood_learns_the_route():
    env, m, srv, events = _setup()
    ok, err = m.login(srv.hex)
    _assert(ok, err)
    _assert(m.server_session(srv.hex)["state"] == "pending")
    srv.answer_login(flood_path=b"\x3a")
    s = m.server_session(srv.hex)
    _assert(s["state"] == "ok" and s["role"] == "guest" and not s["admin"], s)
    _assert(m.get_contact(srv.hex)["path"] == b"\x3a", m.get_contact(srv.hex))
    env.now_ms += 5000                    # the reciprocal PATH goes out after a short delay
    _assert(any(k == "path" for k, _, _ in srv.take()), "a flooded reply is answered with our route")
    _assert(("server", srv.hex) in events, events)


def test_admin_login_direct():
    env, m, srv, events = _setup()
    m.add_contact(srv.hex, node_type=REPEATER)
    m.get_contact(srv.hex).update(path=b"\x3a", path_raw=1)
    m.login(srv.hex, "hunter2")
    srv.answer_login()
    s = m.server_session(srv.hex)
    _assert(s["state"] == "ok" and s["role"] == "admin" and s["admin"], s)


def test_wrong_password_times_out():
    env, m, srv, events = _setup()
    m.login(srv.hex, "nope")
    srv.answer_login()
    env.now_ms += 60000
    m._server_tick()
    s = m.server_session(srv.hex)
    _assert(s["state"] == "failed" and "no answer" in s["error"], s)
    ok, err = m.login(srv.hex, "hunter2")
    _assert(ok, err)
    srv.answer_login()
    _assert(m.server_session(srv.hex)["state"] == "ok")


def test_a_server_clock_far_off_ours_is_noted():
    env, m, srv, events = _setup()
    m.login(srv.hex)
    (kind, pkt, pt), = [x for x in srv.take() if x[0] == "login"]
    ts = struct.unpack("<I", pt[:4])[0]
    srv.login_reply(struct.pack("<I", ts + 3600) + pt[4:], False, 0, flood_path=b"\x3a")
    skew = m.server_session(srv.hex)["clock_skew_s"]
    _assert(3500 < skew < 3700, skew)
    m.login(srv.hex)
    srv.answer_login()                    # within a few seconds: nothing to say
    _assert(m.server_session(srv.hex)["clock_skew_s"] is None)


def test_requests_need_a_login():
    env, m, srv, events = _setup()
    ok, err = m.request_server(srv.hex, "status")
    _assert(not ok and "log in" in err, err)


# --- requests ---------------------------------------------------------------- #

def _logged_in(kind=REPEATER):
    env, m, srv, events = _setup(kind)
    m.login(srv.hex)
    srv.answer_login(flood_path=b"\x3a")
    env.now_ms += 5000
    srv.take()
    return env, m, srv, events


def test_status():
    env, m, srv, events = _logged_in()
    _assert(m.request_server(srv.hex, "status")[0])
    tag, rtype = srv.answer_request(lambda t, p: STATUS)
    _assert(rtype == 0x01)
    r = m.server_session(srv.hex)["results"]["status"]
    _assert(r["data"]["battery_mv"] == 4020 and r["data"]["last_snr"] == -3.5, r)
    _assert(r["at"] > 0, r)


def test_results_carry_mesh_time_when_the_clock_is_unset():
    env, m, srv, events = _logged_in()          # the login reply says 1790000005
    mm = sys.modules["meshcore_manager"]
    real = mm.unix_time
    mm.unix_time = lambda: 3600                # a device that never synced: early 2000
    try:
        m.request_server(srv.hex, "status")
        srv.answer_request(lambda t, p: STATUS)
        at = m.server_session(srv.hex)["results"]["status"]["at"]
        _assert(at >= 1790000005, at)
    finally:
        mm.unix_time = real


def test_room_status_uses_the_room_layout():
    env, m, srv, events = _logged_in(ROOM)
    m.request_server(srv.hex, "status")
    srv.answer_request(lambda t, p: STATUS[:48] + struct.pack("<HH", 12, 30))
    d = m.server_session(srv.hex)["results"]["status"]["data"]
    _assert(d["posted"] == 12, d)


def test_neighbours_telemetry_owner():
    env, m, srv, events = _logged_in()
    m.request_server(srv.hex, "neighbours")
    tag, rtype = srv.answer_request(
        lambda t, p: struct.pack("<hh", 1, 1) + bytes.fromhex("3a444444") + struct.pack("<Ib", 60, 16))
    _assert(rtype == 0x06)
    n = m.server_session(srv.hex)["results"]["neighbours"]["data"]
    _assert(n["total"] == 1 and n["rows"][0]["prefix"] == "3a444444" and n["rows"][0]["snr"] == 4.0, n)
    m.request_server(srv.hex, "telemetry")
    srv.answer_request(lambda t, p: bytes([1, 116]) + struct.pack(">H", 402))
    t = m.server_session(srv.hex)["results"]["telemetry"]["data"]
    _assert(t == [{"channel": 1, "kind": "voltage", "value": 4.02}], t)
    m.request_server(srv.hex, "owner")
    srv.answer_request(lambda t, p: b"v1.9\nGent-Noord\nRobin")
    _assert(m.server_session(srv.hex)["results"]["owner"]["data"]["owner"] == "Robin")


def test_a_late_reply_does_not_land_on_a_newer_request():
    env, m, srv, events = _logged_in()
    m.request_server(srv.hex, "status")
    old = [x for x in srv.take() if x[0] == "req"][0][2]
    old_tag = struct.unpack("<I", old[:4])[0]
    env.now_ms += 60000
    m._server_tick()
    _assert(m.server_session(srv.hex)["error"], "the first request timed out")
    srv.answer_login()                                       # it logged in again by itself
    m.request_server(srv.hex, "neighbours")
    srv.take()
    srv.response(struct.pack("<I", old_tag) + STATUS)        # the late status reply
    s = m.server_session(srv.hex)
    _assert("status" not in s["results"] and s["pending"]["kind"] == "neighbours", s)


def test_request_timeout_reports_and_clears():
    env, m, srv, events = _logged_in()
    m.request_server(srv.hex, "status")
    srv.take()
    env.now_ms += 60000
    m._server_tick()
    s = m.server_session(srv.hex)
    _assert("no answer" in s["error"] and s["pending"]["kind"] == "login", s)   # logs in again


# --- ping and trace ---------------------------------------------------------- #

def test_ping_is_a_trace_to_the_node():
    from meshcore_packet import MeshCorePacket, make_header, ROUTE_TYPE_DIRECT
    env, m, srv, events = _logged_in()
    _assert(m.ping(srv.hex)[0])
    (kind, pkt, payload), = [x for x in srv.take() if x[0] == "trace"]
    _assert(pkt.is_route_direct() and pkt.path == b"", pkt)
    _assert(payload[9:] == bytes([srv.pub[0]]), payload)
    env.now_ms += 350
    back = MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, 0x09), 1, bytes([0x18]), payload)
    m._ingest(back.to_bytes(), rssi=-80, snr=5.5)
    r = m.server_session(srv.hex)["results"]["ping"]["data"]
    _assert(r["hop_snrs"] == [6.0] and r["final_snr"] == 5.5 and r["rtt_ms"] == 350, r)


def test_trace_follows_the_route_there_and_back():
    env, m, srv, events = _logged_in()
    m.get_contact(srv.hex).update(path=b"\x3a\x77", path_raw=2)
    m.trace(srv.hex)
    (kind, pkt, payload), = [x for x in srv.take() if x[0] == "trace"]
    _assert(payload[9:] == bytes([0x3a, 0x77, srv.pub[0], 0x77, 0x3a]), payload)


# --- rooms --------------------------------------------------------------------- #

def test_room_login_sends_sync_since_and_posts_arrive():
    import hashlib
    env, m, srv, events = _setup(ROOM)
    m.login(srv.hex)
    pt = srv.answer_login(flood_path=b"\x3a")
    _assert(struct.unpack("<I", pt[4:8])[0] == 0, "first login: everything since 0")
    env.now_ms += 5000
    srv.take()
    alex = bytes.fromhex("a3111111")
    m._nodes["a3" + "11" * 31] = {"pubkey": "a3" + "11" * 31, "name": "Alex", "type": 1}
    post = srv.post(1790000100, alex, "hello room")
    env.now_ms += 5000                    # acks go out after a short delay
    msgs = m.get_dm_messages(srv.hex)
    _assert(msgs[-1]["text"] == "hello room" and msgs[-1]["sender"] == "Alex", msgs[-1])
    _assert(msgs[-1]["incoming"], msgs[-1])
    sent = srv.take()
    acks = [p for k, _, p in sent if k == "ack"]
    expect = hashlib.sha256(post[:9 + len("hello room")] + srv.our_pub).digest()[:4]
    _assert(acks and acks[0][:4] == expect, (acks, expect.hex()))
    _assert(m.get_contact(srv.hex)["sync_since"] == 1790000100, m.get_contact(srv.hex))
    # the next login asks only for newer posts
    m.login(srv.hex)
    pt = srv.answer_login()
    _assert(struct.unpack("<I", pt[4:8])[0] == 1790000100, pt[:8])


def test_unknown_author_shows_its_key_prefix():
    env, m, srv, events = _setup(ROOM)
    m.login(srv.hex)
    srv.answer_login(flood_path=b"\x3a")
    srv.post(1790000100, bytes.fromhex("c0ffee00"), "hi")
    _assert(m.get_dm_messages(srv.hex)[-1]["sender"] == "C0FFEE00")


if __name__ == "__main__":
    fake_mpos.run_all(globals())
