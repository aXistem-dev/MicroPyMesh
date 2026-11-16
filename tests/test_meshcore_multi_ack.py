"""Extra delivery acks (MeshCore's multi-acks): off by default; with 1 or 2, a direct ack is
preceded by that many MULTIPART copies (remaining count first), 300 ms apart, as
Mesh::routeDirectRecvAcks sends them. A MULTIPART ack that reaches us counts as the ack.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_multi_ack.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402
from test_meshcore_sounds import _dm  # noqa: E402

MULTIPART = 0x0A
ACK = 0x03


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    return env, m


def _acks(m):
    """(payload type, payload, due) of every queued ack, in sending order."""
    out = []
    for due, raw, _ in m._tx_queue:
        t = (raw[0] >> 2) & 0x0F
        if t in (MULTIPART, ACK):
            from meshcore_packet import MeshCorePacket
            out.append((t, bytes(MeshCorePacket.parse(raw).payload), due))
    return out


def _direct_dm(env, m):
    raw = _dm(env, m, "hello")
    pk = list(m._contacts)[0]
    m.get_contact(pk).update(path=b"\x3a", path_raw=1)       # we know the way back
    m._ingest(raw, rssi=-90, snr=4)
    return pk


def test_off_by_default_and_kept():
    env, m = _setup()
    _assert(m.extra_acks() == 0)
    m.set_extra_acks(2)
    m.set_extra_acks(5)                                      # not offered: ignored
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    _assert(fake_mpos.new_manager(env).extra_acks() == 2)


def test_one_ack_when_off():
    env, m = _setup()
    _direct_dm(env, m)
    _assert([t for t, _, _ in _acks(m)] == [ACK], _acks(m))


def test_extra_copies_before_the_ack():
    env, m = _setup()
    m.set_extra_acks(2)
    _direct_dm(env, m)
    acks = _acks(m)
    _assert([t for t, _, _ in acks] == [MULTIPART, MULTIPART, ACK], acks)
    ack4 = acks[2][1][:4]
    _assert(acks[0][1] == bytes([(2 << 4) | ACK]) + ack4, acks[0][1].hex())
    _assert(acks[1][1] == bytes([(1 << 4) | ACK]) + ack4, acks[1][1].hex())
    _assert(acks[1][2] - acks[0][2] >= 300 and acks[2][2] - acks[1][2] >= 300,
            [a[2] for a in acks])


def test_no_extra_copies_on_a_flooded_ack():
    env, m = _setup()
    m.set_extra_acks(1)
    m._ingest(_dm(env, m, "hello"), rssi=-90, snr=4)        # no route back yet: flooded
    _assert([t for t, _, _ in _acks(m)] == [ACK], _acks(m))


def test_a_multipart_ack_marks_our_message_delivered():
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len, ROUTE_TYPE_DIRECT
    env, m = _setup()
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    m.send_dm(peer_pub.hex(), "ping")
    fake_mpos.drain(m)
    msg = m.get_dm_messages(peer_pub.hex())[-1]
    payload = bytes([(1 << 4) | ACK]) + bytes.fromhex(msg["ack"])
    raw = MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, MULTIPART), encode_path_len(0), b"",
                         payload).to_bytes()
    m._ingest(raw, rssi=-80, snr=7)
    _assert(msg["delivered"] is True, msg)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
