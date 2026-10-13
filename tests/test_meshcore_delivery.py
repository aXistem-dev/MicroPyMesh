"""Delivery state of outgoing messages: on-air flag, repeater echoes, resend.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_delivery.py
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
    events = []
    m.add_subscriber(lambda ev, data: events.append((ev, data)))
    return env, m, events


def _echo(raw, path):
    """The same packet as re-flooded by a repeater: same payload, longer path."""
    from meshcore_packet import MeshCorePacket, encode_path_len
    p = MeshCorePacket.parse(raw)
    return MeshCorePacket(p.header, encode_path_len(len(path)), path, p.payload).to_bytes()


def test_channel_message_sent_once():
    import meshcore_manager as mm
    env, m, _ = _setup()
    m._repeater_heard = True
    _assert(m.send_group_text("Public", "hello"))
    fake_mpos.drain(m)
    for _ in range(3):
        env.now_ms += mm.RETRY_AFTER_MS + 1
        m._retry_tick()
        fake_mpos.drain(m)
    _assert(len(m.chip.sent) == 1, len(m.chip.sent))


def test_own_echo_increments_heard_not_messages():
    env, m, _ = _setup()
    m.send_group_text("Public", "hello")
    fake_mpos.drain(m)
    raw = m.chip.sent[0]
    m._ingest(_echo(raw, b"\xAA"), rssi=-80, snr=4)
    m._ingest(_echo(raw, b"\xBB"), rssi=-85, snr=2)
    msgs = m.get_messages("Public")
    _assert(len(msgs) == 1, msgs)
    _assert(msgs[0]["heard"] == 2, msgs[0])


def test_echo_after_seen_table_rolled_over_is_still_ours():
    env, m, _ = _setup()
    m.send_group_text("Public", "hello")
    fake_mpos.drain(m)
    raw = m.chip.sent[0]
    for i in range(80):                    # a busy mesh pushes our hash out of the seen table
        m._remember(bytes([i]) * 8)
    m._ingest(_echo(raw, b"\xAA"), rssi=-80, snr=4)
    msgs = m.get_messages("Public")
    _assert(len(msgs) == 1, msgs)
    _assert(msgs[0]["heard"] == 1, msgs[0])


def test_tx_flag_set_when_on_air():
    env, m, events = _setup()
    m.send_group_text("Public", "hello")
    msg = m.get_messages("Public")[-1]
    _assert(msg["tx"] is False, msg)
    del events[:]
    fake_mpos.drain(m)
    _assert(msg["tx"] is True, msg)
    _assert([e for e in events if e[0] == "message"], events)


def test_unheard_after_timeout():
    import meshcore_manager as mm
    env, m, events = _setup()
    m.send_group_text("Public", "hello")
    fake_mpos.drain(m)
    msg = m.get_messages("Public")[-1]
    env.now_ms += mm.RETRY_AFTER_MS + 1
    m._retry_tick()
    _assert(msg.get("unheard") is True, msg)


def test_heard_message_is_not_marked_unheard():
    import meshcore_manager as mm
    env, m, _ = _setup()
    m.send_group_text("Public", "hello")
    fake_mpos.drain(m)
    m._ingest(_echo(m.chip.sent[0], b"\xAA"), rssi=-80, snr=4)
    env.now_ms += mm.RETRY_AFTER_MS + 1
    m._retry_tick()
    _assert(not m.get_messages("Public")[-1].get("unheard"))


def test_resend_replaces_unheard_channel_message():
    import meshcore_manager as mm
    env, m, _ = _setup()
    m.send_group_text("Public", "hello")
    fake_mpos.drain(m)
    old = m.get_messages("Public")[-1]
    env.now_ms += mm.RETRY_AFTER_MS + 1
    m._retry_tick()
    _assert(m.resend("Public", old) is True)
    msgs = m.get_messages("Public")
    _assert(len(msgs) == 1 and msgs[0] is not old, msgs)
    _assert(msgs[0]["text"] == "hello" and not msgs[0].get("unheard"))


def test_resend_replaces_failed_dm():
    import meshcore_manager as mm
    env, m, _ = _setup()
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    key = peer_pub.hex()
    ok, err = m.send_dm(key, "ping")
    _assert(ok, err)
    for _ in range(mm.DM_MAX_SENDS + 1):
        fake_mpos.drain(m)
        env.now_ms += mm.RETRY_AFTER_MS + 1
        m._retry_tick()
    old = m.get_dm_messages(key)[-1]
    _assert(old["failed"] is True, old)
    env.now_ms += 5000
    _assert(m.resend(key, old) is True)
    msgs = m.get_dm_messages(key)
    _assert(len(msgs) == 1 and msgs[0] is not old, msgs)
    _assert(msgs[0]["text"] == "ping" and msgs[0]["attempt"] == 0 and not msgs[0]["failed"])


def test_delivered_dm_records_ack_time_and_snr():
    import meshcore_manager as mm
    env, m, _ = _setup()
    peer_pub, peer_prv, secret = fake_mpos.with_peer(env, m)
    m.send_dm(peer_pub.hex(), "ping")
    fake_mpos.drain(m)
    msg = m.get_dm_messages(peer_pub.hex())[-1]
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len
    from meshcore_packet import ROUTE_TYPE_DIRECT, PAYLOAD_TYPE_ACK
    ack = bytes.fromhex(msg["ack"]) + b"\x00\x01"
    raw = MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, PAYLOAD_TYPE_ACK), encode_path_len(0),
                         b"", ack).to_bytes()
    m._ingest(raw, rssi=-80, snr=7.0)
    _assert(msg["delivered"] is True and msg["ack_snr"] == 7.0, msg)
    _assert(abs(msg["ack_ts"] - mm.unix_time()) <= 2, msg)


def test_resend_refuses_a_pending_message():
    env, m, _ = _setup()
    m.send_group_text("Public", "hello")
    _assert(m.resend("Public", m.get_messages("Public")[-1]) is False)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
