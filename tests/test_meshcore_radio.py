"""Desktop CPython tests for meshcore_radio.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_radio.py

The point of these is that the upstream-driver path (MicroPythonOS#229) cannot be exercised
on a badge until MicroPythonOS actually ships it, and by then a mistranslated method name or
a wrong unit is a silently detuned radio rather than a crash.  Fakes standing in for both
drivers let the translation be checked now.
"""

import meshcore_radio
from meshcore_radio import (
    adapt, consts, is_legacy, lock_acquire, lock_holder, lock_release,
    status_name, to_lora_cfg,
)


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


class FakeLegacy:
    """Stands in for drivers.lora.sx1262.SX1262 (what 0.17.x ships)."""
    TX_DONE = 1
    RX_DONE = 2
    STATUS = {0: "ERR_NONE", -6: "ERR_RX_TIMEOUT"}

    def getIrqStatus(self):
        return 2


class FakeRadio:
    """The inner upstream lora.SX1262."""

    def __init__(self):
        self.slept = None
        self._dio1 = "dio1-pin"

    def sleep(self, warm_start=True):
        self.slept = warm_start

    def calibrate_image(self):
        pass


class FakePolled:
    """Stands in for mpos.polled_sx126x.PolledSX126x (the lora-upstream branch)."""
    TX_DONE = 1
    RX_DONE = 2
    STATUS = {0: "ERR_NONE", -7: "ERR_CRC_MISMATCH"}

    def __init__(self):
        self.calls = []
        self.rssi = -42.0
        self.snr = 7.5
        self.cfg = None
        self.callback = "unset"
        self._radio = FakeRadio()

    def get_irq_status(self):
        self.calls.append("get_irq_status"); return 2

    def clear_irq_status(self):
        self.calls.append("clear_irq_status")

    def start_recv(self):
        self.calls.append("start_recv")

    def get_status(self):
        self.calls.append("get_status"); return 0x50

    def get_packet_status(self):
        self.calls.append("get_packet_status"); return 0x112233

    def send(self, data):
        self.calls.append("send"); return (len(data), 0)

    def recv(self, len_=0):
        self.calls.append("recv"); return (b"hello", 0)

    def configure(self, cfg):
        self.calls.append("configure"); self.cfg = cfg

    def set_callback(self, cb):
        self.callback = cb

    def clear_callback(self):
        self.callback = None


# --- constants ------------------------------------------------------------------- #

def test_consts_reads_them_off_whichever_driver_we_were_handed():
    _assert(consts(FakeLegacy()).RX_DONE == 2)
    _assert(consts(FakePolled()).RX_DONE == 2)
    # Both drivers agree on the SX1262's own IRQ bits, which is what makes this safe.
    _assert(FakeLegacy.RX_DONE == FakePolled.RX_DONE)


def test_consts_falls_back_when_the_chip_carries_nothing():
    class Bare:
        pass
    _assert(consts(Bare()).RX_DONE == 2)
    _assert(consts(None).TX_DONE == 1)


def test_status_name_tolerates_an_unknown_code():
    chip = FakeLegacy()
    _assert(status_name(chip, 0) == "ERR_NONE")
    _assert(status_name(chip, -6) == "ERR_RX_TIMEOUT")
    # A code this driver's table lacks must not raise: it only ever feeds a log line.
    _assert(status_name(chip, -999) == "ERR_-999", status_name(chip, -999))
    _assert(status_name(None, -6) == "ERR_RX_TIMEOUT")


# --- driver detection ------------------------------------------------------------- #

def test_is_legacy_and_adapt_pass_through():
    legacy = FakeLegacy()
    _assert(is_legacy(legacy))
    # Untouched on the driver shipping today: no wrapper, no behaviour change.
    _assert(adapt(legacy) is legacy)
    _assert(adapt(None) is None)


def test_adapt_wraps_the_upstream_driver():
    polled = FakePolled()
    _assert(not is_legacy(polled))
    a = adapt(polled)
    _assert(a is not polled)
    _assert(a.getIrqStatus() == 2)
    _assert(polled.calls == ["get_irq_status"], polled.calls)


# --- method translation ----------------------------------------------------------- #

def test_every_translated_method_reaches_its_snake_case_name():
    polled = FakePolled()
    a = adapt(polled)
    a.getIrqStatus(); a.clearIrqStatus(); a.startReceive()
    a.getStatus(); a.getPacketStatus()
    _assert(polled.calls == ["get_irq_status", "clear_irq_status", "start_recv",
                             "get_status", "get_packet_status"], polled.calls)


def test_rssi_and_snr_decode_the_packet_status():
    # GetPacketStatus = 0x112233: RssiPkt 0x11 (-raw/2 dBm), SnrPkt 0x22 (signed raw/4 dB).
    a = adapt(FakePolled())
    _assert(a.getRSSI() == -8.5, a.getRSSI())
    _assert(a.getSNR() == 8.5, a.getSNR())


def test_send_and_recv_keep_their_tuple_shape():
    a = adapt(FakePolled())
    _assert(a.send(b"abc") == (3, 0))
    _assert(a.recv() == (b"hello", 0))


def test_setBlockingCallback_none_clears_the_user_callback():
    # This app only ever calls (False, None): worker-polled RX, no DIO1 handler.
    polled = FakePolled()
    adapt(polled).setBlockingCallback(False, None)
    _assert(polled.callback is None)
    adapt(polled).setBlockingCallback(False, "cb")
    _assert(polled.callback == "cb")


def test_setDio2AsRfSwitch_is_a_no_op_but_must_not_raise():
    # Bring-up calls it unconditionally; upstream fixes it at construction instead.
    _assert(adapt(FakePolled()).setDio2AsRfSwitch(False) is None)


def test_sleep_maps_retainConfig_to_warm_start():
    polled = FakePolled()
    adapt(polled).sleep(retainConfig=False)
    _assert(polled._radio.slept is False, polled._radio.slept)


def test_irq_exposes_the_inner_dio1_pin():
    _assert(adapt(FakePolled()).irq == "dio1-pin")


def test_unknown_attributes_fall_through_to_the_chip():
    polled = FakePolled()
    a = adapt(polled)
    _assert(a.STATUS is FakePolled.STATUS)   # so consts() finds them through the wrapper
    _assert(consts(a).RX_DONE == 2)


# --- begin() / lora_cfg translation ------------------------------------------------ #

# The EU/UK Narrow begin() keywords, written out so a drift in meshcore_presets shows up
# here -- a wrong value detunes the radio silently.
MESHCORE_RADIO = dict(
    freq=869.618, bw=62.5, sf=8, cr=8, syncWord=0x12, preambleLength=32,
    implicit=False, crcOn=True, tcxoVoltage=3.0,
    useRegulatorLDO=False, blocking=True, currentLimit=140.0, power=22,
)


def test_to_lora_cfg_converts_units_and_names():
    cfg = to_lora_cfg(MESHCORE_RADIO)
    # MHz -> kHz. Getting this wrong puts us on the wrong band entirely.
    _assert(cfg["freq_khz"] == 869618, cfg["freq_khz"])
    _assert(cfg["sf"] == 8)
    _assert(cfg["bw"] == 62.5)          # kHz in both drivers
    _assert(cfg["coding_rate"] == 8)    # 4/8, denominator in both
    _assert(cfg["syncword"] == 0x12)
    _assert(cfg["preamble_len"] == 32)
    _assert(cfg["output_power"] == 22)


def test_to_lora_cfg_drops_keys_upstream_would_silently_ignore():
    cfg = to_lora_cfg(MESHCORE_RADIO)
    # These are constructor-time on upstream and owned by the board; passing them into
    # configure() would look applied while doing nothing.
    for k in ("tcxoVoltage", "currentLimit", "useRegulatorLDO", "blocking",
              "crcOn", "implicit", "freq", "cr", "syncWord", "preambleLength", "power"):
        _assert(k not in cfg, k)


def test_to_lora_cfg_emits_only_what_was_given():
    _assert(to_lora_cfg({}) == {})
    _assert(to_lora_cfg({"sf": 7}) == {"sf": 7})


def test_begin_configures_and_reports_success_like_the_legacy_driver():
    polled = FakePolled()
    a = adapt(polled)
    _assert(a.begin(**MESHCORE_RADIO) == 0)   # legacy begin() returns 0 on success
    _assert(polled.cfg["freq_khz"] == 869618)
    _assert("configure" in polled.calls)


# --- framework lock --------------------------------------------------------------- #

def test_lock_calls_are_noops_without_the_framework():
    # Off-badge `from mpos import LoRaManager` fails, which is the same code path as an OS
    # whose LoRaManager has no acquire(): nobody is arbitrating, so we take the radio.
    _assert(lock_acquire("meshcore") is True)
    _assert(lock_holder() is None)
    lock_release("meshcore")   # must not raise


class _FakeLoRaManager:
    _holder = None
    _granted = True
    released = []

    @staticmethod
    def acquire(name):
        return _FakeLoRaManager._granted

    @staticmethod
    def release(name):
        _FakeLoRaManager.released.append(name)


def _with_fake_mpos(fn):
    """Install a fake `mpos` module so the lock helpers take their framework path."""
    import sys
    import types
    mod = types.ModuleType("mpos")
    mod.LoRaManager = _FakeLoRaManager
    had = sys.modules.get("mpos")
    sys.modules["mpos"] = mod
    try:
        fn()
    finally:
        if had is None:
            del sys.modules["mpos"]
        else:
            sys.modules["mpos"] = had


def test_lock_uses_the_framework_when_it_exposes_one():
    def body():
        _FakeLoRaManager._granted = True
        _FakeLoRaManager.released = []
        _assert(lock_acquire("meshcore") is True)
        lock_release("meshcore")
        _assert(_FakeLoRaManager.released == ["meshcore"])

        # Denied: another app holds the radio, and we must report who.
        _FakeLoRaManager._granted = False
        _FakeLoRaManager._holder = "lora_chat"
        _assert(lock_acquire("meshcore") is False)
        _assert(lock_holder() == "lora_chat")
        _FakeLoRaManager._holder = None
    _with_fake_mpos(body)


def test_a_throwing_framework_lock_does_not_brick_the_app():
    class Throwing:
        holder = None

        @staticmethod
        def acquire(name):
            raise RuntimeError("boom")

        @staticmethod
        def release(name):
            raise RuntimeError("boom")

    import sys
    import types
    mod = types.ModuleType("mpos")
    mod.LoRaManager = Throwing
    had = sys.modules.get("mpos")
    sys.modules["mpos"] = mod
    try:
        _assert(lock_acquire("meshcore") is True)   # degrade to the pre-#229 free-for-all
        lock_release("meshcore")                    # must not propagate
    finally:
        if had is None:
            del sys.modules["mpos"]
        else:
            sys.modules["mpos"] = had


def test_module_imports_nothing_hardware_specific_at_load():
    # The whole point of keeping this module import-clean: CI runs it off-badge.
    src = open(meshcore_radio.__file__).read()
    for bad in ("\nimport mpos", "\nfrom mpos", "\nimport lvgl",
                "\nfrom drivers", "\nimport machine"):
        _assert(bad not in src, bad)


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok   %s" % t.__name__)
    print("\n%d/%d tests passed" % (len(tests), len(tests)))



def test_default_preset_matches_the_written_out_keywords():
    import meshcore_presets
    kw = meshcore_presets.radio_kwargs(meshcore_presets.by_id("eu-narrow"))
    for k in ("freq", "bw", "sf", "cr", "syncWord", "preambleLength", "power"):
        _assert(kw[k] == MESHCORE_RADIO[k], (k, kw[k], MESHCORE_RADIO[k]))


if __name__ == "__main__":
    _run_all()
