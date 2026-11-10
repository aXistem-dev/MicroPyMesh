"""Timestamps on the air are Unix time, also on ports whose epoch is 2000 (ESP32).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_clock.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

DEVICE_NOW = 844275762            # what time.time() said on the device, 2026-10-02
UNIX_NOW = DEVICE_NOW + 946684800


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


class _Epoch2000:
    """Make the CPython time module look like MicroPython on ESP32 for a while."""

    def __enter__(self):
        self._time, self._gmtime = time.time, time.gmtime
        real_gmtime = time.gmtime
        time.time = lambda: float(DEVICE_NOW)
        time.gmtime = lambda t=None: real_gmtime(946684800 if t == 0 else t)
        return self

    def __exit__(self, *a):
        time.time, time.gmtime = self._time, self._gmtime


def test_unix_time_on_a_2000_epoch_port():
    fake_mpos.install()
    import meshcore_manager as mm
    with _Epoch2000():
        _assert(mm.unix_time() == UNIX_NOW, mm.unix_time())


def test_unix_time_on_a_unix_epoch_port():
    fake_mpos.install()
    import meshcore_manager as mm
    _assert(abs(mm.unix_time() - int(time.time())) <= 1)


def test_outgoing_messages_and_adverts_carry_unix_time():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    from meshcore_packet import MeshCorePacket
    from meshcore_advert import parse_advert
    with _Epoch2000():
        m.send_group_text("Public", "hi")
        m.advertise()
    _assert(m.get_messages("Public")[-1]["ts"] == UNIX_NOW)
    fake_mpos.drain(m)
    adv = MeshCorePacket.parse(m.chip.sent[-1])
    _assert(parse_advert(adv.payload)["timestamp"] == UNIX_NOW)


class _Clock:
    """time.time() at a fixed Unix second (None: a clock never set, 2000-01-01)."""

    def __init__(self, unix):
        self.unix = unix

    def __enter__(self):
        self._time = time.time
        time.time = lambda: float(self.unix if self.unix is not None else 0)
        return self

    def __exit__(self, *a):
        time.time = self._time


def _manager():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    return env, m


def test_a_stored_time_far_ahead_does_not_pin_the_clock():
    # it happened: a "last timestamp" 17 h ahead made every message show the same time
    env, m = _manager()
    m._last_ts = UNIX_NOW + 17 * 3600
    with _Clock(UNIX_NOW):
        _assert(m._timestamp() == UNIX_NOW, m._timestamp())
        _assert(m._timestamp(unique=True) == UNIX_NOW, "advert back on time")
        _assert(m._timestamp(unique=True) == UNIX_NOW + 1, "still strictly increasing")


def test_a_slightly_ahead_last_advert_is_respected():
    env, m = _manager()
    m._last_ts = UNIX_NOW + 30
    with _Clock(UNIX_NOW):
        _assert(m._timestamp(unique=True) == UNIX_NOW + 31, "peers need a newer advert")


def test_without_a_clock_mesh_time_keeps_running():
    env, m = _manager()
    with _Clock(None):
        m._note_heard_ts(UNIX_NOW)
        _assert(m._timestamp() == UNIX_NOW, m._timestamp())
        env.now_ms += 90 * 1000
        _assert(m._timestamp() == UNIX_NOW + 90, m._timestamp())


def test_with_a_clock_a_time_far_in_the_future_is_not_believed():
    env, m = _manager()
    with _Clock(UNIX_NOW):
        m._note_heard_ts(UNIX_NOW + 3 * 86400)
        _assert(m._newest_heard_ts < UNIX_NOW + 86400, m._newest_heard_ts)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
