"""Receiving and transmitting through the polled SX126x driver (no DIO1 line).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_polled_rx.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

FRAME = bytes([0x15, 0x00]) + bytes(range(30))


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    return env, m, m.chip


def test_empty_poll_does_not_rearm():
    env, m, chip = _setup()
    chip.begin_frame()                 # a preamble is coming in right now
    for _ in range(5):
        _assert(m._poll_radio_rx() is False)
    _assert(chip.rearm_count == 0, chip.rearm_count)
    _assert(chip.aborted == 0, chip.aborted)


def test_rx_done_is_cleared_after_recv():
    env, m, chip = _setup()
    chip.inject(FRAME)
    _assert(m._poll_radio_rx() is True)
    _assert(m._poll_radio_rx() is False)
    _assert(len(m._rx_queue) == 1, m._rx_queue)
    _assert(m._rx_queue[0][0] == FRAME)


def test_crc_error_is_cleared_not_rearmed():
    env, m, chip = _setup()
    chip._last_events = chip.CRC_ERR
    _assert(m._poll_radio_rx() is False)
    _assert(chip._last_events == 0, chip._last_events)
    _assert(chip.rearm_count == 0, chip.rearm_count)


def test_rssi_snr_from_packet_status():
    env, m, chip = _setup()
    chip.set_packet_status(rssi_raw=212, snr_raw=0xF2)
    chip.inject(FRAME)
    m._poll_radio_rx()
    _, rssi, snr = m._rx_queue[0]
    _assert(rssi == -106.0, rssi)
    _assert(snr == -3.5, snr)


def test_rssi_inst_reads_noise():
    env, m, chip = _setup()
    _assert(m._radio.getRssiInst() == -106.0)


def test_begin_calibrates_image():
    env, m, chip = _setup()
    import meshcore_manager as mm
    import meshcore_radio
    _assert(meshcore_radio.adapt(chip).begin(**mm.MESHCORE_RADIO) == 0)
    _assert(chip._radio.calibrations == 1, chip._radio.calibrations)


def test_bring_up_arms_rx_and_stops_framework_watchdog():
    env, m, chip = _setup()
    m._radio_ready = False
    _assert(m._bring_up_radio() is True)
    _assert(env.lora.watchdog_stopped is True)
    _assert(chip.rearm_count == 1, chip.rearm_count)
    _assert(m._radio_ready is True)


def test_blocking_send_skips_airtime_sleep():
    env, m, chip = _setup()
    del env.sleeps[:]
    _assert(m._transmit(bytes(40)) is True)
    _assert(chip.sent == [bytes(40)])
    _assert(all(ms < 100 for ms in env.sleeps), env.sleeps)


def test_tx_deferred_while_rx_pending():
    env, m, chip = _setup()
    _assert(m._rx_pending() is False)
    chip.inject(FRAME)
    _assert(m._rx_pending() is True)


def test_lock_holder_returns_name():
    env, m, chip = _setup()
    import meshcore_radio
    _assert(meshcore_radio.lock_acquire("meshcore") is True)
    _assert(meshcore_radio.lock_holder() == "meshcore", meshcore_radio.lock_holder())


def test_stop_does_not_cold_sleep():
    env, m, chip = _setup()
    m._running = True
    m.stop()
    _assert(chip._radio.slept == [], chip._radio.slept)
    _assert("meshcore" in env.lora.released)


def test_watchdog_waits_for_a_bring_up_in_progress():
    env, m, chip = _setup()
    m._radio_ready = False
    m._bringup_in_progress = True       # the start-up thread is still configuring the chip
    m._rx_watchdog()
    _assert(m._reinit_count == 0, m._reinit_count)
    _assert(env.lora.resets == 0, env.lora.resets)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
