"""Radio statistics for the Radio tab: noise floor, packet rate, peak RSSI, TX airtime.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_radio_stats.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

FRAME = bytes([0x15, 0x00]) + bytes(range(30))
HOUR = 3600 * 1000


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    return env, m, m.chip


def _rx(m, chip, rssi_raw=180):
    chip.set_packet_status(rssi_raw, 0x10)
    chip.inject(FRAME)
    _assert(m._poll_radio_rx())


def test_noise_sample_every_10_s():
    env, m, chip = _setup()
    m._sample_noise()
    m._sample_noise()                       # too soon: ignored
    env.now_ms += 10000
    m._sample_noise()
    st = m.radio_stats()
    _assert(st["noise_series"] == [-106.0, -106.0], st["noise_series"])
    _assert(st["noise_dbm"] == -106.0)


def test_noise_series_caps_at_180():
    env, m, chip = _setup()
    for i in range(200):
        chip._radio.rssi_inst_raw = 200 + (i % 20)
        m._sample_noise()
        env.now_ms += 10000
    st = m.radio_stats()
    _assert(len(st["noise_series"]) == 180, len(st["noise_series"]))
    _assert(st["noise_series"][-1] == -(200 + 199 % 20) / 2, st["noise_series"][-1])


def test_no_noise_sample_while_rx_pending():
    env, m, chip = _setup()
    chip.inject(FRAME)
    m._sample_noise()
    _assert(m.radio_stats()["noise_series"] == [])
    _assert(chip._last_events, "the pending packet must be left for the poll")


def test_packets_per_hour_window():
    env, m, chip = _setup()
    _rx(m, chip)
    env.now_ms += 2 * HOUR
    for _ in range(6):
        _rx(m, chip)
        env.now_ms += 1000
    _assert(m.radio_stats()["packets_per_h"] == 6, m.radio_stats())


def test_peak_rssi_30m():
    env, m, chip = _setup()
    _rx(m, chip, rssi_raw=148)              # -74 dBm, then ages out
    env.now_ms += 31 * 60 * 1000
    _rx(m, chip, rssi_raw=190)              # -95
    _rx(m, chip, rssi_raw=180)              # -90
    _assert(m.radio_stats()["peak_rssi_30m"] == -90.0, m.radio_stats())


def test_tx_air_pct():
    env, m, chip = _setup()
    _assert(m._transmit(bytes(40)))         # 542 ms on EU/UK Narrow
    env.now_ms += 60000
    _assert(m.radio_stats()["tx_air_pct"] == 0.9, m.radio_stats())


def test_last_rx_and_rx_on():
    env, m, chip = _setup()
    _assert(m.radio_stats()["last_rx_s"] is None)
    _rx(m, chip)
    env.now_ms += 40000
    st = m.radio_stats()
    _assert(st["last_rx_s"] == 40 and st["rx_on"] is True, st)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
