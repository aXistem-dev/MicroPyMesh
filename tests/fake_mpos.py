"""Desktop stand-ins for the parts of MicroPythonOS the manager touches.

`install()` puts a fake `mpos` into sys.modules (and hides `machine`/`lvgl`), so
meshcore_manager imports on CPython; `new_manager()` then builds a manager wired to a
`FakePolledChip` without starting any thread, so tests can drive _poll_radio_rx(),
_ingest(), _transmit() and _retry_tick() by hand.
"""

import copy
import json
import sys
import types


class _Editor:
    def __init__(self, prefs):
        self._prefs = prefs
        self._data = copy.deepcopy(prefs.data)

    def put_string(self, k, v):
        self._data[k] = v
        return self

    put_int = put_bool = put_list = put_dict = put_string

    def put_dict_item(self, dict_key, item_key, value):
        self._data.setdefault(dict_key, {})[item_key] = value
        return self

    def remove_dict_item(self, dict_key, item_key):
        self._data.get(dict_key, {}).pop(item_key, None)
        return self

    def remove_all(self):
        self._data = {}
        return self

    def commit(self):
        # Round-trip through JSON, as the real store does, so tests catch unserialisable values.
        self._prefs.env.store[(self._prefs.appname, self._prefs.filename)] = json.loads(
            json.dumps(self._data))
        self._prefs.data = copy.deepcopy(self._data)
        return True

    apply = commit


class FakeEnv:
    def __init__(self):
        self.store = {}            # (appname, filename) -> dict
        self.constructions = 0     # SharedPreferences(...) calls, i.e. file reads on device
        self.lora = None
        self.notifications = []
        self.now_ms = 1000000      # fake time.ticks_ms() clock; tests advance it by hand
        self.sleeps = []           # every time.sleep_ms() the code under test asked for

    def prefs(self, app, filename="config.json"):
        return self.store.setdefault((app, filename), {})

    def make_prefs_class(self):
        env = self

        class SharedPreferences:
            def __init__(self, appname, filename="config.json", defaults=None):
                env.constructions += 1
                self.env = env
                self.appname = appname
                self.filename = filename
                raw = env.store.get((appname, filename), {})
                self.data = copy.deepcopy(raw) if isinstance(raw, dict) else {}

            def _get(self, k, default, kind):
                v = self.data.get(k, default)
                return v if isinstance(v, kind) else default

            def get_string(self, k, default=None):
                return self._get(k, default, str)

            def get_int(self, k, default=0):
                return self._get(k, default, int)

            def get_bool(self, k, default=False):
                return self._get(k, default, bool)

            def get_list(self, k, default=None):
                # MicroPythonOS returns [] for a missing key when no default is given
                got = self._get(k, default, list)
                return [] if got is None else got

            def get_dict(self, k, default=None):
                return self._get(k, default, dict)

            def edit(self):
                return _Editor(self)

        return SharedPreferences


def make_fake_lora_manager():
    """A fresh LoRaManager stand-in shaped like the real one: a class with static methods,
    class attributes, and `holder` as a *property* (which reads as a property object when
    looked up on the class, as apps do)."""

    class FakeLoRaManager:
        radioChip = None
        _holder = None
        acquired = []
        released = []
        watchdog_stopped = False
        resets = 0

        @staticmethod
        def acquire(name):
            if FakeLoRaManager._holder not in (None, name):
                return False
            FakeLoRaManager._holder = name
            FakeLoRaManager.acquired.append(name)
            return True

        @staticmethod
        def release(name):
            if FakeLoRaManager._holder == name:
                FakeLoRaManager._holder = None
            FakeLoRaManager.released.append(name)

        @staticmethod
        def stop_watchdog():
            FakeLoRaManager.watchdog_stopped = True

        @staticmethod
        def reset_chip():
            FakeLoRaManager.resets += 1
            return True

        @property
        def holder(self):
            return FakeLoRaManager._holder

    FakeLoRaManager.acquired = []
    FakeLoRaManager.released = []
    return FakeLoRaManager


class _FakeInnerRadio:
    """The inner lora.SX1262 that PolledSX126x wraps (`chip._radio` / `chip.radio`)."""

    def __init__(self, chip):
        self._chip = chip
        self.calibrations = 0
        self.slept = []
        self._dio1 = None
        self.rssi_inst_raw = 212      # GetRssiInst byte: -raw/2 dBm

    def calibrate_image(self):
        self.calibrations += 1

    def sleep(self, warm_start=True):
        self.slept.append(warm_start)

    def _cmd(self, fmt, cmd, *args, n_read=0, **kw):
        if cmd == 0x15:
            return bytes([0xA2, self.rssi_inst_raw])
        return bytes(n_read)


class FakePolledChip:
    """Behaves like mpos.polled_sx126x.PolledSX126x where it matters to the manager.

    - `_last_events` is sticky: recv() leaves it set; only clear_irq_status() clears it.
    - start_recv() goes through standby, so it aborts a frame that is being received.
    - send() blocks for the airtime itself (the real driver waits for TX_DONE).
    """
    TX_DONE = 1
    RX_DONE = 2
    CRC_ERR = 64
    RX_TIMEOUT = 512
    STATUS = {0: "ERR_NONE", -6: "ERR_RX_TIMEOUT", -7: "ERR_CRC_MISMATCH"}

    def __init__(self):
        self._radio = _FakeInnerRadio(self)
        self.radio = self._radio
        self._last_events = 0
        self._frames = []
        self._in_op = False
        self.in_flight = False
        self.aborted = 0
        self.rearm_count = 0
        self.sent = []
        self.cfg = None
        self.callback = None
        self._pkt_status = (0, 0)

    # --- test controls ---
    def inject(self, frame):
        self._frames.append(bytes(frame))
        self._last_events |= self.RX_DONE

    def begin_frame(self):
        self.in_flight = True

    def set_packet_status(self, rssi_raw, snr_raw):
        self._pkt_status = (rssi_raw & 0xFF, snr_raw & 0xFF)

    # --- driver API ---
    def get_irq_status(self):
        return self._last_events

    def clear_irq_status(self):
        self._last_events = 0

    def start_recv(self):
        self.rearm_count += 1
        if self.in_flight:
            self.aborted += 1
            self.in_flight = False

    def recv(self, len_=0):
        if not self._frames:
            return (b"", -6)
        return (self._frames.pop(0), 0)

    send_status = 0                # set to e.g. -5 (TX timeout) to make send() fail

    def send(self, data):
        if self.send_status:
            return (0, self.send_status)
        self.sent.append(bytes(data))
        return (len(data), 0)

    def get_status(self):
        return 0x52

    def try_get_status(self):
        return 0x52

    def get_packet_status(self):
        rssi_raw, snr_raw = self._pkt_status
        return (rssi_raw << 16) | (snr_raw << 8) | rssi_raw

    def configure(self, cfg):
        self.cfg = cfg

    def clear_callback(self):
        self.callback = None

    def set_callback(self, cb):
        self.callback = cb

    def disable_irq(self):
        pass


class FakeGPS:
    """mpos.GPSManager: `present` says whether the board has an NMEA source; poll() hands the
    queued sentences to the listeners."""

    def __init__(self):
        self.present = False
        self.queue = []
        self.listeners = []
        self.polls = 0

    def has_nmea_source(self):
        return self.present

    def add_nmea_listener(self, cb):
        if cb not in self.listeners:
            self.listeners.append(cb)

    def remove_nmea_listener(self, cb):
        if cb in self.listeners:
            self.listeners.remove(cb)

    def poll(self):
        if not self.present:
            return False
        self.polls += 1
        while self.queue:
            s = self.queue.pop(0)
            for cb in list(self.listeners):
                cb(s)
        return True

    @staticmethod
    def position_from_nmea(sentence):
        f = sentence.split("*")[0].split(",")
        if f[0].endswith("RMC") and len(f) > 6 and f[2] == "A":
            lat = int(float(f[3]) / 100) + (float(f[3]) % 100) / 60
            lon = int(float(f[5]) / 100) + (float(f[5]) % 100) / 60
            return (-lat if f[4] == "S" else lat, -lon if f[6] == "W" else lon)
        return None


def _install_clock(env):
    """MicroPython's ticks/sleep_ms on CPython, driven by env.now_ms (sleep_ms never blocks)."""
    import time
    period = 1 << 30            # MicroPython's ticks wrap at 2**30 ms (~12.4 days)
    half = period // 2
    time.ticks_ms = lambda: env.now_ms % period
    time.ticks_diff = lambda a, b: ((a - b + half) % period) - half
    time.ticks_add = lambda a, b: (a + b) % period

    def sleep_ms(ms):
        env.sleeps.append(ms)
        env.now_ms += ms

    time.sleep_ms = sleep_ms


def install(native=None):
    """Install the fakes; returns the FakeEnv. Re-imports the app modules fresh."""
    env = FakeEnv()
    env.lora = make_fake_lora_manager()
    env.gps = FakeGPS()
    _install_clock(env)
    mpos = types.ModuleType("mpos")
    mpos.SharedPreferences = env.make_prefs_class()
    mpos.LoRaManager = env.lora
    mpos.GPSManager = env.gps

    class DeviceInfo:
        hardware_id = "sensecap_indicator"

        @staticmethod
        def get_hardware_id():
            return "sensecap_indicator"

    class TaskManager:
        disabled = True

        @staticmethod
        def create_task(coro):
            try:
                coro.close()
            except Exception:
                pass

    class Notification:
        PRIORITY_DEFAULT = 0

        def __init__(self, **kw):
            self.__dict__.update(kw)

    class NotificationManager:
        @staticmethod
        def notify(n):
            env.notifications.append(n)

        @staticmethod
        def cancel(nid):
            pass

    mpos.DeviceInfo = DeviceInfo
    mpos.TaskManager = TaskManager
    mpos.Notification = Notification
    mpos.NotificationManager = NotificationManager
    mpos.get_foreground_app = lambda: None
    sys.modules["mpos"] = mpos
    for name in ("machine", "lvgl"):
        sys.modules.pop(name, None)
    if native is not None:
        sys.modules["meshcrypto"] = native
    else:
        sys.modules.pop("meshcrypto", None)
    for name in [n for n in sys.modules if n.startswith("meshcore")]:
        del sys.modules[name]
    return env


def new_manager(env, chip=None):
    """A manager on the fake radio, ready to poll/transmit, with no threads started."""
    import meshcore_manager as mm
    import meshcore_radio
    mm.simulation_mode = False
    m = mm.MeshCoreManager()
    chip = chip or FakePolledChip()
    env.lora.radioChip = chip
    m._radio = meshcore_radio.adapt(chip)
    m._radio_ready = True
    m.chip = chip
    return m


def run_all(namespace):
    tests = [v for k, v in sorted(namespace.items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok   %s" % t.__name__)
    print("\n%d/%d tests passed" % (len(tests), len(tests)))


def make_fake_native():
    """A stand-in for the native `meshcrypto` module (MeshCore's orlp ed25519).

    It delegates to the pure-Python implementation, but holds the caller to the C module's
    contract: sign() and derive_pub() take an already-clamped private key, because orlp uses
    prv[:32] as the scalar as-is. `calls` counts uses per function.
    """
    mod = types.ModuleType("meshcrypto")
    mod.calls = {}

    def _count(name):
        mod.calls[name] = mod.calls.get(name, 0) + 1

    def _pure():
        import meshcore_crypto
        return meshcore_crypto

    def _check_clamped(prv):
        b0, b31 = prv[0], prv[31]
        if b0 & 7 or b31 & 128 or not b31 & 64:
            raise AssertionError("meshcrypto got an unclamped private key")

    def _clamp(prv):
        b = bytearray(prv)
        b[0] &= 248
        b[31] &= 63
        b[31] |= 64
        return bytes(b)

    def create_keypair(seed):
        _count("create_keypair")
        prv = _clamp(_pure().sha512(seed))
        return _pure()._pure_public_key(prv), prv

    def derive_pub(prv):
        _count("derive_pub")
        _check_clamped(prv)
        return _pure()._pure_public_key(prv)

    def sign(msg, pub, prv):
        _count("sign")
        _check_clamped(prv)
        return _pure()._pure_sign(prv, msg)

    def verify(sig, msg, pub):
        _count("verify")
        return _pure()._pure_verify(pub, sig, msg)

    def key_exchange(pub, prv):
        _count("key_exchange")
        return _pure()._pure_shared_secret(prv, pub)

    mod.create_keypair = create_keypair
    mod.derive_pub = derive_pub
    mod.sign = sign
    mod.verify = verify
    mod.key_exchange = key_exchange
    return mod


PEER_SEED = bytes([7]) * 32


def with_peer(env, m, name="Alex", seed=PEER_SEED):
    """Give the manager an identity and one contact; returns (peer_pub, peer_prv, secret)
    where `secret` is what the peer derives for talking to us."""
    import meshcore_crypto as mc
    if not m.has_identity():
        m.generate_identity()
    our_pub, _ = m.get_identity()
    peer_pub, peer_prv = mc.generate_keypair(seed=seed)
    ok, err = m.add_contact(peer_pub.hex(), name)
    assert ok, err
    return peer_pub, peer_prv, mc.shared_secret(peer_prv, our_pub)


def sent_packets(chip):
    from meshcore_packet import MeshCorePacket
    return [MeshCorePacket.parse(raw) for raw in chip.sent]


def drain(m):
    """Transmit everything that is due now; returns how many packets went out."""
    n = 0
    while m._drain_tx():
        n += 1
    return n


def advert_frame(seed, name, ts, node_type=1, route=1, path=b"", tamper=False, lat=None, lon=None):
    """A signed ADVERT packet from the node whose key comes from `seed`."""
    import meshcore_crypto as mc
    from meshcore_advert import build_advert_appdata, advert_signed_message, assemble_advert_payload
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len, PAYLOAD_TYPE_ADVERT
    pub, prv = mc.generate_keypair(seed=seed)
    app = build_advert_appdata(node_type, name, lat, lon)
    sig = mc.sign(prv, advert_signed_message(pub, ts, app), pub)
    if tamper:
        sig = bytes([sig[0] ^ 1]) + sig[1:]
    payload = assemble_advert_payload(pub, ts, sig, app)
    return pub, MeshCorePacket(make_header(route, PAYLOAD_TYPE_ADVERT),
                               encode_path_len(len(path)), path, payload).to_bytes()
