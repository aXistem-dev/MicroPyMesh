"""TX scheduling: ACK delay, the direct-DM ACK rule and the reciprocal PATH return.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_tx_timing.py
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
    peer_pub, peer_prv, secret = fake_mpos.with_peer(env, m)
    return env, m, peer_pub, secret


def _dm_frame(m, peer_pub, secret, route, path=b"", text="hi", ts=1700000000):
    import meshcore_dm
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len, PAYLOAD_TYPE_TXT_MSG
    our_pub, _ = m.get_identity()
    payload, _ = meshcore_dm.encode_dm(secret, peer_pub, our_pub[0], text, ts)
    return MeshCorePacket(make_header(route, PAYLOAD_TYPE_TXT_MSG),
                          encode_path_len(len(path)), path, payload).to_bytes()


def _path_frame(m, peer_pub, secret, route, flood_path, return_path):
    import meshcore_dm
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len, PAYLOAD_TYPE_PATH
    our_pub, _ = m.get_identity()
    payload = meshcore_dm.build_path_ack(secret, our_pub[0], peer_pub[0], return_path,
                                         encode_path_len(len(return_path)), bytes(6))
    return MeshCorePacket(make_header(route, PAYLOAD_TYPE_PATH),
                          encode_path_len(len(flood_path)), flood_path, payload).to_bytes()


def test_ack_is_delayed():
    from meshcore_packet import ROUTE_TYPE_FLOOD
    env, m, peer_pub, secret = _setup()
    m._ingest(_dm_frame(m, peer_pub, secret, ROUTE_TYPE_FLOOD, b"\xA1"), rssi=-90, snr=5)
    _assert(fake_mpos.drain(m) == 0, "ack went out at once")
    env.now_ms += 300
    _assert(fake_mpos.drain(m) == 1)


def test_direct_dm_without_path_gets_flood_bare_ack():
    from meshcore_packet import ROUTE_TYPE_DIRECT, ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_ACK
    env, m, peer_pub, secret = _setup()
    m._ingest(_dm_frame(m, peer_pub, secret, ROUTE_TYPE_DIRECT), rssi=-90, snr=5)
    env.now_ms += 300
    fake_mpos.drain(m)
    pkts = fake_mpos.sent_packets(m.chip)
    _assert(len(pkts) == 1, pkts)
    _assert(pkts[0].payload_type == PAYLOAD_TYPE_ACK, pkts[0].payload_type)
    _assert(pkts[0].route_type == ROUTE_TYPE_FLOOD, pkts[0].route_type)


def test_direct_dm_with_known_path_gets_direct_ack():
    from meshcore_packet import ROUTE_TYPE_DIRECT, PAYLOAD_TYPE_ACK, encode_path_len
    env, m, peer_pub, secret = _setup()
    c = m._contacts[peer_pub.hex()]
    c["path"], c["path_raw"] = b"\x3A", encode_path_len(1)
    m._ingest(_dm_frame(m, peer_pub, secret, ROUTE_TYPE_DIRECT), rssi=-90, snr=5)
    env.now_ms += 300
    fake_mpos.drain(m)
    pkts = fake_mpos.sent_packets(m.chip)
    _assert(len(pkts) == 1, pkts)
    _assert(pkts[0].payload_type == PAYLOAD_TYPE_ACK)
    _assert(pkts[0].route_type == ROUTE_TYPE_DIRECT)
    _assert(pkts[0].path == b"\x3A", pkts[0].path)


def test_flood_path_triggers_reciprocal_return():
    import meshcore_dm
    from meshcore_packet import ROUTE_TYPE_FLOOD, ROUTE_TYPE_DIRECT, PAYLOAD_TYPE_PATH
    env, m, peer_pub, secret = _setup()
    m._ingest(_path_frame(m, peer_pub, secret, ROUTE_TYPE_FLOOD, b"\xD4\xE5", b"\xC3"),
              rssi=-90, snr=5)
    _assert(m._contacts[peer_pub.hex()]["path"] == b"\xC3")
    env.now_ms += 499
    _assert(fake_mpos.drain(m) == 0, "path return before 500 ms")
    env.now_ms += 1
    _assert(fake_mpos.drain(m) == 1)
    pkt = fake_mpos.sent_packets(m.chip)[0]
    _assert(pkt.route_type == ROUTE_TYPE_DIRECT and pkt.payload_type == PAYLOAD_TYPE_PATH)
    _assert(pkt.path == b"\xC3", pkt.path)
    our_pub, _ = m.get_identity()
    dec = meshcore_dm.decode_path(pkt.payload, peer_pub[0], [(our_pub, secret)])
    _assert(dec is not None and dec["path"] == b"\xD4\xE5", dec)


def test_direct_path_no_return():
    from meshcore_packet import ROUTE_TYPE_DIRECT
    env, m, peer_pub, secret = _setup()
    m._ingest(_path_frame(m, peer_pub, secret, ROUTE_TYPE_DIRECT, b"", b"\xC3"),
              rssi=-90, snr=5)
    env.now_ms += 2000
    _assert(fake_mpos.drain(m) == 0)


def test_reset_route_forgets_the_path_and_announces_it():
    from meshcore_packet import encode_path_len
    env, m, peer_pub, secret = _setup()
    events = []
    m.add_subscriber(lambda ev, data: events.append(ev))
    c = m._contacts[peer_pub.hex()]
    c["path"], c["path_raw"] = b"\x3A", encode_path_len(1)
    _assert(m.reset_route(peer_pub.hex()) is True)
    _assert(not m.get_contact(peer_pub.hex()).get("path"))
    _assert("contacts" in events, events)
    _assert(m.reset_route(peer_pub.hex()) is False)     # nothing left to forget


def test_queue_order_is_by_due_time():
    env, m, peer_pub, secret = _setup()
    m._enqueue_tx(b"\x11late", delay_ms=500)
    m._enqueue_tx(b"\x11now")
    env.now_ms += 600
    fake_mpos.drain(m)
    _assert(m.chip.sent == [b"\x11now", b"\x11late"], m.chip.sent)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
