"""Transmit power: 22 dBm unless chosen, kept across restarts, within what the SX1262 can do,
and what the radio is started with.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_tx_power.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _restart(env):
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    return fake_mpos.new_manager(env)


def test_default_is_the_preset_power():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    _assert(m.tx_power() == 22, m.tx_power())
    _assert(m.radio_begin_kwargs()["power"] == 22)


def test_a_chosen_power_is_kept_and_used():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    _assert(m.set_tx_power(14) == (True, None))
    _assert(m.radio_begin_kwargs()["power"] == 14)
    _assert(_restart(env).tx_power() == 14, "kept")


def test_out_of_range_is_refused():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    for bad in (23, 1, "x"):
        ok, err = m.set_tx_power(bad)
        _assert(not ok and err, (bad, err))
    _assert(m.tx_power() == 22)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
