"""Radio driver compatibility for MeshCore.

Hardware-independent and unit-testable off-badge -- deliberately free of `mpos`, `lvgl` and
`drivers` imports at module level, so the translation logic can be exercised in desktop
CPython against fake chip objects (the modules that actually talk to the radio cannot be
imported off-badge at all).

Why this exists:

We never construct the radio.  The board does, and hands it to us as
`LoRaManager.radioChip` -- so *which driver class we get is the OS's choice, not ours*.
Through MicroPythonOS 0.17.x that is `drivers.lora.sx1262.SX1262`.  The upstream-driver
work (MicroPythonOS#229) replaces it with `lora.SX1262` wrapped in
`mpos.polled_sx126x.PolledSX126x`, which carries the same constants but renames every
method to snake_case and turns RSSI/SNR into properties.

Rather than hardcode either one, we duck-type the object we are handed.  `adapt()` returns
it unchanged when it already speaks the API this app calls, and wraps it otherwise.  That
keeps one .mpk running on both, and confines the whole difference to this file.

A note on `mpos.lora_adapter.MPOSLoRa`: it appears in MicroPythonOS#229's plan and in the
example diff on that issue, but it was never built -- it exists in no release, and not on
the `lora-upstream` branch either, where the role is filled by `PolledSX126x`.  Importing
it would fail everywhere, so we do not.

What is deliberately NOT translated: `begin()`.  The upstream driver configures through
`configure(cfg)` with a different dict (`freq_khz`, `syncword`, `output_power`, ...), and
the settings ours passes that have no `cfg` key -- tcxoVoltage, currentLimit,
useRegulatorLDO -- are constructor-time arguments the *board* owns, not ours to set after
the fact.  `to_lora_cfg()` below does the part that is knowable; see `begin()` in the
adapter for what a bring-up on the upstream driver would still need from the framework.
"""

# IRQ bit positions and the error-code table are fixed by the SX1262 itself, so they are
# identical in both drivers.  These are the fallback for when neither the chip object nor
# its class carries them (a fake in tests, or a future driver that drops the constants).
_TX_DONE = 1 << 0
_RX_DONE = 1 << 1

_STATUS = {
    0: "ERR_NONE",
    -1: "ERR_UNKNOWN",
    -6: "ERR_RX_TIMEOUT",
    -7: "ERR_CRC_MISMATCH",
    -804: "ERR_INVALID_DATA",
}


class _Consts:
    """Constant carrier used when the driver class does not export its own."""
    TX_DONE = _TX_DONE
    RX_DONE = _RX_DONE
    STATUS = _STATUS


def consts(chip):
    """Return an object exposing TX_DONE / RX_DONE / STATUS for `chip`.

    Both drivers put these on the class, so the chip instance itself is normally the right
    answer and is returned as-is.  This replaces the old module-level
    `from drivers.lora.sx1262 import SX1262` done purely to reach the constants: reading
    them off the object we were handed is correct on whichever driver built it, and cannot
    import a module the running OS does not have.
    """
    if chip is not None and hasattr(chip, "RX_DONE") and hasattr(chip, "STATUS"):
        return chip
    return _Consts


def status_name(chip, err):
    """Human-readable name for a driver error code, tolerating an unknown code.

    The drivers' STATUS dicts do not agree on every entry, and a KeyError here would abort
    an RX/TX path over nothing but a log line.
    """
    table = getattr(consts(chip), "STATUS", _STATUS)
    try:
        return table[err]
    except (KeyError, TypeError):
        return "ERR_%s" % (err,)


def is_legacy(chip):
    """True if `chip` already speaks the camelCase API this app calls."""
    return hasattr(chip, "getIrqStatus")


def to_lora_cfg(kw):
    """Translate this app's begin() keywords into an upstream `lora_cfg` dict.

    Only the keys upstream's `configure()` actually reads are emitted; anything else is
    dropped rather than passed through, because upstream ignores unknown keys silently and
    a typo would then look like it had been applied.

    Unit notes, since these differ between the two drivers and getting one wrong detunes the
    radio rather than raising: our `freq` is MHz and upstream's `freq_khz` is kHz; `cr` and
    upstream's `coding_rate` are both the denominator of 4/N; `bw` is kHz in both.
    """
    cfg = {}
    if "freq" in kw:
        cfg["freq_khz"] = int(round(kw["freq"] * 1000))
    for ours, theirs in (("sf", "sf"), ("bw", "bw"), ("cr", "coding_rate"),
                         ("syncWord", "syncword"), ("preambleLength", "preamble_len"),
                         ("power", "output_power")):
        if ours in kw:
            cfg[theirs] = kw[ours]
    return cfg


class _PolledAdapter:
    """Presents a `PolledSX126x` through the camelCase API the rest of this app calls.

    Method names are mapped one-for-one; the only shape changes are RSSI/SNR (properties
    upstream, methods here) and the callback/sleep paths, which have no direct counterpart.
    `send()` and `recv()` already return the same `(payload, status)` tuples in both
    drivers, so they pass straight through.
    """

    # send() waits for TX_DONE itself, so the caller must not blind-wait the airtime again.
    blocking_send = True

    def __init__(self, chip):
        self._chip = chip

    # Constants live on PolledSX126x itself, so consts() finds them via __getattr__ below.
    def __getattr__(self, name):
        # Anything not translated here (TX_DONE/RX_DONE/STATUS, radio, suspend/resume, ...)
        # belongs to the wrapped chip.  Only called for attributes not found normally.
        return getattr(self._chip, name)

    @property
    def irq(self):
        """The DIO1 pin, for the cheap 'is anything pending' check that avoids an SPI read.

        Upstream keeps it private on the inner radio and does not always have it; returning
        None makes `.value()` raise, which _poll_radio_rx already catches and treats as
        "pin read unavailable, fall through to the SPI status check".
        """
        radio = getattr(self._chip, "_radio", None)
        return getattr(radio, "_dio1", None)

    def getIrqStatus(self):
        return self._chip.get_irq_status()

    def clearIrqStatus(self):
        return self._chip.clear_irq_status()

    def startReceive(self):
        return self._chip.start_recv()

    def getStatus(self):
        return self._chip.get_status()

    def getPacketStatus(self):
        return self._chip.get_packet_status()

    def getRSSI(self):
        """RSSI of the last received packet in dBm, from GetPacketStatus (the driver's own
        `rssi` property is never filled in on the polled path)."""
        v = self._chip.get_packet_status()
        return -((v >> 16) & 0xFF) / 2

    def getSNR(self):
        """SNR of the last received packet in dB (signed quarter-dB in GetPacketStatus)."""
        raw = (self._chip.get_packet_status() >> 8) & 0xFF
        if raw > 127:
            raw -= 256
        return raw / 4

    def getRssiInst(self):
        """Instantaneous RSSI in dBm (GetRssiInst, 0x15): the noise floor while idle in RX."""
        return -self._chip._radio._cmd("B", 0x15, n_read=2)[1] / 2

    def send(self, data):
        return self._chip.send(data)

    def recv(self, len_=0):
        return self._chip.recv(len_)

    def setBlockingCallback(self, blocking, callback=None):
        """We only ever call this as (False, None): non-blocking RX with no DIO1 handler,
        because we poll from the worker thread instead (see _poll_radio_rx).  Upstream
        spells that `clear_callback()`, which drops the user callback while leaving the
        driver's own ISR -- and its continuous-RX arming -- in place.
        """
        if callback is None:
            self._chip.clear_callback()
        else:
            self._chip.set_callback(callback)

    def setDio2AsRfSwitch(self, enable):
        """No-op: the board fixes this at construction (`dio2_rf_sw`), and LoRaManager re-arms
        it after a hardware reset, so there is nothing to do here.
        """
        return None

    def sleep(self, retainConfig=True):
        """Upstream names the same flag `warm_start` on the inner radio."""
        radio = getattr(self._chip, "_radio", None)
        if radio is None:
            return None
        return radio.sleep(warm_start=retainConfig)

    def begin(self, **kw):
        """Configure for MeshCore.  Returns 0 on success, matching the legacy driver.

        Upstream splits what `begin()` did in two: the parts expressible as a `lora_cfg`
        (handled here) and the constructor-time ones -- tcxoVoltage, currentLimit,
        useRegulatorLDO -- which the board applies when it builds the radio and which cannot
        be changed afterwards.  On an OS shipping the upstream driver the board is therefore
        responsible for those, and this call only retunes the modem.
        """
        self._chip.configure(to_lora_cfg(kw))
        # Image calibration for the band in use: configure() leaves the power-on 902-928 MHz
        # calibration in place, which costs sensitivity at 868 MHz.
        self._chip._radio.calibrate_image()
        return 0


def adapt(chip):
    """Return `chip` speaking this app's radio API, wrapping it only if it needs it."""
    if chip is None or is_legacy(chip):
        return chip
    return _PolledAdapter(chip)


def lock_acquire(name):
    """Claim the shared radio, if the running OS arbitrates it.

    Only one app can drive the single physical SX1262, and MicroPythonOS#229 adds
    `LoRaManager.acquire()/release()` so lora_chat and MeshCore stop fighting over it.
    Releases through 0.17.x have a four-line LoRaManager with no such methods, so treat
    their absence as "nobody is arbitrating, go ahead" -- the same capability-probe shape
    _reset_lora_via_ch32 already uses for `reset_chip`.

    Returns True when the radio is ours to use.
    """
    try:
        from mpos import LoRaManager
    except Exception:
        return True
    fn = getattr(LoRaManager, "acquire", None)
    if fn is None:
        return True
    try:
        return bool(fn(name))
    except Exception as e:
        # A framework that has the method but throws must not brick the app: log and take
        # the radio.  Worst case we are back to the pre-#229 free-for-all.
        print("MeshCoreRadio: LoRaManager.acquire failed, proceeding:", repr(e))
        return True


def lock_holder():
    """Name of the app currently holding the radio, or None if unknown/unarbitrated."""
    try:
        from mpos import LoRaManager
        # LoRaManager.holder is an instance property, so read the class-level field it wraps.
        h = getattr(LoRaManager, "_holder", None)
        return h if isinstance(h, str) else None
    except Exception:
        return None


def lock_release(name):
    """Give the shared radio back.  No-op on an OS that does not arbitrate it."""
    try:
        from mpos import LoRaManager
    except Exception:
        return
    fn = getattr(LoRaManager, "release", None)
    if fn is None:
        return
    try:
        fn(name)
    except Exception as e:
        print("MeshCoreRadio: LoRaManager.release failed:", repr(e))
