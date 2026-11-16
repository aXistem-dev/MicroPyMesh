"""Long-running and failure cases: TX errors, the ticks wrap, an unset clock, packets
still arriving when we want to transmit, and prefs writes that fail.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_robustness.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

APP = "eu.axistem.micropymesh"
WRAP = 1 << 30


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup(now_ms=None):
    env = fake_mpos.install()
    if now_ms is not None:
        env.now_ms = now_ms
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    return env, m


def _group_frame(sender, text, ts):
    from meshcore_channel import encode_group_text, PUBLIC_CHANNEL
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len
    from meshcore_packet import ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT
    payload = encode_group_text(PUBLIC_CHANNEL, sender, text, ts)
    return MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                          encode_path_len(1), b"\x01", payload).to_bytes()


# --- a transmit that fails ------------------------------------------------- #

def test_failed_tx_dm_is_retried_then_marked_failed():
    import meshcore_manager as mm
    env, m = _setup()
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    m.chip.send_status = -5                    # every TX times out
    m.send_dm(peer_pub.hex(), "ping")
    for _ in range(mm.DM_MAX_SENDS + 1):
        fake_mpos.drain(m)
        env.now_ms += mm.RETRY_AFTER_MS + 1
        m._retry_tick()
    msg = m.get_dm_messages(peer_pub.hex())[-1]
    _assert(msg["failed"] is True, msg)


def test_failed_tx_channel_message_becomes_unheard():
    import meshcore_manager as mm
    env, m = _setup()
    m.chip.send_status = -5
    m.send_group_text("Public", "hello")
    fake_mpos.drain(m)
    env.now_ms += mm.RETRY_AFTER_MS + 1
    m._retry_tick()
    _assert(m.get_messages("Public")[-1].get("unheard") is True)


def test_resend_offered_for_a_message_that_never_went_out():
    fake_mpos.install()
    import ui_model
    _assert(ui_model.delivery({"incoming": False, "tx": False, "failed": True, "ack": "x"})["icon"] == "retry")
    _assert("tap to resend" in ui_model.delivery({"incoming": False, "tx": False, "unheard": True})["text"])


# --- the 2**30 ms ticks wrap ---------------------------------------------- #

def test_history_still_flushes_across_the_ticks_wrap():
    env, m = _setup(now_ms=WRAP - 2000)
    m._worker_running = True
    m._ingest(_group_frame("Sam", "before", 1700000000), rssi=-90, snr=4)
    m._flush_due()
    env.now_ms += 6000                         # past the wrap
    m._ingest(_group_frame("Sam", "after", 1700000010), rssi=-90, snr=4)
    m._flush_due()
    stored = env.prefs(APP, "channel_history.json")["ch"]["Public"]
    _assert(stored[-1]["text"] == "after", stored)


def test_tx_due_across_the_ticks_wrap():
    env, m = _setup(now_ms=WRAP - 200)
    m._enqueue_tx(b"\x11late", delay_ms=500)
    m._enqueue_tx(b"\x11now")
    _assert(fake_mpos.drain(m) == 1)
    env.now_ms += 600                          # wraps
    _assert(fake_mpos.drain(m) == 1)
    _assert(m.chip.sent == [b"\x11now", b"\x11late"], m.chip.sent)


def test_noise_sampling_and_stats_across_the_ticks_wrap():
    env, m = _setup(now_ms=WRAP - 5000)
    m._sample_noise()
    env.now_ms += 11000
    m._sample_noise()
    _assert(len(m.radio_stats()["noise_series"]) == 2, m.radio_stats()["noise_series"])
    m.chip.set_packet_status(180, 0x10)
    m.chip.inject(bytes([0x15, 0]) + bytes(20))
    m._poll_radio_rx()
    env.now_ms += 1000
    st = m.radio_stats()
    _assert(st["packets_per_h"] == 1 and st["last_rx_s"] == 1, st)


def test_node_age_across_the_ticks_wrap():
    fake_mpos.install()
    import ui_model
    rows = ui_model.node_rows([{"pubkey": "a1" * 32, "id": "a1", "type": 1, "name": "A",
                                "hops": 0, "heard_ms": WRAP - 60000}], 60000)
    _assert(rows[0]["age"] == "2 min", rows[0])


# --- an unset clock -------------------------------------------------------- #

class _Epoch2000Unset:
    """time.time() as on a device that never synced: early 2000."""

    def __enter__(self):
        self._time, self._gmtime = time.time, time.gmtime
        real = time.gmtime
        time.time = lambda: 3600.0
        time.gmtime = lambda t=None: real(946684800 if t == 0 else t)

    def __exit__(self, *a):
        time.time, time.gmtime = self._time, self._gmtime


def test_unset_clock_uses_the_newest_heard_time():
    env, m = _setup()
    m._ingest(_group_frame("Sam", "hi", 1790000000), rssi=-90, snr=4)
    with _Epoch2000Unset():
        m.send_group_text("Public", "hello")
    _assert(m.get_messages("Public")[-1]["ts"] >= 1790000000, m.get_messages("Public")[-1])


def test_advert_timestamps_always_increase_and_survive_restart():
    from meshcore_packet import MeshCorePacket
    from meshcore_advert import parse_advert
    env, m = _setup()
    with _Epoch2000Unset():
        m.advertise()
        m.advertise()
        fake_mpos.drain(m)
        ts = [parse_advert(MeshCorePacket.parse(r).payload)["timestamp"] for r in m.chip.sent]
        _assert(ts[1] > ts[0], ts)
        m2 = fake_mpos.new_manager(env)
        m2.advertise()
        fake_mpos.drain(m2)
        ts2 = parse_advert(MeshCorePacket.parse(m2.chip.sent[-1]).payload)["timestamp"]
    _assert(ts2 > ts[1], (ts, ts2))


# --- packets still arriving ------------------------------------------------ #

def test_tx_waits_while_a_header_is_arriving():
    env, m = _setup()
    m.chip._last_events = 0x10                 # HEADER_VALID: a packet is coming in
    _assert(m._rx_pending() is True)
    m._poll_radio_rx()
    _assert(m.chip._last_events & 0x10, "the poll must not clear a packet in progress")


def test_stale_preamble_flag_expires():
    env, m = _setup()
    m.chip._last_events = 0x04                 # PREAMBLE_DETECTED, never followed by data
    _assert(m._rx_pending() is True)
    env.now_ms += 5000
    _assert(m._rx_pending() is False)


# --- prefs writes ---------------------------------------------------------- #

def test_identity_lives_in_its_own_file():
    env, m = _setup()
    pub, prv = m.get_identity()
    ident = env.prefs(APP, "identity.json")
    _assert(ident.get("pub") == pub.hex() and ident.get("prv") == prv.hex(), ident)
    _assert(not env.prefs(APP).get("identity_prv"), env.prefs(APP))
    env.store[(APP, "config.json")] = "not json"     # a torn settings file
    m2 = fake_mpos.new_manager(env)
    _assert(m2.get_identity()[0] == pub)


def test_identity_moves_out_of_config_json():
    env = fake_mpos.install()
    import meshcore_crypto as mc
    pub, prv = mc.generate_keypair(seed=bytes([5]) * 32)
    env.prefs(APP).update({"identity_pub": pub.hex(), "identity_prv": prv.hex()})
    m = fake_mpos.new_manager(env)
    _assert(m.get_identity()[0] == pub)
    _assert(env.prefs(APP, "identity.json").get("pub") == pub.hex())
    _assert(not env.prefs(APP).get("identity_prv"), env.prefs(APP))


def test_failed_history_write_is_retried():
    env, m = _setup()
    m._ingest(_group_frame("Sam", "keep me", 1700000000), rssi=-90, snr=4)
    real = fake_mpos._Editor.commit
    fake_mpos._Editor.commit = lambda self: (_ for _ in ()).throw(OSError("flash busy"))
    try:
        m._flush_dirty()
    finally:
        fake_mpos._Editor.commit = real
    m._flush_dirty()
    stored = env.prefs(APP, "channel_history.json").get("ch", {}).get("Public", [])
    _assert(stored and stored[-1]["text"] == "keep me", stored)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
