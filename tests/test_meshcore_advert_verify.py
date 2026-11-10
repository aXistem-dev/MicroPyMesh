"""Advert handling: signature check, replay guard, node types and signal metadata.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_advert_verify.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

SEED = bytes([9]) * 32


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup(native=True):
    env = fake_mpos.install(native=fake_mpos.make_fake_native() if native else None)
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    events = []
    m.add_subscriber(lambda ev, data: events.append((ev, data)))
    return env, m, events


def _nodes(m):
    return {n["pubkey"]: n for n in m.get_learned_companions()}


def test_bad_signature_dropped_when_native():
    env, m, events = _setup()
    pub, raw = fake_mpos.advert_frame(SEED, "Mallory", 1000, tamper=True)
    m._ingest(raw, rssi=-90, snr=3)
    _assert(pub.hex() not in _nodes(m))
    _assert(not [e for e in events if e[0] == "node"], events)


def test_good_signature_marked_verified():
    env, m, events = _setup()
    pub, raw = fake_mpos.advert_frame(SEED, "Alex", 1000)
    m._ingest(raw, rssi=-90, snr=3)
    _assert(_nodes(m)[pub.hex()]["verified"] is True)


def test_pure_crypto_keeps_unverified_advert():
    env, m, events = _setup(native=False)
    pub, raw = fake_mpos.advert_frame(SEED, "Alex", 1000, tamper=True)
    m._ingest(raw, rssi=-90, snr=3)
    _assert(_nodes(m)[pub.hex()]["verified"] is False)


def test_replayed_advert_ignored():
    env, m, events = _setup()
    pub, raw = fake_mpos.advert_frame(SEED, "Alex", 1000)
    m._ingest(raw, rssi=-90, snr=3)
    _, raw_old = fake_mpos.advert_frame(SEED, "Alex-old", 999, path=b"\x01")
    del events[:]
    m._ingest(raw_old, rssi=-90, snr=3)
    _assert(_nodes(m)[pub.hex()]["name"] == "Alex")
    _assert(not [e for e in events if e[0] == "node"], events)


def test_repeater_and_room_kept():
    env, m, _ = _setup()
    rp, raw = fake_mpos.advert_frame(bytes([2]) * 32, "Gent-Noord", 1000, node_type=2)
    m._ingest(raw, rssi=-90, snr=3)
    rm, raw = fake_mpos.advert_frame(bytes([3]) * 32, "Gent BBS", 1000, node_type=3)
    m._ingest(raw, rssi=-90, snr=3)
    nodes = _nodes(m)
    _assert(nodes[rp.hex()]["type"] == 2 and nodes[rm.hex()]["type"] == 3, nodes)


def test_node_signal_metadata():
    env, m, _ = _setup()
    pub, raw = fake_mpos.advert_frame(SEED, "Alex", 1000, path=b"\x3A\xF1")
    m._ingest(raw, rssi=-100, snr=-3.5)
    n = _nodes(m)[pub.hex()]
    _assert(n["hops"] == 2 and n["route"] == "flood", n)
    _assert(n["snr"] == -3.5 and n["heard_ms"] == env.now_ms, n)
    pub2, raw2 = fake_mpos.advert_frame(bytes([4]) * 32, "Near", 1000, route=2)
    m._ingest(raw2, rssi=-60, snr=8)
    n2 = _nodes(m)[pub2.hex()]
    _assert(n2["hops"] == 0 and n2["route"] == "direct", n2)


def test_zero_hop_advert_route():
    from meshcore_packet import MeshCorePacket, ROUTE_TYPE_DIRECT, ROUTE_TYPE_FLOOD
    env, m, _ = _setup()
    _assert(m.advertise(flood=False) == (True, None))
    _assert(m.advertise() == (True, None))
    fake_mpos.drain(m)
    pkts = [MeshCorePacket.parse(r) for r in m.chip.sent]
    _assert(pkts[0].route_type == ROUTE_TYPE_DIRECT and pkts[0].path_hash_count() == 0)
    _assert(pkts[1].route_type == ROUTE_TYPE_FLOOD)


def test_advert_queued_while_the_radio_starts():
    env, m, _ = _setup()
    m._radio = None
    m._radio_ready = False
    m._running = True                  # started; bring-up still in progress
    _assert(m.advertise() == (True, None))
    _assert(len(m._tx_queue) == 1)
    m._running = False
    _assert(m.advertise()[0] is False)  # radio off: nothing would ever send it


def test_node_cap_evicts_oldest():
    import meshcore_manager as mm
    env, m, _ = _setup(native=False)
    first = None
    for i in range(mm.MAX_NODES + 1):
        pub, raw = fake_mpos.advert_frame(bytes([i + 10]) * 32, "n%d" % i, 1000)
        first = first or pub
        env.now_ms += 10
        m._ingest(raw, rssi=-90, snr=1)
    nodes = _nodes(m)
    _assert(len(nodes) == mm.MAX_NODES, len(nodes))
    _assert(first.hex() not in nodes)


def test_incoming_channel_msg_has_snr_hops():
    from meshcore_channel import encode_group_text, PUBLIC_CHANNEL
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len
    from meshcore_packet import ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT
    env, m, _ = _setup()
    payload = encode_group_text(PUBLIC_CHANNEL, "Sam", "anyone near?", 1700000000)
    raw = MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                         encode_path_len(3), b"\x01\x02\x03", payload).to_bytes()
    m._ingest(raw, rssi=-95, snr=6.5)
    msg = m.get_messages("Public")[-1]
    _assert(msg["snr"] == 6.5 and msg["hops"] == 3 and msg["rx_ms"] == env.now_ms, msg)


def test_flooded_advert_keeps_the_repeater_path():
    env, m, events = _setup()
    pub, raw = fake_mpos.advert_frame(SEED, "Alex", 1000, path=b"\x3a\xf1")
    m._ingest(raw, rssi=-90, snr=3)
    n = _nodes(m)[pub.hex()]
    _assert(n["hops"] == 2 and n["path"] == "3af1", n)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
