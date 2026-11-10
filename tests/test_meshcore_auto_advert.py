"""Automatic adverts: off by default; when chosen, a flood advert every few hours and/or a
zero-hop advert every few minutes. Any advert sent resets the timers it covers (a flood
advert reaches the neighbours too).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_auto_advert.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

MIN = 60 * 1000
HOUR = 60 * MIN


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    m._running = True
    return env, m


def _adverts(m):
    """Route of every queued advert: "flood" or "zero"."""
    from meshcore_packet import PAYLOAD_TYPE_ADVERT, ROUTE_TYPE_FLOOD
    out = []
    for _, raw, _ in m._tx_queue:
        if (raw[0] >> 2) & 0x0F == PAYLOAD_TYPE_ADVERT:
            out.append("flood" if raw[0] & 0x03 == ROUTE_TYPE_FLOOD else "zero")
    return out


def _tick(env, m, ms):
    env.now_ms += ms
    m._auto_advert_tick()


def test_off_by_default():
    env, m = _setup()
    _assert(m.auto_advert_settings() == {"flood_h": 0, "zero_hop_min": 0},
            m.auto_advert_settings())
    _tick(env, m, 48 * HOUR)
    _assert(_adverts(m) == [], _adverts(m))


def test_settings_persist_and_only_offered_values():
    env, m = _setup()
    m.set_auto_advert(flood_h=12, zero_hop_min=30)
    m.set_auto_advert(flood_h=5)                 # not one of the choices: ignored
    m2 = fake_mpos.new_manager(env)
    _assert(m2.auto_advert_settings() == {"flood_h": 12, "zero_hop_min": 30},
            m2.auto_advert_settings())


def test_flood_advert_on_its_interval():
    env, m = _setup()
    m.set_auto_advert(flood_h=3)
    _tick(env, m, 3 * HOUR - 1000)
    _assert(_adverts(m) == [], _adverts(m))
    _tick(env, m, 2000)
    _assert(_adverts(m) == ["flood"], _adverts(m))
    _tick(env, m, 1000)
    _assert(_adverts(m) == ["flood"], "once per interval")


def test_zero_hop_advert_on_its_interval():
    env, m = _setup()
    m.set_auto_advert(zero_hop_min=15)
    _tick(env, m, 15 * MIN + 1000)
    _assert(_adverts(m) == ["zero"], _adverts(m))


def test_a_flood_advert_also_resets_the_zero_hop_timer():
    env, m = _setup()
    m.set_auto_advert(flood_h=3, zero_hop_min=15)
    _tick(env, m, 10 * MIN)
    m.advertise(flood=True)                      # by hand
    m._tx_queue[:] = []
    _tick(env, m, 10 * MIN)
    _assert(_adverts(m) == [], "a zero-hop advert is due 15 min after the flood one")
    _tick(env, m, 6 * MIN)
    _assert(_adverts(m) == ["zero"], _adverts(m))


def test_a_zero_hop_advert_leaves_the_flood_timer():
    env, m = _setup()
    m.set_auto_advert(flood_h=3)
    _tick(env, m, 2 * HOUR)
    m.advertise(flood=False)
    m._tx_queue[:] = []
    _tick(env, m, HOUR + 1000)
    _assert(_adverts(m) == ["flood"], _adverts(m))


def test_nothing_without_an_identity():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m._running = True
    m.set_auto_advert(zero_hop_min=15)
    _tick(env, m, HOUR)
    _assert(_adverts(m) == [], _adverts(m))


def test_switching_a_kind_on_counts_from_then():
    env, m = _setup()
    m.set_auto_advert(zero_hop_min=15)
    _tick(env, m, 7 * 24 * HOUR)                 # a week of zero-hop adverts only
    m._tx_queue[:] = []
    m.set_auto_advert(flood_h=3)
    _tick(env, m, 3 * HOUR + 1000)
    _assert("flood" in _adverts(m), _adverts(m))


def test_a_failing_advert_waits_for_the_next_interval():
    env, m = _setup()
    m.set_auto_advert(zero_hop_min=15)
    calls = []
    m.advertise = lambda flood=True: calls.append(flood) or (False, "sign failed")
    _tick(env, m, 15 * MIN + 1000)
    _tick(env, m, 1000)
    _tick(env, m, 1000)
    _assert(calls == [False], calls)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
