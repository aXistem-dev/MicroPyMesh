"""Our own position: set by hand (typed or picked on the map) or by a GPS. GPS is off by
default; switched on without a GPS answering, it switches itself off again. The position goes
into adverts only when sharing is on.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_position.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

RMC = "$GPRMC,101500,A,5049.674,N,00315.894,E,0.0,0.0,031026,,,A"     # 50.82790, 3.26490
NO_FIX = "$GPRMC,101500,V,,,,,,,031026,,,N"


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup(env=None):
    env = env or fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    events = []
    m.add_subscriber(lambda e, d: events.append(e))
    return env, m, events


def _restart(env):
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    return fake_mpos.new_manager(env)


def test_no_position_and_gps_off_by_default():
    env, m, _ = _setup()
    _assert(m.position() is None, m.position())
    _assert(m.gps_status() == {"enabled": False, "state": "off"}, m.gps_status())
    _assert(m.share_position() is False, "share off")


def test_a_position_set_by_hand_survives_a_restart():
    env, m, events = _setup()
    _assert(m.set_position(50.8279, 3.2649) == (True, None), "set")
    _assert(m.position() == {"lat": 50.8279, "lon": 3.2649, "source": "manual"}, m.position())
    _assert("position" in events, events)
    m2 = _restart(env)
    _assert(m2.position() == {"lat": 50.8279, "lon": 3.2649, "source": "manual"}, m2.position())
    m2.clear_position()
    _assert(_restart(env).position() is None, "cleared")


def test_impossible_positions_are_refused():
    env, m, _ = _setup()
    for lat, lon in ((91, 3), (50, 181), (0, 0), (float("nan"), 3)):
        ok, err = m.set_position(lat, lon)
        _assert(not ok and err, (lat, lon, err))
    _assert(m.position() is None, m.position())


def test_gps_without_a_gps_is_refused_at_once():
    env, m, events = _setup()
    ok, err = m.set_gps_enabled(True)
    _assert(not ok and "No GPS" in err, err)
    _assert(m.gps_status() == {"enabled": False, "state": "absent"}, m.gps_status())


def test_gps_that_stays_silent_switches_itself_off():
    env, m, events = _setup()
    env.gps.present = True
    _assert(m.set_gps_enabled(True) == (True, None), "on")
    _assert(m.gps_status()["state"] == "searching", m.gps_status())
    env.now_ms += 10000
    m._gps_tick()
    _assert(m.gps_status()["enabled"], "still waiting")
    env.now_ms += 11000
    m._gps_tick()
    _assert(m.gps_status() == {"enabled": False, "state": "absent"}, m.gps_status())
    _assert(not _restart(env).gps_status()["enabled"], "stays off after a restart")


def test_a_gps_without_a_fix_stays_on():
    env, m, _ = _setup()
    env.gps.present = True
    m.set_gps_enabled(True)
    env.gps.queue = [NO_FIX]
    m._gps_tick()
    env.now_ms += 60000
    env.gps.queue = [NO_FIX]
    m._gps_tick()
    _assert(m.gps_status() == {"enabled": True, "state": "no_fix"}, m.gps_status())


def test_a_gps_fix_sets_the_position_and_is_saved():
    env, m, events = _setup()
    m.set_position(51.0, 4.0)
    env.gps.present = True
    m.set_gps_enabled(True)
    env.gps.queue = [RMC]
    m._gps_tick()
    pos = m.position()
    _assert(pos["source"] == "gps" and abs(pos["lat"] - 50.8279) < 1e-4
            and abs(pos["lon"] - 3.2649) < 1e-4, pos)
    _assert(m.gps_status()["state"] == "fix", m.gps_status())
    _assert(_restart(env).position()["source"] == "gps", "saved")


def test_gps_is_polled_about_once_a_second():
    env, m, _ = _setup()
    env.gps.present = True
    m.set_gps_enabled(True)
    env.gps.queue = [NO_FIX]
    for _ in range(10):
        env.now_ms += 100
        env.gps.queue = [NO_FIX]
        m._gps_tick()
    _assert(1 <= env.gps.polls <= 2, env.gps.polls)


def test_gps_on_at_start_looks_for_the_gps_again():
    env, m, _ = _setup()
    env.gps.present = True
    m.set_gps_enabled(True)
    m2 = _restart(env)
    _assert(m2.gps_status() == {"enabled": True, "state": "searching"}, m2.gps_status())
    env.now_ms += 21000
    m2._gps_tick()
    _assert(m2.gps_status()["state"] == "absent", m2.gps_status())


def test_adverts_carry_the_position_only_when_shared():
    from meshcore_advert import parse_advert
    env, m, _ = _setup()
    m.set_position(50.8279, 3.2649)
    m.advertise(flood=False)
    fake_mpos.drain(m)
    adv = parse_advert(fake_mpos.sent_packets(m.chip)[-1].payload)
    _assert(adv["lat"] is None, adv)
    m.set_share_position(True)
    _assert(_restart(env).share_position(), "saved")
    m.advertise(flood=False)
    fake_mpos.drain(m)
    adv = parse_advert(fake_mpos.sent_packets(m.chip)[-1].payload)
    _assert(abs(adv["lat"] - 50.8279) < 1e-5 and abs(adv["lon"] - 3.2649) < 1e-5, adv)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
