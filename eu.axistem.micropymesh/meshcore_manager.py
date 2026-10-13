# MeshCoreManager -- background MeshCore node/radio owner (singleton).
#
# Owns the shared SX1262 and runs passively in the background, independent of any UI, so
# the device listens (and can send on public channels) even when no MeshCore screen is
# open.  Enabled by an app-local toggle (`service_enabled` pref): MeshCoreBootService starts
# it at boot when enabled, and the Settings switch turns it on/off live.  UI activities
# attach as a data source.
#
# Data model (all lvgl-free; UI subscribes via add_subscriber and marshals to LVGL):
#   - nodes     : companions, repeaters and rooms learned from ADVERT packets (signature
#                 checked when the native meshcrypto module is present)
#   - channels  : public group channels (default "Public" + user-added), Channel objects
#   - messages  : per-channel chat history (public group text, decoded)
#   - packets   : raw parsed-packet log (debug)
#
# Radio settings verified against MeshCore source. cr=5 (4/5) matches standard MeshCore
# nodes -- required for interop (both RX decode and TX being decodable by companion/repeater).

try:
    simulation_mode = False
    import machine  # noqa: F401  -- only to tell a device from a desktop run
except Exception as e:
    print("MeshCoreManager: simulation mode (no machine module): %s" % e)
    simulation_mode = True

import meshcore_region
from meshcore_packet import (MeshCorePacket, make_header, encode_path_len,
                             ROUTE_TYPE_FLOOD, ROUTE_TYPE_DIRECT, PAYLOAD_TYPE_GRP_TXT,
                             ROUTE_TYPE_TRANSPORT_FLOOD, PH_ROUTE_MASK,
                             PAYLOAD_TYPE_ADVERT, PAYLOAD_TYPE_TXT_MSG, PAYLOAD_TYPE_PATH,
                             PAYLOAD_TYPE_ACK, PAYLOAD_TYPE_REQ, PAYLOAD_TYPE_RESPONSE,
                             PAYLOAD_TYPE_ANON_REQ, PAYLOAD_TYPE_TRACE, PAYLOAD_TYPE_MULTIPART)
from meshcore_channel import (decode_group_text, encode_group_text, PUBLIC_CHANNEL, Channel,
                             psk_from_text)
from meshcore_advert import (parse_advert, build_advert_appdata, advert_signed_message,
                             assemble_advert_payload, ADV_TYPE_CHAT, ADV_TYPE_ROOM, ADV_TYPE_NAMES,
                             contact_share_uri, channel_share_uri, parse_channel_uri)
# Import siblings at module load (while the app dir is on sys.path) and reference them by
# attribute later. A lazy `from meshcore_crypto import ...` inside a function runs after the
# app dir has left sys.path and fails on MicroPython ("no module named ..."); an attribute
# access on the already-imported module cannot.
import meshcore_crypto   # noqa: F401
import meshcore_dm       # noqa: F401
import meshcore_radio    # noqa: F401
import meshcore_presets  # noqa: F401
import meshcore_server   # noqa: F401
import ui_model           # pure helpers (local time); the background service needs it too

# begin() keywords for the default preset (EU/UK Narrow); bring-up uses the stored preset.
MESHCORE_RADIO = meshcore_presets.radio_kwargs(
    meshcore_presets.by_id(meshcore_presets.DEFAULT_PRESET))

# MeshCore timestamps are Unix seconds; MicroPython on the ESP32 counts from 2000-01-01.
EPOCH_2000_OFFSET = 946684800


def unix_time():
    """Seconds since 1970-01-01 UTC, whatever epoch this port's time.time() uses."""
    import time
    t = int(time.time())
    try:
        if time.gmtime(0)[0] == 2000:
            t += EPOCH_2000_OFFSET
    except Exception:
        pass
    return t


# MicroPython's ticks_ms() wraps every 2**30 ms (~12.4 days): compare ticks only through
# these, never with plain subtraction.
_TICKS_PERIOD = 1 << 30
_TICKS_HALF = _TICKS_PERIOD // 2


def tdiff(a, b):
    """a - b in ms, for two ticks_ms() values less than ~6 days apart."""
    return ((a - b + _TICKS_HALF) % _TICKS_PERIOD) - _TICKS_HALF


def sd_root():
    """Where the OS mounts the SD card (SDCardManager), else MicroPythonOS's usual /sdcard."""
    try:
        from mpos import SDCardManager
        mp = SDCardManager.get_mount_point()
        if mp:
            return mp.rstrip("/") or "/"
    except Exception:
        pass
    return "/sdcard"


def parse_path(text, size):
    """Hex hops ("a1,b2" or "a1 b2"), `size` bytes each -> (path bytes, None) or (None, why)."""
    hops = [t for t in text.replace(",", " ").split() if t]
    if not hops:
        return None, "at least one hop"
    if len(hops) * size > 64:
        return None, "at most %d hops of %d byte%s (64 bytes)" % (64 // size, size,
                                                                  "" if size == 1 else "s")
    out = b""
    for h in hops:
        if len(h) != 2 * size:
            return None, "each hop is %d hex characters" % (2 * size)
        try:
            out += bytes.fromhex(h)
        except ValueError:
            return None, "%s is not hex" % h
    return out, None


def _metres(lat1, lon1, lat2, lon2):
    """Distance between two nearby points (equirectangular: plenty for "has it moved")."""
    import math
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.sqrt(x * x + y * y)


def tadd(a, delta):
    return (a + delta) % _TICKS_PERIOD


# SX126x IRQ bits that mean "a packet is arriving" (PreambleDetected, HeaderValid).
IRQ_BUSY = 0x04 | 0x10

# A clock reading before this has never been set (no Wi-Fi/NTP yet).
CLOCK_VALID_AFTER = 1704067200     # 2024-01-01
IDENTITY_FILE = "identity.json"    # {"pub": hex, "prv": hex}: written once, kept apart


class _DummyLock:
    """No-op lock for desktop simulation / ports without _thread."""
    def acquire(self, *a):
        return True

    def release(self):
        pass


MAX_PACKETS = 100       # raw log cap
MAX_MESSAGES = 200      # per-channel / per-contact history cap (RAM)
CH_HISTORY_CAP = 50     # channel messages kept on flash, per channel
NOISE_EVERY_MS = 10000  # noise-floor sample interval (Radio tab)
NOISE_SAMPLES = 180     # 30 min of samples
STATS_WINDOW_MS = 3600 * 1000
FLUSH_EVERY_MS = 5000   # coalesce history/unread writes while the worker runs
# Files next to config.json (each SharedPreferences() re-reads its whole file, so bulky
# history stays out of the small settings file).
DM_HISTORY_FILE = "dm_history.json"         # {"h": {pubkey_hex: [msg]}}
CH_HISTORY_FILE = "channel_history.json"    # {"ch": {channel_name: [msg]}}
UNREAD_FILE = "unread.json"                 # {"u": {key: count}, "m": [keys with a mention]}
NODES_FILE = "nodes.json"                   # {"n": {pubkey: node}} heard by advert
NODES_FLUSH_MS = 60000      # heard nodes change with every advert: write them at most this often
_NODE_STORED = ("name", "type", "lat", "lon", "timestamp", "hops", "snr", "rssi", "path",
                "route", "verified", "heard_ts", "seq")
_CH_STORED = ("ts", "sender", "text", "incoming", "snr", "hops", "tx", "heard", "unheard",
              "rssi", "path", "hsize", "region")
MAX_NODES = 100         # learned-nodes cap (RAM): companions, repeaters, rooms, sensors
# Resends. A byte-identical packet is useless: every node keeps a "seen" table of packet
# hashes, so repeaters refuse to re-flood it and the recipient drops it before it can even
# re-ack. MeshCore therefore keeps the MESSAGE identical (same text, so it is not a new
# message) and only varies the bit that feeds the packet hash: the 2-bit `attempt` counter
# in a DM's flags byte. Channel messages are sent once, as the firmware does: a resend
# could only vary the timestamp, and stock clients show that as a second message. A
# repeater echoing it back is counted as "heard"; with no echo it is marked unheard and the
# user can resend it as a new message.
RETRY_AFTER_MS = 15000      # no ack (DM) / no repeater echo (channel) within this
DM_MAX_SENDS = 4            # attempts 0..3 -- MeshCore keeps the attempt in 2 bits
CH_RESEND_WINDOW_S = 3      # older clients resend channel text with ts+1, ts+2: show once
MAX_OWN_HASHES = 32         # our recent channel packets, to recognise repeater echoes

# Radio re-init rate limit. Doubles per consecutive failure, resets once RX is healthy --
# see _attempt_reinit for why an unrecoverable radio must go quiet rather than keep resetting.
# TX timing, as in the firmware (BaseChatMesh.cpp TXT_ACK_DELAY, Mesh.cpp reciprocal path).
ACK_DELAY_MS = 200
MULTI_ACK_GAP_MS = 300      # between the extra ACK copies and the ACK (Mesh.cpp)
EXTRA_ACKS = (0, 1, 2)
ACK_JITTER_MS = 100
PATH_RETURN_DELAY_MS = 500

REINIT_BACKOFF_MS = 5000
REINIT_BACKOFF_MAX_MS = 60000

# Optional hashtag channel seeded into a fresh install alongside "Public" (None: Public only).
DEFAULT_CHANNEL = None

MESHCORE_APP = "eu.axistem.micropymesh"
NICKNAME_PREFS = MESHCORE_APP


# Buzzer sounds (RTTTL): one short beep per event, its pitch telling what came in.
TUNES = {
    "mention": "mention:d=16,o=7,b=180:c",  # highest: you were named in a channel
    "dm": "direct:d=16,o=6,b=180:a",       # high: someone wrote to you
    "channel": "channel:d=16,o=6,b=180:e",  # middle: a channel message
    "advert": "advert:d=32,o=5,b=180:c",    # low and shorter: a node announced itself
    "test": "test:d=8,o=6,b=180:c",
}
LAST_TS_SLACK_S = 300       # a last advert time further ahead of the clock is a leftover
GPS_DETECT_MS = 20000       # GPS switched on, nothing heard from it this long: no GPS, off again
GPS_POLL_MS = 1000          # bring in the GPS's sentences this often
GPS_SAVE_MS = 600000        # write a moving GPS position to flash at most this often
GPS_SAVE_M = 50             # ... or once it moved this far
TUNE_GAP_MS = 3000          # at most one beep this often (a burst of packets gets one sound)
SOUND_DEFAULTS = {"enabled": False, "all": False, "channel": True, "dm": True, "mention": True,
                  "advert": False}
SOUND_KINDS = ("channel", "mention", "dm", "advert")
DEFAULT_QUICK_REPLIES = ("copy", "on my way", "ETA 10 min", "signal report")
MAX_QUICK_REPLIES = 8
MAX_QUICK_REPLY_LEN = 40
AUTO_ADD_KINDS = ("chat", "rptr", "room", "sensor")
AUTO_ADVERT_FLOOD_H = (0, 3, 6, 12, 24)        # automatic flood advert: off or every N hours
AUTO_ADVERT_ZERO_HOP_MIN = (0, 15, 30, 60, 120)  # automatic zero-hop advert: off or every N min
AUTO_ADD_DEFAULTS = {"enabled": False, "all": False, "chat": False, "rptr": False, "room": False,
                     "sensor": False, "max_hops": None}
CONTACT_SORTS = ("heard", "name", "nearest", "message")
MAX_HOPS = 64               # a flood path holds at most 64 hops
CLOCK_SKEW_WARN_S = 300     # a server clock this far off ours breaks logins and ordering
ROOM_KEEP_ALIVE_S = 128     # rooms zero the login's suggested interval; the value it last held
RECENT_MAX = 20                 # packets kept for the Radio tab's "recently heard" list
RX_RATE_WINDOW_MS = 10 * 60 * 1000
_KIND_SHORT = {PAYLOAD_TYPE_GRP_TXT: "GRP", 0x06: "GRP", PAYLOAD_TYPE_ADVERT: "ADV",
               PAYLOAD_TYPE_TXT_MSG: "TXT", PAYLOAD_TYPE_ACK: "ACK", PAYLOAD_TYPE_PATH: "PATH",
               PAYLOAD_TYPE_REQ: "REQ", PAYLOAD_TYPE_RESPONSE: "RSP", PAYLOAD_TYPE_ANON_REQ: "ANON",
               PAYLOAD_TYPE_TRACE: "TRACE", 0x0A: "MULTI", 0x0B: "CTL", 0x0F: "RAW"}


class MeshCoreManager:

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._radio = None
        self._radio_ready = False   # True only after a full, successful bring-up
        self._running = False
        # Serialises all radio SPI (RX poll + TX) across the worker and UI threads. Both
        # talk to the SX1262 over SPI bus 2 (shared with the LCD DMA); without this, a TX
        # from a UI thread could overlap the worker's RX poll and wedge the radio.
        try:
            import _thread
            self._radio_lock = _thread.allocate_lock()
        except Exception:
            self._radio_lock = _DummyLock()
        # Serialises prefs writes: the worker (contacts, history) and the UI (name, channels)
        # both rewrite these JSON files, and SharedPreferences rewrites a whole file per commit.
        try:
            import _thread
            self._prefs_lock = _thread.allocate_lock()
        except Exception:
            self._prefs_lock = _DummyLock()
        self._subscribers = []
        self._sim_started = False
        self._rx_queue = []                      # (raw, rssi, snr) awaiting processing
        self._tx_queue = []                      # (due_ms, raw) awaiting transmit, by due time
        self._worker_running = False
        self._last_rx_check_ms = 0               # RX watchdog: last chip-mode check
        self._last_reinit_ms = 0                 # RX watchdog: last full re-init (rate limit)
        self._reinit_backoff_ms = REINIT_BACKOFF_MS   # grows while re-inits keep failing
        self._rx_bad = 0                         # consecutive not-in-RX watchdog checks
        self._count = 0
        self._seq = 0
        self._packets = []                       # raw log strings, most-recent-first
        self._nodes = {}                         # pubkey_hex -> node dict
        self._seen = []                          # recent packet hashes (dedup, FIFO)
        self._seen_set = set()
        self._own = {}                           # our channel packet hash -> (channel, msg)
        self._own_order = []
        self._channels = [PUBLIC_CHANNEL]        # Channel objects
        self._messages = {PUBLIC_CHANNEL.name: []}   # channel name -> [msg dicts]
        self._dm_messages = {}                   # contact pubkey_hex -> [msg dicts] (persisted)
        self._dm_names = {}                      # former contacts whose chat was kept: pubkey -> name
        self._contacts = {}                      # contact pubkey_hex -> contact dict (persisted)
        self._pending_acks = {}                  # ack_hex -> (pubkey_hex, msg) for sent DMs
        self._pending_order = []                 # ack_hex FIFO, to cap _pending_acks
        self._retries = []                       # unconfirmed sends awaiting an ack/echo
        self._sessions = {}                      # repeater/room pubkey -> login + results
        self._heard_log = []                     # (ticks_ms, kind, rssi, snr, hops), newest first
        self._heard_ms = []                      # receive times, last RX_RATE_WINDOW_MS
        self._repeater_heard = False             # heard a packet that travelled via a repeater
        self._recent = {}                        # (msg key) -> timestamp, to spot resends
        self._recent_order = []                  # key FIFO, to cap _recent
        self._keygen_running = False             # a first-run keygen thread is working
        self._bringup_in_progress = False        # guard: one radio bring-up thread at a time
        self._secrets_thread_running = False     # guard: one contact-secret precompute thread
        self._dirty_history = set()              # contact pubkey_hex with unsaved DM history
        self._dirty_channels = set()             # channel names with unsaved history
        self._unread_dirty = False
        self._last_flush_ms = None
        self._unread = {}                        # channel name / contact pubkey_hex -> unread count
        self._mentions = set()                   # keys whose unread messages mention us
        self._newest_heard_ts = 0                # clock fallback, see _timestamp()
        self._newest_heard_ms = 0
        self._busy_since = None                  # ticks when a packet started arriving
        # --- radio statistics (Radio tab) ---
        self._noise = []                         # noise-floor samples (dBm), oldest first
        self._last_noise_ms = None
        self._rx_log = []                        # (ticks_ms, rssi) of received packets, last hour
        self._tx_log = []                        # (ticks_ms, airtime_ms) of transmits, last hour
        self._stats_start_ms = self._now_ms()
        self._preset = None                      # cached radio_preset()
        # --- status/diagnostics (stashed by the worker so the UI never does raw radio SPI) ---
        self._last_status_byte = None            # last SX1262 GetStatus byte (worker-read)
        self._last_rx_ms = None                  # ticks_ms of the last received packet
        self._last_tx_ms = None                  # ticks_ms of the last transmit
        self._tx_count = 0                        # packets transmitted this session
        self._reinit_count = 0                    # radio re-inits (wedge recoveries)
        self._reset_count = 0                     # radio hardware resets attempted
        self._reset_fail_count = 0                # ... of which the chip did not come back
        self._last_flood_ms = self._now_ms()      # automatic adverts count from here
        self._last_zero_hop_ms = self._last_flood_ms
        self._load_identity()
        self._load_channels()
        self._seed_default_channels()
        self._load_contacts()
        self._load_nodes()
        self._load_channel_history()
        self._load_unread()
        self._load_position()
        self._load_routing()

    def is_running(self):
        return self._running

    # --- status / diagnostics ---------------------------------------------- #
    @staticmethod
    def _now_ms():
        import time
        try:
            return time.ticks_ms()
        except AttributeError:
            return int(time.time() * 1000)

    def _ms_since(self, t):
        if t is None:
            return None
        import time
        now = self._now_ms()
        try:
            return time.ticks_diff(now, t)
        except AttributeError:
            return now - t

    _MODE_NAMES = {0x20: "standby", 0x30: "standby", 0x40: "tuning",
                   0x50: "listening", 0x60: "transmitting"}

    def radio_status(self):
        """lvgl-free status snapshot for the Me-tab diagnostics. Reads only fields the worker
        stashed -- NEVER touches the radio SPI here (unlocked SPI from the UI wedges the chip)."""
        st = self._last_status_byte
        mode = None
        if st is not None:
            mode = self._MODE_NAMES.get(st & 0x70, "stuck")   # 0x00/unknown -> stuck
        return {
            "enabled": self.is_service_enabled(),
            "running": self._running,
            "ready": self._radio_ready,
            "recovering": self._bringup_in_progress,
            "mode": mode,
            "rx_count": self._count,
            "last_rx_ms": self._ms_since(self._last_rx_ms),
            "tx_count": self._tx_count,
            "last_tx_ms": self._ms_since(self._last_tx_ms),
            "tx_pending": len(self._tx_queue),
            "reinits": self._reinit_count,
            "resets": self._reset_count,
            "reset_fails": self._reset_fail_count,
            "nodes": len(self._nodes),
            "contacts": len(self._contacts),
        }

    # --- radio statistics --------------------------------------------------- #
    def _log_stat(self, log, entry):
        log.append(entry)
        while log and tdiff(entry[0], log[0][0]) > STATS_WINDOW_MS:
            log.pop(0)
        if len(log) > 1000:
            del log[0]

    def _sample_noise(self):
        """Every NOISE_EVERY_MS, read the instantaneous RSSI while nothing is being received:
        that is the noise floor. Skipped when the radio lock is busy or a packet waits."""
        if simulation_mode or not self._radio_ready or self._radio is None:
            return
        now = self._now_ms()
        if self._last_noise_ms is not None and tdiff(now, self._last_noise_ms) < NOISE_EVERY_MS:
            return
        reader = getattr(self._radio, "getRssiInst", None)
        if reader is None or not self._radio_lock.acquire(0):
            return
        try:
            if self._radio.getIrqStatus():
                return                      # something to receive first
            dbm = reader()
        except Exception as e:
            print("MeshCoreManager: noise sample failed:", repr(e))
            return
        finally:
            self._radio_lock.release()
        self._last_noise_ms = now
        self._noise.append(dbm)
        if len(self._noise) > NOISE_SAMPLES:
            del self._noise[0]

    def radio_stats(self):
        """Figures for the Radio tab; reads only what the worker recorded (no SPI)."""
        now = self._now_ms()
        rx_hour = [e for e in self._rx_log if tdiff(now, e[0]) <= STATS_WINDOW_MS]
        rx_half = [e[1] for e in rx_hour
                   if tdiff(now, e[0]) <= STATS_WINDOW_MS // 2 and e[1] is not None]
        tx_ms = sum(e[1] for e in self._tx_log if tdiff(now, e[0]) <= STATS_WINDOW_MS)
        elapsed = min(max(tdiff(now, self._stats_start_ms), 1), STATS_WINDOW_MS)
        return {
            "noise_dbm": self._noise[-1] if self._noise else None,
            "noise_series": list(self._noise),
            "peak_rssi_30m": max(rx_half) if rx_half else None,
            "packets_per_h": len(rx_hour),
            "tx_air_pct": round(tx_ms * 100.0 / elapsed, 1),
            "last_rx_s": None if self._last_rx_ms is None else tdiff(now, self._last_rx_ms) // 1000,
            "rx_on": bool(self._radio_ready),
            "recent": [{"age_s": tdiff(now, t) // 1000, "kind": k, "rssi": r, "snr": q, "hops": h}
                       for t, k, r, q, h in self._heard_log],
            "rx_per_min": round(len([t for t in self._heard_ms
                                     if tdiff(now, t) <= RX_RATE_WINDOW_MS]) * 60000.0
                                / min(max(tdiff(now, self._stats_start_ms), 60000),
                                      RX_RATE_WINDOW_MS), 1),
        }

    # --- radio preset ------------------------------------------------------- #
    def radio_preset(self):
        """The active preset dict (id, freq, bw, sf, cr; name for the built-in ones)."""
        if self._preset is not None:
            return self._preset
        stored = None
        try:
            from mpos import SharedPreferences
            stored = SharedPreferences(NICKNAME_PREFS).get_dict("radio", None)
        except Exception as e:
            print("MeshCore: radio preset read error:", repr(e))
        self._preset = meshcore_presets.resolve(stored)
        return self._preset

    def set_radio_preset(self, preset):
        """Store a preset (a built-in id, or a custom dict with freq/bw/sf/cr) and retune
        the radio when it is running."""
        if isinstance(preset, str):
            if meshcore_presets.by_id(preset) is None:
                raise ValueError("unknown preset %r" % preset)
            stored = {"id": preset}
        else:
            stored = {"id": "custom", "freq": float(preset["freq"]), "bw": preset["bw"],
                      "sf": int(preset["sf"]), "cr": int(preset["cr"])}
        try:
            from mpos import SharedPreferences
            ed = self._editor()
            ed.put_dict("radio", stored)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: radio preset write error:", repr(e))
            return
        self._preset = None
        if self._running:
            self.restart()

    # --- background-service enable toggle (app-local pref, live) ------------ #
    def is_service_enabled(self):
        """Whether the MeshCore radio service should run (persisted, default off)."""
        try:
            from mpos import SharedPreferences
            return SharedPreferences(NICKNAME_PREFS).get_bool("service_enabled", False)
        except Exception:
            return False

    def set_service_enabled(self, on):
        """Persist the toggle and start/stop the radio live (no reboot needed)."""
        on = bool(on)
        try:
            from mpos import SharedPreferences
            ed = self._editor()
            ed.put_bool("service_enabled", on)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: set_service_enabled error:", repr(e))
        if on:
            self.start()
        else:
            self.stop()
        self._notify("service", on)

    def _editor(self, filename="config.json"):
        """A prefs editor holding the prefs lock until _commit(), which always releases it.
        (MicroPython's lock.acquire() has no timeout: every _editor() needs its _commit().)"""
        from mpos import SharedPreferences
        self._prefs_lock.acquire()
        try:
            return SharedPreferences(NICKNAME_PREFS, filename=filename).edit()
        except Exception:
            self._prefs_lock.release()
            raise

    def _commit(self, ed):
        try:
            ed.commit()
        finally:
            try:
                self._prefs_lock.release()
            except Exception:
                pass

    def _load_identity(self):
        """Read keypair + nickname once; the getters serve these cached copies. The keypair
        lives in its own file, written once, so a torn write of the busy settings file can
        never cost the node its identity. An older install kept it in config.json: move it."""
        self._pub = self._prv = None
        self._nick = ""
        self._last_ts = 0
        try:
            import binascii
            from mpos import SharedPreferences
            p = SharedPreferences(NICKNAME_PREFS)
            ident = SharedPreferences(NICKNAME_PREFS, filename=IDENTITY_FILE)
            pub = ident.get_string("pub", "") or ""
            prv = ident.get_string("prv", "") or ""
            legacy = not (pub and prv)
            if legacy:
                pub = p.get_string("identity_pub", "") or ""
                prv = p.get_string("identity_prv", "") or ""
            if pub and prv:
                # MicroPython's unhexlify needs bytes (CPython also accepts str)
                self._pub = binascii.unhexlify(pub.encode())
                self._prv = binascii.unhexlify(prv.encode())
                if legacy:
                    self._save_identity(self._pub, self._prv)
            self._nick = p.get_string("nickname", "") or ""
            self._last_ts = p.get_int("last_ts", 0) or 0
        except Exception as e:
            print("MeshCore: identity load error:", repr(e))

    def _save_identity(self, pub, prv):
        import binascii
        ed = self._editor(IDENTITY_FILE)
        ed.put_string("pub", binascii.hexlify(pub).decode())
        ed.put_string("prv", binascii.hexlify(prv).decode())
        self._commit(ed)
        ed = self._editor()
        ed.put_string("identity_pub", "")
        ed.put_string("identity_prv", "")
        self._commit(ed)

    def nickname(self):
        return self._nick or self.default_nickname()

    def _note_heard_ts(self, ts):
        """Remember the newest plausible timestamp heard from the mesh (clock fallback),
        and when: mesh time runs on from there. With a set clock, a time more than a day
        ahead of it is some node's wrong clock and is not believed."""
        if not ts or ts <= CLOCK_VALID_AFTER or ts <= self._mesh_now():
            return
        now = unix_time()
        if now >= CLOCK_VALID_AFTER and ts > now + 86400:
            return
        self._newest_heard_ts = ts
        self._newest_heard_ms = self._now_ms()

    def _mesh_now(self):
        """The newest time heard on the mesh, plus the seconds since it was heard."""
        if not self._newest_heard_ts:
            return 0
        return self._newest_heard_ts + max(0, tdiff(self._now_ms(),
                                                    self._newest_heard_ms)) // 1000

    def _timestamp(self, unique=False):
        """Unix time for an outgoing packet. Without a set clock (no Wi-Fi), fall back to
        the newest time heard on the mesh (BaseChatMesh::bootstrapRTCfromContacts), and
        never go back in time. unique=True (adverts) makes it strictly increasing and
        persists it: peers drop an advert that is not newer than the last one."""
        t = unix_time()
        if t < CLOCK_VALID_AFTER:
            t = max(t, self._mesh_now())
        if self._last_ts > t + LAST_TS_SLACK_S:
            self._last_ts = 0           # a leftover from a clock that was wrong: not a floor
        if unique:
            if t <= self._last_ts:
                t = self._last_ts + 1
            self._last_ts = t
            try:
                ed = self._editor()
                ed.put_int("last_ts", t)
                self._commit(ed)
            except Exception as e:
                print("MeshCore: last_ts write error:", repr(e))
        elif t < self._last_ts:
            t = self._last_ts
        return t

    def default_nickname(self):
        """MC-<first 4 hex of the public key>, so two nodes never share a default name.

        The public key is already the node's identity, so its first bytes are as good a
        unique tag as any -- and 'MC-D5E4' matches the node id you see on the air (the
        first byte of that same key)."""
        pub, _ = self.get_identity()
        if pub is None:
            return "MC"
        return "MC-%s" % pub.hex()[:4].upper()

    def set_nickname(self, name):
        name = (name or "").strip()
        if not name:
            return False
        try:
            from mpos import SharedPreferences
            ed = self._editor()
            ed.put_string("nickname", name)
            self._commit(ed)
            self._nick = name
            return True
        except Exception as e:
            print("MeshCore: set_nickname error:", repr(e))
            return False

    # --- identity (Ed25519 keypair) ---------------------------------------- #
    def get_identity(self):
        """Return (pubkey32, prv64) for this node, or (None, None) if not generated."""
        return self._pub, self._prv

    def has_identity(self):
        return self.get_identity()[0] is not None

    def node_id(self):
        """The node's 1-byte routing id = first byte of the public key, or None."""
        pub, _ = self.get_identity()
        return pub[0] if pub else None

    def generate_identity(self):
        """Generate + persist a new MeshCore Ed25519 keypair.

        Seconds with the pure-Python fallback (milliseconds with meshcrypto); call it off
        the UI thread. Returns the 32-byte public key, or None on failure.
        """
        try:
            pub, prv = meshcore_crypto.generate_keypair()
        except Exception as e:
            print("MeshCore: keygen failed:", repr(e))
            return None
        try:
            self._save_identity(pub, prv)
            print("MeshCore: identity generated, node id 0x%02x" % pub[0])
        except Exception as e:
            print("MeshCore: save identity error:", repr(e))
        self._pub, self._prv = pub, prv
        self._name_from_identity(pub)
        self._notify("identity", pub)
        return pub

    def new_identity(self):
        """Replace our key pair. Everything derived from the old key goes: the secrets shared
        with contacts and nodes, repeater logins, messages still being retried. A name made
        from the old key ("MC-29EE") follows the new one; a chosen name stays. Returns the
        new public key, or None."""
        old_pub, _ = self.get_identity()
        auto = old_pub is not None and self._nick == "MC-%s" % old_pub.hex()[:4].upper()
        pub = self.generate_identity()
        if pub is None:
            return None
        for c in self._contacts.values():
            c["secret"] = None
        for n in self._nodes.values():
            n["secret"] = None
        self._sessions = {}
        self._retries = [r for r in self._retries if "secret" not in r]
        if auto:
            self._nick = ""
            self._name_from_identity(pub)
        return pub

    def export_identity(self, folder=None):
        """Write our name and key pair to FOLDER/meshcore-identity-<id>.json. Returns
        (True, path) or (False, why)."""
        import json
        pub, prv = self.get_identity()
        if pub is None:
            return (False, "no identity yet")
        folder = folder or sd_root()
        path = "%s/meshcore-identity-%s.json" % (folder.rstrip("/"), pub.hex()[:8])
        data = {"name": self.nickname(), "public_key": pub.hex(), "private_key": prv.hex(),
                "exported": self._timestamp()}
        try:
            with open(path, "w") as f:
                f.write(json.dumps(data))
        except OSError as e:
            return (False, "could not write %s (%s)" % (path, "no SD card" if e.args and
                                                         e.args[0] in (19, 2) else e))
        return (True, path)

    def _name_from_identity(self, pub):
        """Give an unnamed node a name derived from its brand-new key (MC-D5E4)."""
        current = self._nick
        if current and current != "MC":         # the user picked a name -- leave it alone
            return
        name = "MC-%s" % pub.hex()[:4].upper()
        if self.set_nickname(name):
            print("MeshCore: node named %s" % name)
            self._notify("nickname", name)

    def is_keygen_running(self):
        return self._keygen_running

    def ensure_identity(self):
        """First start with no keypair -> generate one (and a name from it) in the background.

        Pure-Python Ed25519 keygen takes seconds, so it must not block the caller: start()
        is called from the boot service and from the app, and both should just carry on."""
        if self._keygen_running or self.has_identity():
            return False
        self._keygen_running = True
        print("MeshCore: no identity yet -- generating one (first start)")
        try:
            import _thread
            try:
                from mpos import TaskManager
                _thread.stack_size(TaskManager.good_stack_size())
            except Exception:
                pass
            _thread.start_new_thread(self._keygen_thread, ())
        except Exception as e:
            print("MeshCore: could not start keygen thread:", repr(e))
            self._keygen_thread()
        return True

    def _keygen_thread(self):
        try:
            self.generate_identity()
        finally:
            self._keygen_running = False

    def contact_uri(self):
        """meshcore:// contact card for this node, to render as a QR. None if no identity."""
        pub, _ = self.get_identity()
        if pub is None:
            return None
        try:
            import binascii
            return contact_share_uri(self.nickname(), binascii.hexlify(pub).decode(),
                                     ADV_TYPE_CHAT)
        except Exception as e:
            print("MeshCore: contact_uri error:", repr(e))
            return None

    def advertise(self, flood=True):
        """Build and Ed25519-sign a self-advert so peers learn our identity/name.

        flood=True floods it through the whole mesh; flood=False sends it zero-hop (route
        DIRECT, empty path), so only direct neighbours hear it (Mesh::sendZeroHop).
        Requires an identity. Returns (ok, err).
        """
        import time
        pub, prv = self.get_identity()
        if pub is None:
            return (False, "no identity -- generate one first")
        try:
            ts = self._timestamp(unique=True)
            pos = self._position if self._share_position else None
            app_data = build_advert_appdata(ADV_TYPE_CHAT, self.nickname(),
                                            pos["lat"] if pos else None,
                                            pos["lon"] if pos else None)
            message = advert_signed_message(pub, ts, app_data)
            signature = meshcore_crypto.sign(prv, message, pub)
            payload = assemble_advert_payload(pub, ts, signature, app_data)
        except Exception as e:
            print("MeshCore: advert build failed:", repr(e))
            return (False, str(e))
        if simulation_mode:
            print("MeshCore: SIM advertise (id 0x%02x, %s)" % (pub[0], self.nickname()))
            return (True, None)
        if self._radio is None and not self._running:
            return (False, "radio is off")
        # While the radio is still starting, the packet waits in the TX queue.
        route = ROUTE_TYPE_FLOOD if flood else ROUTE_TYPE_DIRECT
        pkt = MeshCorePacket(make_header(route, PAYLOAD_TYPE_ADVERT),
                             encode_path_len(0), b"", payload)
        try:
            self._remember(pkt.packet_hash())  # ignore the repeater's echo of our advert
        except Exception:
            pass
        self._enqueue_tx(pkt.to_bytes())   # the worker flushes it in the next RX gap
        now = self._now_ms()
        self._last_zero_hop_ms = now     # a flood advert reaches the neighbours too
        if flood:
            self._last_flood_ms = now
        print("MeshCore: advert queued (id 0x%02x, name '%s')" % (pub[0], self.nickname()))
        return (True, None)

    def auto_advert_settings(self):
        """{flood_h, zero_hop_min}: automatic advert intervals, 0 = off."""
        out = {"flood_h": 0, "zero_hop_min": 0}
        try:
            from mpos import SharedPreferences
            saved = SharedPreferences(NICKNAME_PREFS).get_dict("auto_advert", {}) or {}
            if saved.get("flood_h") in AUTO_ADVERT_FLOOD_H:
                out["flood_h"] = saved["flood_h"]
            if saved.get("zero_hop_min") in AUTO_ADVERT_ZERO_HOP_MIN:
                out["zero_hop_min"] = saved["zero_hop_min"]
        except Exception:
            pass
        return out

    def set_auto_advert(self, flood_h=None, zero_hop_min=None):
        cur = self.auto_advert_settings()
        now = self._now_ms()
        if flood_h in AUTO_ADVERT_FLOOD_H:
            if flood_h and not cur["flood_h"]:
                self._last_flood_ms = now     # switched on: count from now (ticks wrap)
            cur["flood_h"] = flood_h
        if zero_hop_min in AUTO_ADVERT_ZERO_HOP_MIN:
            if zero_hop_min and not cur["zero_hop_min"]:
                self._last_zero_hop_ms = now
            cur["zero_hop_min"] = zero_hop_min
        try:
            ed = self._editor()
            ed.put_dict("auto_advert", cur)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: auto advert settings error:", repr(e))
        self._auto_advert_cache = cur
        return cur

    def _auto_advert_tick(self):
        """Send the automatic adverts that are due. The timers count from the last advert
        of that kind (by hand or automatic), or from the start."""
        cfg = getattr(self, "_auto_advert_cache", None)
        if cfg is None:
            cfg = self._auto_advert_cache = self.auto_advert_settings()
        if not (cfg["flood_h"] or cfg["zero_hop_min"]) or not self.has_identity():
            return
        now = self._now_ms()
        # The timers move first, so an advert that fails waits for the next interval.
        if cfg["flood_h"] and tdiff(now, self._last_flood_ms) >= cfg["flood_h"] * 3600000:
            self._last_flood_ms = self._last_zero_hop_ms = now
            self.advertise(flood=True)
        elif (cfg["zero_hop_min"]
              and tdiff(now, self._last_zero_hop_ms) >= cfg["zero_hop_min"] * 60000):
            self._last_zero_hop_ms = now
            self.advertise(flood=False)

    # --- channel management ------------------------------------------------- #
    def _load_channels(self):
        try:
            from mpos import SharedPreferences
            saved = SharedPreferences(NICKNAME_PREFS).get_list("channels", []) or []
        except Exception as e:
            print("MeshCore: load channels error:", repr(e))
            saved = []
        for entry in saved:
            try:
                name = entry.get("name")
                psk = entry.get("psk")
                if name and psk and self.get_channel(name) is None:
                    self._channels.append(Channel.from_psk_base64(name, psk))
                    self._messages.setdefault(name, [])
            except Exception as e:
                print("MeshCore: skipping bad saved channel %r: %s" % (entry, e))

    def _seed_default_channels(self):
        """First run: join DEFAULT_CHANNEL (when set) next to Public.

        Guarded by a flag rather than "is it missing?", so that deleting it makes it stay
        deleted instead of coming back on the next boot."""
        try:
            from mpos import SharedPreferences
            p = SharedPreferences(NICKNAME_PREFS)
            if p.get_bool("channels_seeded", False):
                return
        except Exception as e:
            print("MeshCore: seed check error:", repr(e))
            return
        if DEFAULT_CHANNEL and self.get_channel("#" + DEFAULT_CHANNEL) is None:
            ch = Channel.from_hashtag_name(DEFAULT_CHANNEL)
            self._channels.append(ch)
            self._messages.setdefault(ch.name, [])
            self._save_channels()
            print("MeshCore: joined #%s (default channel)" % DEFAULT_CHANNEL)
        try:
            from mpos import SharedPreferences
            ed = self._editor()
            ed.put_bool("channels_seeded", True)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: seed flag error:", repr(e))

    def channel_kind(self, name):
        ch = self.get_channel(name)
        return ch.kind if ch is not None else "hashtag"

    def _save_channels(self):
        try:
            from mpos import SharedPreferences
            custom = [{"name": c.name, "psk": c.psk_b64}
                      for c in self._channels
                      if c.name != PUBLIC_CHANNEL.name and c.psk_b64]
            ed = self._editor()
            ed.put_list("channels", custom)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: save channels error:", repr(e))

    def add_channel(self, name, psk=""):
        """Join a channel: a #name (key from the name), a name with a key (32 hex or base64),
        or a meshcore://channel/add link in `name`."""
        name = (name or "").strip()
        link = parse_channel_uri(name)
        if link is not None:
            name, psk = link
        psk_b64 = psk_from_text(psk)
        if not name:
            return (False, "empty name")
        try:
            if psk_b64:
                # explicit shared key -> private channel
                ch = Channel.from_psk_base64(name, psk_b64)
            else:
                # bare name -> public hashtag channel with a name-derived key, so it
                # interoperates with other nodes' "#<name>" channel (MeshCore convention)
                ch = Channel.from_hashtag_name(name)
        except Exception as e:
            return (False, "invalid channel (%s)" % e)
        if self.get_channel(ch.name) is not None:
            return (False, "channel '%s' already exists" % ch.name)
        self._channels.append(ch)
        self._messages.setdefault(ch.name, [])
        self._save_channels()
        self._notify("channels", None)
        return (True, None)

    def new_channel_key(self):
        """A random 128-bit key for a new private channel, as 32 hex characters."""
        import binascii
        try:
            import os
            raw = os.urandom(16)
        except Exception:
            raw = bytes(self._rand_byte() for _ in range(16))
        return binascii.hexlify(raw).decode()

    def channel_key_hex(self, name):
        """The channel's key in hex: 32 characters for a 128-bit key, else 64. None if unknown."""
        ch = self.get_channel(name)
        if ch is None:
            return None
        key = ch.secret[:16] if ch.secret[16:] == bytes(16) else ch.secret
        import binascii
        return binascii.hexlify(key).decode()

    def channel_uri(self, name):
        """meshcore://channel/add link to share the channel (128-bit keys only), else None."""
        key = self.channel_key_hex(name)
        if key is None or len(key) != 32:
            return None
        return channel_share_uri(name, key)

    def remove_channel(self, name):
        if name == PUBLIC_CHANNEL.name:
            return False  # the default public channel is not removable
        if self.get_channel(name) is None:
            return False
        self._channels = [c for c in self._channels if c.name != name]
        self._messages.pop(name, None)
        self._unread.pop(name, None)
        self._mentions.discard(name)
        self._unread_dirty = True
        self._dirty_channels.add(name)       # flushing a channel with no list deletes it
        self._save_channels()
        self._notify("channels", None)
        return True

    # --- lifecycle ---------------------------------------------------------- #
    def start(self):
        if self._running:
            print("MeshCoreManager: already running")
            return
        self._running = True
        self.ensure_identity()     # first start: mint a keypair + a name, off-thread
        if simulation_mode:
            self._ensure_worker()
            self._start_simulation()
            return
        self._ensure_worker()
        self._spawn_radio_init()

    def _spawn_radio_init(self):
        """Start a radio bring-up thread, but only ONE at a time -- double-tapping the service
        toggle / Restart radio must not stack two threads hitting the shared SX1262 concurrently."""
        if self._bringup_in_progress:
            print("MeshCoreManager: radio bring-up already in progress, ignoring")
            return
        self._bringup_in_progress = True
        try:
            import _thread
            from mpos import TaskManager
            _thread.stack_size(TaskManager.good_stack_size())
            _thread.start_new_thread(self._radio_init_thread, ())
        except Exception as e:
            self._bringup_in_progress = False
            print("MeshCoreManager: could not start radio init:", repr(e))

    def _reset_radio(self):
        """Hardware-reset the SX1262 through the framework (the board supplies the reset
        line via LoRaManager.board_reset). Returns True when the chip answered afterwards."""
        ok = False
        try:
            from mpos import LoRaManager
            ok = bool(LoRaManager.reset_chip())
        except Exception as e:
            print("MeshCoreManager: LoRaManager.reset_chip failed:", repr(e))
        self._reset_count += 1
        if not ok:
            self._reset_fail_count += 1
        print("MeshCoreManager: radio reset %s (t=%d, resets=%d, failed=%d)"
              % ("ok" if ok else "NOT VERIFIED", self._now_ms(),
                 self._reset_count, self._reset_fail_count))
        return ok

    def _bring_up_radio(self):
        """Reset + configure the radio for continuous RX. Returns True on success."""
        import time
        from mpos import LoRaManager
        # Not ready until setBlockingCallback below succeeds. Assigning self._radio during
        # begin() is not enough: if begin() throws on a wedged radio, the chip never gets
        # setBlockingCallback and send() then fails with "'SX1262' has no attribute
        # 'blocking'". TX/RX guard on this flag so they never touch a half-configured radio.
        self._radio_ready = False
        state = None
        # Claim the shared SX1262 before touching it. On an OS that arbitrates the radio
        # (MicroPythonOS#229) this is what stops us and lora_chat from driving the same chip
        # at once. Bail out *before* the reset -- resetting a radio another app is
        # mid-transaction on is exactly the collision the lock exists to prevent.
        if not meshcore_radio.lock_acquire("meshcore"):
            print("MeshCoreManager: LoRa held by %s, not starting radio"
                  % (meshcore_radio.lock_holder(),))
            return False
        # The framework starts its own radio watchdog on acquire. It polls the chip from the
        # asyncio loop, outside our _radio_lock; ours (_rx_watchdog) runs on the worker under
        # that lock, so only ours may touch the chip.
        stop_wd = getattr(LoRaManager, "stop_watchdog", None)
        if stop_wd is not None:
            stop_wd()
        # One reset per bring-up, not one per attempt: a failed begin() gets a second
        # begin(), and only a bring-up that still fails resets again, with _attempt_reinit's
        # backoff keeping those far apart.
        self._reset_radio()
        if LoRaManager.radioChip is None:
            print("MeshCoreManager: this device has no LoRa radio")
            meshcore_radio.lock_release("meshcore")
            return False
        for attempt in (1, 2):
            # adapt(): the board decides which driver class radioChip is, and the upstream
            # one (MicroPythonOS#229) renames every method. Returns it untouched on the
            # driver 0.17.x ships, so this costs nothing today.
            self._radio = meshcore_radio.adapt(LoRaManager.radioChip)
            state = self._radio.begin(**meshcore_presets.radio_kwargs(self.radio_preset()))
            print("MeshCoreManager: begin state=%s (attempt %d)" % (state, attempt))
            if state == 0:
                break
            time.sleep_ms(200)  # bad begin -> settle and retry once
        # Configure RX regardless of the reported begin state (best effort).
        try:
            # No interrupt callback: the worker thread polls the IRQ status instead
            # (_poll_radio_rx), so reception does not depend on which app is in front.
            # Then arm continuous receive explicitly; dropping the callback does not.
            self._radio.setBlockingCallback(False, None)
            self._radio.clearIrqStatus()
            self._radio.startReceive()
            self._radio_ready = True
            print("MeshCoreManager: passive receive started, worker-polled (begin state=%s)" % state)
            return True
        except Exception as e:
            print("MeshCoreManager: RX setup failed:", repr(e))
            return False

    def _radio_init_thread(self):
        import time
        time.sleep(1)
        ok = False
        try:
            ok = self._bring_up_radio()
            if not ok:
                self._running = False
        except Exception as e:
            print("MeshCoreManager: radio init failed:", repr(e))
            self._running = False
        finally:
            self._bringup_in_progress = False
            # A bring-up that failed clears _running directly, so stop() early-returns and
            # would never hand the radio back -- give it up here instead, or a badge that
            # cannot start its radio would hold the claim against every other LoRa app.
            # Harmless when we never got the claim: release() ignores a non-holder.
            if not ok:
                meshcore_radio.lock_release("meshcore")
        # warm per-contact shared secrets off the UI/RX path (avoids the first-DM freeze/stall)
        self._spawn_secret_precompute()

    def restart(self):
        """Recover a wedged radio: stop, reset, and re-init (from the UI)."""
        print("MeshCoreManager: restart requested")
        self._running = False
        self._radio_ready = False    # block TX/RX until bring-up re-completes
        # An explicit "Restart radio" is the user telling us to try again now, so drop any
        # backoff the watchdog had built up rather than making them wait it out.
        self._reinit_backoff_ms = REINIT_BACKOFF_MS
        if simulation_mode:
            self._running = True
            return
        self._running = True
        self._ensure_worker()
        self._spawn_radio_init()

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._radio_ready = False
        if not simulation_mode and self._radio is not None:
            # Hand the chip back under our radio lock, so the worker is never mid-SPI;
            # LoRaManager.release() puts it in standby.
            self._radio_lock.acquire()
            try:
                meshcore_radio.lock_release("meshcore")
            finally:
                self._radio_lock.release()
        self._flush_dirty(force=True)   # persist anything coalesced before going idle
        print("MeshCoreManager: stopped")

    # --- per-contact shared-secret precompute (keep ECDH off UI/RX threads) - #
    def _spawn_secret_precompute(self):
        """Warm each contact's X25519 shared secret on a background thread so the ~0.6s ECDH
        never runs on the UI thread (send_dm) or the RX worker (decode). MicroPython round-robins
        threads, so this doesn't block RX polling. Only one such thread runs at a time."""
        if simulation_mode or self._secrets_thread_running or not self.has_identity():
            return
        if not any(c.get("secret") is None for c in self._contacts.values()):
            return
        self._secrets_thread_running = True
        try:
            import _thread
            from mpos import TaskManager
            _thread.stack_size(TaskManager.good_stack_size())
            _thread.start_new_thread(self._secret_precompute_thread, ())
        except Exception as e:
            self._secrets_thread_running = False
            print("MeshCoreManager: could not start secret precompute:", repr(e))

    def _secret_precompute_thread(self):
        try:
            for contact in list(self._contacts.values()):
                if not self._running:
                    break   # service turned off -> stop precomputing
                if contact.get("secret") is None:
                    self._node_secret(contact)   # computes + caches on the contact dict
        except Exception as e:
            print("MeshCoreManager: secret precompute error:", repr(e))
        finally:
            self._secrets_thread_running = False

    # --- receive path ------------------------------------------------------- #
    def _poll_radio_rx(self):
        """Poll for a received packet from the WORKER thread and enqueue it.

        RX is serviced here rather than from a DIO1 pin interrupt: a soft IRQ runs in the
        main VM thread and is starved whenever another app holds the CPU, so backgrounded RX
        would drop packets. The dedicated worker thread runs regardless of which app is in
        front. Where the board wires DIO1, a cheap GPIO read gates the SPI status read;
        otherwise the IRQ status is read over SPI on every poll. The lock serialises this
        against TX.

        Returns True if a packet was received and queued.
        """
        if not self._radio_ready or self._radio is None:
            return False
        try:
            if not self._radio.irq.value():   # DIO1 low -> nothing pending (no SPI needed)
                return False
        except Exception:
            pass  # no DIO1 line -> the SPI IRQ-status read below is the check
        rc = meshcore_radio.consts(self._radio)
        got = False
        self._radio_lock.acquire()
        try:
            events = self._radio.getIrqStatus()
            if not events:
                # Nothing pending. Never re-arm here: going through standby would cut off a
                # packet whose preamble is arriving right now.
                return False
            if events & rc.RX_DONE:
                rssi = self._radio.getRSSI()      # packet status first: recv() moves on
                snr = self._radio.getSNR()
                msg, err = self._radio.recv()     # reads the FIFO and keeps RX armed
                self._radio.clearIrqStatus()      # or the next poll re-reads the same RX_DONE
                if err == 0 and msg and len(msg) > 0:
                    self._rx_queue.append((bytes(msg), rssi, snr))
                    self._last_rx_ms = self._now_ms()
                    self._log_stat(self._rx_log, (self._last_rx_ms, rssi))
                    got = True
                else:
                    print("MeshCoreManager: recv err=%s"
                          % meshcore_radio.status_name(self._radio, err))
            elif events & IRQ_BUSY and not events & ~IRQ_BUSY & 0xFFFF:
                pass        # a packet is still arriving: leave its flags for RX_DONE
            else:
                # CRC error / timeout / header error: drop the flags; continuous RX goes on.
                self._radio.clearIrqStatus()
        except Exception as e:
            # recv may have thrown before re-arming -> force RX back on so we don't stall.
            print("MeshCoreManager: rx poll exception:", repr(e))
            try:
                self._radio.clearIrqStatus()
                self._radio.startReceive()
            except Exception as e2:
                print("MeshCoreManager: re-arm failed:", repr(e2))
        finally:
            self._radio_lock.release()
        return got

    def _rx_pending(self):
        """True if a received packet is waiting in the radio -- don't TX over it (a send
        starts by draining and discarding the FIFO)."""
        if simulation_mode or self._radio is None:
            return False
        try:
            return bool(self._radio.irq.value())
        except Exception:
            pass   # no DIO1 line -> ask the chip
        if not self._radio_lock.acquire(0):
            return True    # the worker is mid-SPI: not a gap
        try:
            flags = self._radio.getIrqStatus()
            if flags & meshcore_radio.consts(self._radio).RX_DONE:
                return True
            return self._receiving(flags)
        except Exception:
            return False
        finally:
            self._radio_lock.release()

    def _receiving(self, flags):
        """True while a packet is arriving (preamble or header seen, no RX_DONE yet), so
        we do not transmit over it (firmware isReceiving()). A flag left by noise that never
        became a packet is cleared after the longest possible packet's airtime."""
        if not flags & IRQ_BUSY:
            self._busy_since = None
            return False
        now = self._now_ms()
        if self._busy_since is None:
            self._busy_since = now
            return True
        if tdiff(now, self._busy_since) < self._busy_timeout_ms():
            return True
        self._busy_since = None
        try:
            self._radio.clearIrqStatus()
        except Exception:
            pass
        return False

    def _busy_timeout_ms(self):
        return meshcore_presets.airtime_ms(self.radio_preset(), 255) + 200

    def _enqueue_tx(self, raw, delay_ms=0, jitter_ms=0, on_sent=None, channel=None):
        """Queue an outgoing packet, due `delay_ms` (+ up to `jitter_ms`) from now; the worker
        transmits it in the first RX gap after that and then calls `on_sent(ok)`. All TX
        flows through here so the worker thread is the SOLE owner of the radio (no
        cross-thread SPI)."""
        now = self._now_ms()
        delay = delay_ms
        if jitter_ms > 0:
            delay += self._rand_byte() * jitter_ms // 256
        due = tadd(now, delay)
        raw = self._finish_flood(raw, channel)
        i = len(self._tx_queue)
        while i > 0 and tdiff(self._tx_queue[i - 1][0], due) > 0:   # sorted, FIFO among equals
            i -= 1
        self._tx_queue.insert(i, (due, bytes(raw), on_sent))

    def _drain_tx(self):
        """Transmit the earliest queued packet if it is due; True if one went out (or was
        attempted)."""
        if not self._tx_queue or tdiff(self._tx_queue[0][0], self._now_ms()) > 0:
            return False
        _, raw, on_sent = self._tx_queue.pop(0)
        ok = False
        try:
            ok = self._transmit(raw)
        except Exception as e:
            print("MeshCoreManager: tx drain error:", repr(e))
        if on_sent is not None:
            try:
                on_sent(ok)
            except Exception as e:
                print("MeshCoreManager: on_sent error:", repr(e))
        return True

    def _rx_watchdog(self):
        """Periodically (~2s) make sure the radio is still in continuous RX, and recover it
        if not.

        In continuous mode the SX1262 shouldn't leave RX on its own, but a TX that overran or
        an SPI glitch on the LCD-shared bus can drop it to STANDBY (DIO1 never rises -> silently
        deaf) or wedge it entirely (GetStatus returns 0x00 = unresponsive on SPI). A standby
        gets a light re-arm; a hard wedge (0x00) or a persistent problem gets a full re-init."""
        if simulation_mode or self._radio is None:
            return
        import time
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_rx_check_ms) < 2000:
            return
        self._last_rx_check_ms = now
        if self._bringup_in_progress:
            return      # the start-up thread is configuring the chip; let it finish
        # Bring-up never completed / a previous re-init failed -> recovery is the only option.
        if not self._radio_ready:
            self._attempt_reinit(now)
            return
        reinit = False
        if not self._radio_lock.acquire(0):
            return  # bus busy (mid RX/TX) -> check again next tick
        try:
            st = self._radio.getStatus()
            self._last_status_byte = st          # stash for the UI diagnostics (no SPI there)
            # chip mode = status bits [6:4]; 0x50 = RX (healthy).
            if (st & 0x70) == 0x50:
                if self._rx_bad:
                    print("MeshCoreManager: RX recovered")
                self._rx_bad = 0
                self._reinit_backoff_ms = REINIT_BACKOFF_MS   # healthy again -> forget the backoff
            else:
                self._rx_bad += 1
                if self._rx_bad == 1:   # log the transition once, not every 2s
                    print("MeshCoreManager: RX watchdog -- not in RX (status 0x%02x)" % st)
                # 0x00 = chip not answering SPI (hard wedge): a startReceive can't fix it.
                if st == 0x00 or self._rx_bad >= 3:
                    reinit = True
                else:
                    self._radio.clearIrqStatus()
                    self._radio.startReceive()
        except Exception as e:
            print("MeshCoreManager: RX watchdog error:", repr(e))
            reinit = True
        finally:
            self._radio_lock.release()
        if reinit:
            self._attempt_reinit(now)

    def _attempt_reinit(self, now):
        """Recover a wedged radio via a full re-init (reset + begin + setBlockingCallback).

        Rate-limited so a radio that's momentarily un-recoverable doesn't thrash the reset
        line and SPI or flood the serial -- but it keeps retrying, so it self-heals.

        The delay doubles on each consecutive failure (5s -> 60s cap) and resets as soon as
        the watchdog sees RX again, which turns an unrecoverable radio (a dead module) into
        a quiet one instead of one that is reset every 5s forever."""
        import time
        if time.ticks_diff(now, self._last_reinit_ms) < self._reinit_backoff_ms:
            return
        self._last_reinit_ms = now
        self._rx_bad = 0
        self._reinit_count += 1
        print("MeshCoreManager: radio re-init (recovering wedged radio, backoff=%dms)"
              % self._reinit_backoff_ms)
        try:
            self._bring_up_radio()   # sets _radio_ready True on success
        except Exception as e:
            print("MeshCoreManager: re-init failed:", repr(e))
        # Grow unconditionally. _bring_up_radio reports success as soon as setBlockingCallback
        # returns -- deliberately, since it configures RX best-effort even on a bad begin() --
        # so its return value does not mean the radio is actually hearing anything. Only the
        # watchdog seeing chip mode RX proves that, and that is where the backoff is cleared.
        self._reinit_backoff_ms = min(self._reinit_backoff_ms * 2, REINIT_BACKOFF_MAX_MS)

    def _ensure_worker(self):
        if self._worker_running:
            return
        self._worker_running = True
        try:
            import _thread
            try:
                from mpos import TaskManager
                _thread.stack_size(TaskManager.good_stack_size())
            except Exception:
                pass  # stack sizing is device-only; fine to skip off-badge
            _thread.start_new_thread(self._process_loop, ())
        except Exception as e:
            self._worker_running = False
            print("MeshCoreManager: could not start worker:", repr(e))

    def _process_loop(self):
        import time
        while self._running:
            # 1) Service the radio first (fast: DIO1 gate + recv into the queue).
            did_rx = False
            if not simulation_mode:
                try:
                    did_rx = self._poll_radio_rx()
                except Exception as e:
                    print("MeshCoreManager: rx poll error:", repr(e))
            # 2) Process at most one queued packet (AES decode / UI notify -- CPU work that
            #    must NOT sit between recv and the next poll for long).
            did_proc = False
            if self._rx_queue:
                try:
                    raw, rssi, snr = self._rx_queue.pop(0)
                    self._ingest(raw, rssi=rssi, snr=snr)
                    did_proc = True
                except Exception as e:
                    print("MeshCoreManager: process exception:", repr(e))
            # 3) Transmit one queued outgoing packet -- but only in a gap: not right after an
            #    RX and only when no packet is mid-reception (DIO1 low). The worker is the SOLE
            #    owner of the radio, so all TX happens here (never from a UI thread).
            did_tx = False
            if (self._tx_queue and (simulation_mode or self._radio_ready)
                    and not did_rx and not self._rx_pending()):
                did_tx = self._drain_tx()
            # 4) Idle: run the RX watchdog (re-arm/re-init if the chip fell out of RX), flush
            #    any coalesced DM-history writes, send due automatic adverts, then sleep briefly.
            if not did_rx and not did_proc and not did_tx:
                self._rx_watchdog()
                self._sample_noise()
                if self._retries:
                    try:
                        self._retry_tick()
                    except Exception as e:
                        print("MeshCoreManager: retry error:", repr(e))
                if self._sessions:
                    try:
                        self._server_tick()
                    except Exception as e:
                        print("MeshCoreManager: server tick error:", repr(e))
                self._flush_due()
                self._gps_tick()
                try:
                    self._auto_advert_tick()
                except Exception as e:
                    print("MeshCoreManager: auto advert error:", repr(e))
                time.sleep(0.02)   # portable (MicroPython + CPython)
        self._worker_running = False
        self._flush_dirty()   # persist anything pending as the worker exits

    @staticmethod
    def _meta(rssi, snr):
        parts = []
        if rssi is not None:
            parts.append("RSSI=%s" % rssi)
        if snr is not None:
            parts.append("SNR=%s" % snr)
        return " ".join(parts)

    def _ingest(self, msg, rssi=None, snr=None):
        self._count += 1
        try:
            pkt = MeshCorePacket.parse(msg)
        except ValueError as e:
            print("MeshCore unparseable #%d: %s  hex=%s" % (self._count, e, msg.hex()))
            self._log("#%d UNPARSEABLE (%s) %dB %s" % (self._count, e, len(msg), msg.hex()))
            return

        pkt.rssi = rssi
        pkt.snr = snr
        meta = self._meta(rssi, snr)

        # De-duplicate flooded copies (direct + via repeater carry the same payload).
        try:
            h = pkt.packet_hash()
        except Exception:
            h = None
        if h is not None and h in self._own:
            self._note_echo(h)      # one of OUR packets, re-flooded by a repeater
            return
        if h is not None and not self._remember(h):
            return
        now = self._now_ms()
        self._heard_log.insert(0, (now, _KIND_SHORT.get(pkt.payload_type, "?"), rssi, snr,
                                self._hops(pkt)))
        del self._heard_log[RECENT_MAX:]
        self._heard_ms.append(now)
        while self._heard_ms and tdiff(now, self._heard_ms[0]) > RX_RATE_WINDOW_MS:
            self._heard_ms.pop(0)
        if pkt.path_hash_count():   # this copy travelled through at least one repeater
            self._repeater_heard = True

        if pkt.payload_type == PAYLOAD_TYPE_ADVERT:
            self._handle_advert(pkt, rssi, snr, meta)
        elif pkt.payload_type == PAYLOAD_TYPE_GRP_TXT and self._handle_group_text(pkt, rssi, meta):
            pass
        elif pkt.payload_type == PAYLOAD_TYPE_TXT_MSG and self._handle_dm(pkt, rssi, meta):
            pass
        elif pkt.payload_type == PAYLOAD_TYPE_PATH and self._handle_path(pkt):
            pass
        elif pkt.payload_type == PAYLOAD_TYPE_ACK and self._handle_ack(pkt):
            pass
        elif pkt.payload_type == PAYLOAD_TYPE_MULTIPART and self._handle_multipart(pkt):
            pass
        elif pkt.payload_type == PAYLOAD_TYPE_RESPONSE and self._handle_response(pkt):
            pass
        elif pkt.payload_type == PAYLOAD_TYPE_TRACE and self._handle_trace(pkt):
            pass
        else:
            summary = pkt.summary()
            print("MeshCore packet #%d: %s  hex=%s" % (self._count, summary, msg.hex()))
        # always keep a raw log line
        self._log("#%d %s" % (self._count, pkt.summary()))

    def _dup_message(self, key, ts, window=0):
        """True if this message was already shown -- i.e. it is a RESEND of one.

        The packet-hash de-dup above cannot catch a resend: a resend is deliberately a
        different packet (that is the only way the mesh will re-flood it), so without this
        the same message would appear twice in the chat. A DM's attempts all carry the same
        timestamp (window=0). A channel's attempts step it by one (ts, ts+1, ts+2), so a
        small window catches those while still letting someone genuinely repeat themselves
        a few seconds later."""
        prev = self._recent.get(key)
        if prev is not None:
            if abs(ts - prev) <= window:
                return True
            self._recent[key] = ts       # same text, but a new message
            return False
        self._recent[key] = ts
        self._recent_order.append(key)
        while len(self._recent_order) > 64:
            self._recent.pop(self._recent_order.pop(0), None)
        return False

    def _remember(self, h):
        """Record a packet hash; returns False if it was already seen."""
        if h in self._seen_set:
            return False
        self._seen.append(h)
        self._seen_set.add(h)
        if len(self._seen) > 64:
            self._seen_set.discard(self._seen.pop(0))
        return True

    @staticmethod
    def _hops(pkt):
        """Repeaters a packet travelled through (a flood's path grows per hop; a direct
        packet's path is used up on the way, so it reads as 0)."""
        return pkt.path_hash_count() if pkt.is_route_flood() else 0

    def _handle_advert(self, pkt, rssi, snr, meta):
        try:
            adv = parse_advert(pkt.payload)
        except ValueError as e:
            print("MeshCore: advert parse error:", e)
            return
        # ignore our own advert (echoed back by a repeater)
        pub, _ = self.get_identity()
        if pub is not None and adv["pubkey"] == pub.hex():
            return
        node = self._nodes.get(adv["pubkey"], {})
        if node and adv["timestamp"] <= node.get("timestamp", 0):
            return      # not newer than what we have: a replay (BaseChatMesh::onAdvertRecv)
        # Signature: enforced when verification is cheap (native crypto); with the
        # pure-Python fallback (seconds per check) the node is kept but marked unverified.
        if meshcore_crypto.NATIVE:
            payload = bytes(pkt.payload)
            signed = advert_signed_message(payload[0:32], adv["timestamp"], payload[100:])
            if not meshcore_crypto.verify(payload[0:32], payload[36:100], signed):
                print("MeshCore: advert with a bad signature from %s dropped" % adv["id"])
                return
            adv["verified"] = True
            self._note_heard_ts(adv["timestamp"])
        self._seq += 1
        new_node = not node
        node.update(adv)
        hops = self._hops(pkt)
        node["rssi"] = rssi
        node["snr"] = snr
        node["seq"] = self._seq
        node["hops"] = hops
        node["route"] = "flood" if hops else "direct"
        node["path"] = bytes(pkt.path).hex() if hops else ""
        node["heard_ms"] = self._now_ms()
        self._nodes[adv["pubkey"]] = node
        # cap learned nodes (RAM): evict the least-recently-heard one
        if new_node and len(self._nodes) > MAX_NODES:
            self._evict_oldest_node()
        node["heard_ts"] = self._timestamp()
        self._nodes_dirty = True        # written with the next coalesced flush
        # if this node is already a contact, refresh its live signal, position and last-heard
        c = self._contacts.get(adv["pubkey"])
        if c is not None:
            c["rssi"] = rssi
            c["seq"] = self._seq
            c["heard_ts"] = node["heard_ts"]
            if adv.get("lat") is not None and adv.get("lon") is not None:
                c["lat"], c["lon"] = adv["lat"], adv["lon"]
            self._contacts_dirty = True
            # a contact's name tracks its advertised name (same pubkey) -> auto-rename on change
            new_name = adv.get("name")
            if new_name and new_name != c.get("name"):
                print("MeshCore: contact %s renamed '%s' -> '%s'" % (
                    adv["pubkey"][:8], c.get("name"), new_name))
                c["name"] = new_name
                self._save_contacts()   # persist the new name
        print("MeshCore %s: %s id=%s %s%s" % (
            node.get("type_name"), node.get("name") or "?", node.get("id"), meta,
            "" if node.get("verified") else " [UNVERIFIED]"))
        self._notify("node", node)
        self._chime("advert")
        self._auto_add(node)

    def _evict_oldest_node(self):
        """Drop the least-recently-heard learned companion to bound RAM. Contacts keep their
        own persisted record in self._contacts, so evicting them from the learned list is safe."""
        try:
            oldest = min(self._nodes, key=lambda k: self._nodes[k].get("seq", 0))
            del self._nodes[oldest]
        except Exception:
            pass

    def _handle_group_text(self, pkt, rssi, meta):
        try:
            decoded = decode_group_text(pkt.payload, tuple(self._channels))
        except Exception as e:
            print("MeshCore: group decode error:", repr(e))
            decoded = None
        if not decoded:
            return False
        msg = {
            "ts": decoded["timestamp"],
            "sender": decoded["sender"] or "?",
            "text": decoded["text"],
            "rssi": rssi,
            "snr": pkt.snr,
            "hops": self._hops(pkt),
            "rx_ms": self._now_ms(),
            "incoming": True,
        }
        msg.update(self._route_info(pkt))
        self._note_heard_ts(msg["ts"])
        if self._dup_message(("ch", decoded["channel"], msg["sender"], msg["text"]),
                             msg["ts"], window=CH_RESEND_WINDOW_S):
            print("MeshCore [%s] %s: (resend, already shown)" % (decoded["channel"], msg["sender"]))
            return True
        if self.is_blocked_name(msg["sender"]):
            print("MeshCore [%s] %s: (blocked)" % (decoded["channel"], msg["sender"]))
            return True
        print("MeshCore [%s] %s: %s  (%s)" % (decoded["channel"], msg["sender"], msg["text"], meta))
        self._add_message(decoded["channel"], msg)
        self._bump_unread(decoded["channel"], mention=self._mentions_us(msg["text"]))
        self._notify("message", (decoded["channel"], msg))
        self._chime("mention" if self._mentions_us(msg["text"]) else "channel", decoded["channel"])
        self._post_notification(decoded["channel"], msg)
        return True

    # --- unread counters (channel name / contact pubkey_hex) ---------------- #
    def _bump_unread(self, key, mention=False):
        self._unread[key] = self._unread.get(key, 0) + 1
        if mention:
            self._mentions.add(key)
        self._unread_dirty = True

    def get_unread(self, key):
        return self._unread.get(key, 0)

    def get_mention(self, key):
        """True while an unread message in this chat mentions us (@[nickname])."""
        return key in self._mentions

    def clear_unread(self, key):
        """Called by a chat screen when it shows the messages (opened / new msg while open)."""
        had = self._unread.pop(key, 0)
        if key in self._mentions:
            self._mentions.discard(key)
            had = True
        if had:
            self._unread_dirty = True
            if not self._worker_running:
                self._flush_dirty()
            self._notify("unread", key)

    def mark_all_read(self):
        """Every chat read: no unread counts or mentions left."""
        if not self._unread and not self._mentions:
            return
        self._unread.clear()
        self._mentions.clear()
        self._unread_dirty = True
        if not self._worker_running:
            self._flush_dirty()
        self._notify("unread", None)

    # --- direct messages (1:1, X25519) ------------------------------------- #
    def _node_secret(self, node):
        """Return (and cache on the node) the 32-byte X25519 shared secret with a node.

        Needs our identity's private key and the node's public key (learned from its
        advert).  ECDH is ~0.6s on the badge, so the result is cached per node."""
        sec = node.get("secret")
        if sec is not None:
            return sec
        pub_hex = node.get("pubkey")
        _, prv = self.get_identity()
        if not prv or not pub_hex:
            return None
        try:
            import binascii
            pub = binascii.unhexlify(pub_hex.encode())
            sec = meshcore_crypto.shared_secret(prv, pub)
            node["secret"] = sec
            return sec
        except Exception as e:
            print("MeshCore: shared_secret error:", repr(e))
            return None

    def _handle_dm(self, pkt, rssi, meta):
        """Decode an incoming TXT_MSG addressed to us -- only from an added contact.

        You must add a companion to your contact list before you can exchange DMs with
        them, so we only try to decrypt against saved contacts (never random nodes)."""
        self_hash = self.node_id()
        if self_hash is None or len(pkt.payload) < 2:
            return False
        if pkt.payload[0] != (self_hash & 0xFF):
            return False  # not addressed to us -- cheap reject before any ECDH
        src_hash = pkt.payload[1]
        candidates = self._contact_candidates(src_hash)
        if not candidates:
            return False  # sender isn't a contact -> ignore (add them first to chat)
        try:
            got = meshcore_dm.decode_dm(pkt.payload, self_hash, candidates)
        except Exception as e:
            print("MeshCore: dm decode error:", repr(e))
            return False
        if not got:
            return False
        pub_hex = got["pubkey"].hex()
        contact = self._contacts.get(pub_hex, {})
        if got["txt_type"] == meshcore_dm.TXT_TYPE_SIGNED_PLAIN:
            return self._handle_room_post(got, pkt, contact)
        name = contact.get("name") or ("%02x" % got["src_hash"])
        msg = {"ts": got["timestamp"], "sender": name, "text": got["text"],
               "rssi": rssi, "snr": pkt.snr, "hops": self._hops(pkt),
               "rx_ms": self._now_ms(), "incoming": True}
        msg.update(self._route_info(pkt))
        # A resend must still be ACKED -- they are resending precisely because our ack was
        # lost -- but it must not show up in the chat a second time.
        dup = self._dup_message(("dm", pub_hex, got["text"]), msg["ts"])
        if dup:
            print("MeshCore DM <%s>: (resend, already shown -- re-acking)" % name)
        elif self.is_blocked_key(pub_hex):
            print("MeshCore DM <%s>: (blocked -- acking only)" % name)
        else:
            print("MeshCore DM <%s>: %s  (%s)" % (name, msg["text"], meta))
            self._add_dm(pub_hex, msg)
            self._chime("dm", pub_hex)
            self._bump_unread(pub_hex)
            self._notify("dm", (pub_hex, msg))
            self._post_dm_notification(pub_hex, name, msg)
        try:
            self._send_ack(got, pkt)
        except Exception as e:
            print("MeshCore: send ack error:", repr(e))
        return True

    def _contact_candidates(self, src_hash):
        """(pubkey_bytes, shared_secret) for each contact whose hash matches src_hash."""
        out = []
        try:
            import binascii
            for pub_hex, contact in self._contacts.items():
                if int(pub_hex[0:2], 16) != src_hash:
                    continue
                sec = self._node_secret(contact)
                if sec is not None:
                    out.append((binascii.unhexlify(pub_hex.encode()), sec))
        except Exception as e:
            print("MeshCore: candidate error:", repr(e))
        return out

    @staticmethod
    def _unhex(s):
        if not s:
            return None
        try:
            import binascii
            return binascii.unhexlify(s.encode())
        except Exception:
            return None

    @staticmethod
    def _rand_byte():
        try:
            import os
            return os.urandom(1)[0]
        except Exception:
            import time
            return int(time.time()) & 0xFF

    def _send_ack(self, got, pkt):
        """Acknowledge a received DM, ACK_DELAY_MS later (BaseChatMesh::onPeerDataRecv).

        A DM that reached us by FLOOD gets a PATH-return carrying the ack: that is what
        teaches the sender the route to us, so their next message can come direct. One that
        already arrived DIRECT needs no route lesson, so it gets a plain ACK -- sent back
        along the route we learned from them, or flooded if we have not learned one. (A
        PATH-return for a DIRECT packet would teach a zero-hop route: its path is used up by
        the time it reaches us.)"""
        if pkt.is_route_flood():
            self._send_path_ack(got, pkt)
        else:
            self._send_bare_ack(got)

    def _send_bare_ack(self, got):
        contact = self._contacts.get(got["pubkey"].hex())
        route, path_raw, path = self._route(contact)
        ack6 = bytes(got["ack_hash"]) + bytes([0, self._rand_byte()])
        delay = ACK_DELAY_MS
        if route == ROUTE_TYPE_DIRECT:
            # Extra copies first, as MULTIPART packets counting down (Mesh::routeDirectRecvAcks)
            extra = getattr(self, "_extra_acks_cache", None)
            if extra is None:
                extra = self._extra_acks_cache = self.extra_acks()
            for remaining in range(extra, 0, -1):
                multi = MeshCorePacket(make_header(route, PAYLOAD_TYPE_MULTIPART), path_raw, path,
                                       bytes([(remaining << 4) | PAYLOAD_TYPE_ACK]) +
                                       bytes(got["ack_hash"])[:4])
                self._enqueue_tx(multi.to_bytes(), delay)
                delay += MULTI_ACK_GAP_MS
        out = MeshCorePacket(make_header(route, PAYLOAD_TYPE_ACK), path_raw, path, ack6)
        try:
            self._remember(out.packet_hash())
        except Exception:
            pass
        self._enqueue_tx(out.to_bytes(), delay, ACK_JITTER_MS)

    def _send_path_ack(self, got, pkt):
        """Reply to a received DM with a flood PATH-return embedding its ack hash."""
        pub, _ = self.get_identity()
        if pub is None:
            return
        contact = self._contacts.get(got["pubkey"].hex())
        if contact is None:
            return
        secret = self._node_secret(contact)
        if secret is None:
            return
        ack6 = bytes(got["ack_hash"]) + bytes([0, self._rand_byte()])
        payload = meshcore_dm.build_path_ack(secret, got["src_hash"], pub[0],
                                             pkt.path, pkt.path_len_raw, ack6)
        out = MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_PATH),
                             encode_path_len(0), b"", payload)
        try:
            self._remember(out.packet_hash())   # de-dupe the repeater's echo of our ack
        except Exception:
            pass
        self._enqueue_tx(out.to_bytes(), ACK_DELAY_MS, ACK_JITTER_MS)

    def _handle_path(self, pkt):
        """A PATH-return addressed to us may carry the ACK for a DM we sent -> mark delivered."""
        self_hash = self.node_id()
        if self_hash is None or len(pkt.payload) < 2:
            return False
        if pkt.payload[0] != (self_hash & 0xFF):
            return False
        candidates = self._contact_candidates(pkt.payload[1])
        if not candidates:
            return False
        try:
            dec = meshcore_dm.decode_path(pkt.payload, self_hash, candidates)
        except Exception as e:
            print("MeshCore: path decode error:", repr(e))
            return False
        if not dec:
            return False
        self._learn_path(dec)
        if dec.get("ack_hash"):
            self._mark_delivered(dec["ack_hash"], pkt.snr)
        if dec.get("extra_type") == meshcore_server.PATH_EXTRA_RESPONSE:
            self._on_server_reply(dec["pubkey"].hex(), dec["extra"])
        if pkt.is_route_flood():
            self._send_path_return(dec, pkt)
        return True

    def _send_path_return(self, dec, pkt):
        """Answer a flooded PATH with our own route back, sent DIRECT along the path it just
        taught us (Mesh.cpp, onPeerPathRecv): the sender then knows the route in both
        directions."""
        contact = self._contacts.get(dec["pubkey"].hex())
        pub, _ = self.get_identity()
        if contact is None or pub is None:
            return
        secret = self._node_secret(contact)
        if secret is None:
            return
        payload = meshcore_dm.build_path_return(secret, dec["src_hash"], pub[0],
                                                pkt.path, pkt.path_len_raw)
        out = MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, PAYLOAD_TYPE_PATH),
                             dec["path_len_raw"], dec["path"], payload)
        try:
            self._remember(out.packet_hash())
        except Exception:
            pass
        self._enqueue_tx(out.to_bytes(), PATH_RETURN_DELAY_MS)

    def _learn_path(self, dec):
        """A contact told us the route back to them -- send DIRECT from now on.

        This is the path our flood took to reach them, recorded hop by hop, and it is used
        verbatim: path[0] is the next hop, and each repeater that matches it pops itself off
        and forwards (Mesh.cpp). Direct routing costs a fraction of a flood's airtime, which
        on a shared channel is the difference between a resend getting through and not."""
        pub_hex = dec["pubkey"].hex()
        contact = self._contacts.get(pub_hex)
        if contact is None:
            return
        path = dec.get("path") or b""
        if contact.get("path") == path or contact.get("route_mode") == "manual":
            return
        contact["path"] = path
        contact["path_raw"] = dec.get("path_len_raw", len(path))
        hops = dec.get("path_len_raw", 0) & 63
        print("MeshCore: learned route to %s (%d hop%s) -- sending direct from now on"
              % (contact.get("name") or pub_hex[:2], hops, "" if hops == 1 else "s"))
        self._save_contacts()

    def reset_route(self, pubkey_hex):
        """Back to auto with no path: the next message floods and the contact teaches us a
        fresh route. Returns False when there was nothing to reset."""
        contact = self._contacts.get(pubkey_hex)
        if contact is None or (not contact.get("path") and contact.get("route_mode", "auto") == "auto"):
            return False
        contact["route_mode"] = "auto"
        contact["path"] = None
        contact["path_raw"] = 0
        self._save_contacts()
        self._notify("contacts", None)
        return True

    def route_mode(self, pubkey_hex):
        """"auto" (the learned path, else flood), "flood" or "manual"."""
        return (self._contacts.get(pubkey_hex) or {}).get("route_mode", "auto")

    def route_text(self, pubkey_hex):
        """The path as hex hops ("A1,B2"), or "" without one."""
        c = self._contacts.get(pubkey_hex) or {}
        path = c.get("path") or b""
        size = ((c.get("path_raw") or 0) >> 6) + 1
        return ",".join(path[i:i + size].hex().upper() for i in range(0, len(path), size))

    def set_route(self, pubkey_hex, mode, path_text=None):
        contact = self._contacts.get(pubkey_hex)
        if contact is None:
            return (False, "not a contact")
        if mode == "manual":
            path, err = parse_path(path_text or "", self._hash_size)
            if err:
                return (False, err)
            contact["path"] = path
            contact["path_raw"] = encode_path_len(len(path) // self._hash_size, self._hash_size)
        elif mode not in ("auto", "flood"):
            return (False, "auto, flood or manual")
        elif contact.get("route_mode") == "manual":
            contact["path"] = None              # a typed path is not a learned one
            contact["path_raw"] = 0
        contact["route_mode"] = mode
        self._save_contacts()
        self._notify("contacts", None)
        return (True, None)

    def _reset_path(self, pubkey_hex):
        """The direct route stopped working -- forget it and flood again (resetPathTo). A
        path set by hand stays: it was chosen on purpose."""
        contact = self._contacts.get(pubkey_hex)
        if contact is None or not contact.get("path") or contact.get("route_mode") == "manual":
            return
        contact["path"] = None
        contact["path_raw"] = 0
        print("MeshCore: direct route to %s failed -- back to flooding"
              % (contact.get("name") or pubkey_hex[:2]))
        self._save_contacts()

    @staticmethod
    def _route(contact):
        """(route_type, path_len_raw, path) for a packet to this contact."""
        path = (contact or {}).get("path")
        if path and (contact or {}).get("route_mode") != "flood":
            return (ROUTE_TYPE_DIRECT, (contact.get("path_raw") or len(path)), path)
        return (ROUTE_TYPE_FLOOD, encode_path_len(0), b"")

    def _handle_ack(self, pkt):
        """A bare ACK packet (direct-routed) -> mark the matching sent DM delivered."""
        ack = meshcore_dm.decode_ack(pkt.payload)
        if ack is None:
            return False
        if self._sessions and self._keep_alive_acked(ack):
            return True
        return self._mark_delivered(ack, pkt.snr)

    def _handle_multipart(self, pkt):
        """One of a set of packets; only the extra copies of an ACK are used (Mesh.cpp)."""
        p = bytes(pkt.payload)
        if len(p) < 5 or p[0] & 0x0F != PAYLOAD_TYPE_ACK:
            return False
        ack = p[1:5]
        if self._sessions and self._keep_alive_acked(ack):
            return True
        return self._mark_delivered(ack, pkt.snr)

    def extra_acks(self):
        """How many extra copies go before each direct ACK we send: 0, 1 or 2."""
        try:
            from mpos import SharedPreferences
            n = SharedPreferences(NICKNAME_PREFS).get_int("extra_acks", 0)
        except Exception:
            n = 0
        return n if n in EXTRA_ACKS else 0

    def set_extra_acks(self, n):
        if n not in EXTRA_ACKS:
            return
        try:
            ed = self._editor()
            ed.put_int("extra_acks", n)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: extra acks error:", repr(e))
        self._extra_acks_cache = n

    def _note_echo(self, h):
        """We heard one of our own channel packets come back: a repeater re-flooded it. That
        is the only delivery proof a channel offers, so count it. (A DM echo proves nothing
        about the recipient; DMs wait for the real ack.)"""
        channel, msg = self._own[h]
        msg["heard"] = msg.get("heard", 0) + 1
        msg["unheard"] = False
        for rec in self._retries:
            if rec["kind"] == "ch" and rec["msg"] is msg:
                self._retries.remove(rec)
                break
        self._notify("message", (channel, msg))

    def _remember_own(self, h, channel, msg):
        self._own[h] = (channel, msg)
        self._own_order.append(h)
        while len(self._own_order) > MAX_OWN_HASHES:
            self._own.pop(self._own_order.pop(0), None)

    def _retry_tick(self):
        """Resend a DM that wasn't acked within RETRY_AFTER_MS; mark a channel message that no
        repeater echoed within that time as unheard."""
        for rec in list(self._retries):
            sent = rec.get("sent_ms")
            if sent is None or self._ms_since(sent) < RETRY_AFTER_MS:
                continue                     # still queued, or not yet timed out
            msg = rec["msg"]
            if rec["kind"] == "ch":
                self._retries.remove(rec)
                if not msg.get("heard"):
                    msg["unheard"] = True
                    self._notify("message", (rec["channel"], msg))
                continue
            if rec.get("direct"):
                # It went out on the learned route and was not acked, so that route is
                # stale (a repeater moved/died). Forget it: the resend floods and the
                # contact will teach us the new route with its next PATH return.
                self._reset_path(rec["pubkey"])
            if rec["attempt"] + 1 >= DM_MAX_SENDS:
                self._give_up(rec)
                continue
            rec["attempt"] += 1
            msg["attempt"] = rec["attempt"]
            self._tx_dm(rec)
            print("MeshCore: no ack, resending DM (attempt %d)" % (rec["attempt"] + 1))
            self._notify("dm", (rec["pubkey"], msg))

    def _give_up(self, rec):
        """Out of attempts -- mark the DM failed so the chat can offer a resend."""
        rec["msg"]["failed"] = True
        try:
            self._retries.remove(rec)
        except ValueError:
            pass
        print("MeshCore: giving up on the DM (no ack after %d sends)" % DM_MAX_SENDS)
        self._save_history(rec["pubkey"])
        self._notify("dm", (rec["pubkey"], rec["msg"]))

    def _cancel_retry(self, msg):
        for rec in list(self._retries):
            if rec["msg"] is msg:
                self._retries.remove(rec)
                for a in rec.get("acks", ()):        # drop the other attempts' pending acks
                    self._pending_acks.pop(a, None)
                return

    def _register_pending(self, ack_hex, pubkey_hex, msg):
        self._pending_acks[ack_hex] = (pubkey_hex, msg)
        self._pending_order.append(ack_hex)
        while len(self._pending_order) > 32:
            self._pending_acks.pop(self._pending_order.pop(0), None)

    def _mark_delivered(self, ack4, snr=None):
        key = ack4.hex() if hasattr(ack4, "hex") else ack4
        entry = self._pending_acks.pop(key, None)
        if not entry:
            return False
        try:
            self._pending_order.remove(key)
        except ValueError:
            pass
        pub_hex, msg = entry
        msg["delivered"] = True
        msg["ack_ts"] = self._timestamp()
        msg["ack_snr"] = snr
        self._cancel_retry(msg)      # acked (possibly for an earlier attempt) -> stop resending
        print("MeshCore: DM delivered (ack %s)" % key)
        self._save_history(pub_hex)
        self._notify("dm", (pub_hex, msg))
        return True

    # --- quick replies and auto-add ----------------------------------------- #
    def quick_replies(self):
        # An explicit default: MicroPythonOS answers [] for a missing list key, which would
        # read as "the user removed every reply".
        unset = ["\x00unset"]
        try:
            from mpos import SharedPreferences
            saved = SharedPreferences(NICKNAME_PREFS).get_list("quick_replies", unset)
        except Exception:
            saved = unset
        if not isinstance(saved, list) or saved == unset:
            return list(DEFAULT_QUICK_REPLIES)
        return [str(t) for t in saved]

    def set_quick_replies(self, texts):
        """Keep the non-empty ones (trimmed, at most MAX_QUICK_REPLY_LEN characters), at most
        MAX_QUICK_REPLIES of them."""
        clean = []
        for t in texts or ():
            t = (t or "").strip()[:MAX_QUICK_REPLY_LEN]
            if t:
                clean.append(t)
        clean = clean[:MAX_QUICK_REPLIES]
        try:
            ed = self._editor()
            ed.put_list("quick_replies", clean)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: quick replies error:", repr(e))
        return clean

    def auto_add_settings(self):
        """{enabled, all, chat, rptr, room, sensor, max_hops}: whether nodes become contacts
        when their advert is heard; all types, or the types switched on (kept while "all" is
        on); max_hops None means any distance. Saved before the main switch existed, the
        types alone decide: on when any of them is."""
        out = dict(AUTO_ADD_DEFAULTS)
        try:
            from mpos import SharedPreferences
            saved = SharedPreferences(NICKNAME_PREFS).get_dict("auto_add", {}) or {}
            for k in AUTO_ADD_KINDS + ("all",):
                if k in saved:
                    out[k] = bool(saved[k])
            out["enabled"] = bool(saved["enabled"]) if "enabled" in saved else \
                any(out[k] for k in AUTO_ADD_KINDS)
            out["max_hops"] = self._hop_limit(saved.get("max_hops"))
        except Exception:
            pass
        return out

    @staticmethod
    def _hop_limit(v):
        """A hop count 0..64 or None (no limit); anything else is no limit."""
        if v is None or v == "":
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n if 0 <= n <= MAX_HOPS else None

    def set_auto_add(self, **changes):
        cur = self.auto_add_settings()
        for k, v in changes.items():
            if k == "max_hops":
                cur[k] = self._hop_limit(v)
            elif k in cur:
                cur[k] = bool(v)
        if "enabled" in changes:
            self._auto_add_switch_set = True
        elif not getattr(self, "_auto_add_switch_set", False):
            try:
                from mpos import SharedPreferences
                saved = SharedPreferences(NICKNAME_PREFS).get_dict("auto_add", {}) or {}
                self._auto_add_switch_set = "enabled" in saved
            except Exception:
                pass
            if not self._auto_add_switch_set:   # never switched: the types decide
                cur["enabled"] = any(cur[k] for k in AUTO_ADD_KINDS)
        stored = dict(cur)
        if not getattr(self, "_auto_add_switch_set", False):
            del stored["enabled"]
        try:
            ed = self._editor()
            ed.put_dict("auto_add", stored)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: auto-add error:", repr(e))
        self._auto_add_cache = cur
        return cur

    def _auto_add(self, node):
        pk = node.get("pubkey")
        if not pk or pk in self._contacts:
            return
        cfg = getattr(self, "_auto_add_cache", None)
        if cfg is None:
            cfg = self._auto_add_cache = self.auto_add_settings()
        if not cfg.get("enabled"):
            return
        kind = {1: "chat", 2: "rptr", 3: "room", 4: "sensor"}.get(node.get("type"))
        if not kind or not (cfg.get("all") or cfg.get(kind)):
            return
        limit = cfg.get("max_hops")
        if limit is not None and (node.get("hops") or 0) > limit:
            return
        self.add_contact(pk, node.get("name"), node.get("type"))

    # --- buzzer ------------------------------------------------------------ #
    # --- path hash size and region scopes ----------------------------------- #
    def _load_routing(self):
        self._hash_size = 1
        self._regions = []
        self._default_region = None
        self._channel_scopes = {}       # channel -> "none" or a region; absent: the default
        try:
            from mpos import SharedPreferences
            prefs = SharedPreferences(NICKNAME_PREFS)
            size = prefs.get_int("path_hash_size", 1)
            self._hash_size = size if size in (1, 2, 3) else 1
            self._regions = sorted(set(prefs.get_list("regions", []) or []))
            d = prefs.get_string("default_region", "") or ""
            self._default_region = d if d in self._regions else None
            self._channel_scopes = dict(prefs.get_dict("channel_scopes", {}) or {})
        except Exception as e:
            print("MeshCore: routing settings load error:", repr(e))

    def _save_routing(self):
        try:
            ed = self._editor()
            ed.put_int("path_hash_size", self._hash_size)
            ed.put_list("regions", list(self._regions))
            ed.put_string("default_region", self._default_region or "")
            ed.put_dict("channel_scopes", dict(self._channel_scopes))
            self._commit(ed)
        except Exception as e:
            print("MeshCore: routing settings save error:", repr(e))

    def path_hash_size(self):
        """Bytes per hop in the paths of the floods we send (1, 2 or 3)."""
        return self._hash_size

    def set_path_hash_size(self, size):
        if size not in (1, 2, 3):
            return (False, "1, 2 or 3 bytes")
        self._hash_size = size
        self._save_routing()
        return (True, None)

    def regions(self):
        return list(self._regions)

    def add_region(self, text):
        name = meshcore_region.clean_name(text)
        if name is None:
            return (False, "letters, digits, - _ . only, up to %d" % meshcore_region.MAX_NAME)
        if name in self._regions:
            return (False, "already in the list")
        self._regions = sorted(self._regions + [name])
        self._save_routing()
        return (True, None)

    def remove_region(self, name):
        self._regions = [r for r in self._regions if r != name]
        if self._default_region == name:
            self._default_region = None
        for ch in [c for c, v in self._channel_scopes.items() if v == name]:
            del self._channel_scopes[ch]
        self._save_routing()

    def default_region(self):
        return self._default_region

    def set_default_region(self, name):
        """The scope of our floods (None: unscoped)."""
        if name is not None and name not in self._regions:
            return (False, "not a known region")
        self._default_region = name
        self._save_routing()
        return (True, None)

    def channel_scope(self, channel):
        """"default" (the default scope), "none" (unscoped) or a region name."""
        return self._channel_scopes.get(channel, "default")

    def set_channel_scope(self, channel, value):
        if value not in ("default", "none") and value not in self._regions:
            return (False, "not a known region")
        if value == "default":
            self._channel_scopes.pop(channel, None)
        else:
            self._channel_scopes[channel] = value
        self._save_routing()
        return (True, None)

    def _scope_for(self, channel=None):
        if channel is not None:
            v = self._channel_scopes.get(channel, "default")
            if v == "none":
                return None
            if v != "default":
                return v
        return self._default_region

    def _finish_flood(self, raw, channel=None):
        """A flood we start (no hops yet) gets our path hash size and, with a scope, becomes a
        TRANSPORT_FLOOD carrying the region's transport code (Mesh::sendFlood)."""
        try:
            pkt = MeshCorePacket.parse(raw)
        except ValueError:
            return raw
        if pkt.route_type != ROUTE_TYPE_FLOOD or pkt.path_len_raw & 63:
            return raw
        pkt.path_len_raw = encode_path_len(0, self._hash_size)
        name = self._scope_for(channel)
        if name:
            pkt.header = (pkt.header & ~PH_ROUTE_MASK) | ROUTE_TYPE_TRANSPORT_FLOOD
            key = meshcore_region.region_key(name)
            pkt.transport_codes = (meshcore_region.transport_code(key, pkt.payload_type,
                                                                  pkt.payload), 0)
        return pkt.to_bytes()

    def _route_info(self, pkt):
        """What a received packet says about its way here: path (hex), hash size, region."""
        info = {"path": bytes(pkt.path).hex(), "hsize": (pkt.path_len_raw >> 6) + 1}
        if pkt.route_type == ROUTE_TYPE_TRANSPORT_FLOOD:
            info["region"] = meshcore_region.match_region(
                pkt.transport_codes[0], pkt.payload_type, pkt.payload, self._regions) or "?"
        return info

    # --- own position ---------------------------------------------------------- #
    def _load_position(self):
        self._position = None           # {"lat", "lon", "source": "manual" | "gps"}
        self._share_position = False
        self._gps_on = False
        self._gps_state = "off"         # off, searching, no_fix, fix, absent
        self._gps_since = 0             # when the search started
        self._gps_heard = None          # when the GPS last said anything
        self._gps_polled = 0
        self._gps_saved = None          # (ms, lat, lon) of the last write
        try:
            from mpos import SharedPreferences
            prefs = SharedPreferences(NICKNAME_PREFS)
            loc = prefs.get_dict("position", {}) or {}
            if "lat" in loc and "lon" in loc:
                self._position = {"lat": float(loc["lat"]), "lon": float(loc["lon"]),
                                  "source": loc.get("source", "manual")}
            self._share_position = bool(prefs.get_bool("share_position", False))
            if prefs.get_bool("gps", False):
                self.set_gps_enabled(True)
        except Exception as e:
            print("MeshCore: position load error:", repr(e))

    def position(self):
        """Our position: {"lat", "lon", "source"} or None."""
        return dict(self._position) if self._position else None

    def set_position(self, lat, lon, source="manual"):
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return (False, "not a number")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):     # also false for NaN
            return (False, "latitude -90..90, longitude -180..180")
        if lat == 0 and lon == 0:
            return (False, "0, 0 means no position on the mesh")
        self._position = {"lat": lat, "lon": lon, "source": source}
        self._save_position()
        self._notify("position", self.position())
        return (True, None)

    def clear_position(self):
        self._position = None
        self._save_position()
        self._notify("position", None)

    def _save_position(self):
        try:
            ed = self._editor()
            ed.put_dict("position", self._position or {})
            self._commit(ed)
            if self._position:
                self._gps_saved = (self._now_ms(), self._position["lat"], self._position["lon"])
        except Exception as e:
            print("MeshCore: position save error:", repr(e))

    def share_position(self):
        return self._share_position

    def set_share_position(self, on):
        self._share_position = bool(on)
        try:
            ed = self._editor()
            ed.put_bool("share_position", self._share_position)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: share setting error:", repr(e))

    def gps_status(self):
        return {"enabled": self._gps_on, "state": self._gps_state}

    def set_gps_enabled(self, on):
        """Switch the GPS on or off. Returns (ok, err); without a GPS it stays off."""
        gps = self._gps_manager()
        if on and (gps is None or not gps.has_nmea_source()):
            self._gps_switch(False, "absent")
            return (False, "No GPS found")
        if on:
            self._gps_since = self._now_ms()
            self._gps_heard = None
            gps.add_nmea_listener(self._on_nmea)
            self._gps_switch(True, "searching")
        else:
            if gps is not None:
                gps.remove_nmea_listener(self._on_nmea)
            self._gps_switch(False, "off")
        return (True, None)

    def _gps_switch(self, on, state):
        changed = on != self._gps_on
        self._gps_on, self._gps_state = on, state
        if changed or not on:
            try:
                ed = self._editor()
                ed.put_bool("gps", on)
                self._commit(ed)
            except Exception as e:
                print("MeshCore: gps setting error:", repr(e))
        self._notify("position", self.position())

    @staticmethod
    def _gps_manager():
        try:
            from mpos import GPSManager
            return GPSManager if hasattr(GPSManager, "has_nmea_source") else None
        except ImportError:
            return None

    def _gps_tick(self):
        """Worker idle path: bring in the GPS's sentences; a GPS that never says anything is
        not there, and is switched off."""
        if not self._gps_on:
            return
        now = self._now_ms()
        if tdiff(now, self._gps_polled) >= GPS_POLL_MS:
            self._gps_polled = now
            gps = self._gps_manager()
            if gps is not None:
                gps.poll()
        if self._gps_heard is None and tdiff(now, self._gps_since) > GPS_DETECT_MS:
            gps = self._gps_manager()
            if gps is not None:
                gps.remove_nmea_listener(self._on_nmea)
            print("MeshCore: no GPS answered, GPS switched off")
            self._gps_switch(False, "absent")

    def _on_nmea(self, sentence):
        now = self._now_ms()
        self._gps_heard = now
        gps = self._gps_manager()
        fix = gps.position_from_nmea(sentence) if gps is not None else None
        if fix is None:
            if self._gps_state == "searching":       # it answers, without a fix yet
                self._gps_state = "no_fix"
                self._notify("position", self.position())
            return
        lat, lon = fix
        first = self._gps_state != "fix"
        self._gps_state = "fix"
        self._position = {"lat": lat, "lon": lon, "source": "gps"}
        saved = self._gps_saved
        if (first or saved is None or tdiff(now, saved[0]) >= GPS_SAVE_MS
                or _metres(lat, lon, saved[1], saved[2]) >= GPS_SAVE_M):
            self._save_position()
            self._notify("position", self.position())

    def sound_settings(self):
        """{enabled, channel, dm, advert}: whether the buzzer sounds, and for what."""
        out = dict(SOUND_DEFAULTS)
        try:
            from mpos import SharedPreferences
            saved = SharedPreferences(NICKNAME_PREFS).get_dict("sound", {}) or {}
            for k in out:
                if k in saved:
                    out[k] = bool(saved[k])
        except Exception:
            pass
        return out

    def set_sound_settings(self, **changes):
        cur = self.sound_settings()
        for k, v in changes.items():
            if k in cur:
                cur[k] = bool(v)
        try:
            ed = self._editor()
            ed.put_dict("sound", cur)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: sound settings error:", repr(e))
        self._sound_cache = cur
        return cur

    def sound_override(self, key):
        """"on", "off" or "default" (follow the settings) for a channel name or contact key."""
        return self._overrides().get(key, "default")

    def set_sound_override(self, key, mode):
        ov = dict(self._overrides())
        if mode in ("on", "off"):
            ov[key] = mode
        else:
            ov.pop(key, None)
        try:
            ed = self._editor()
            ed.put_dict("sound_overrides", ov)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: sound override error:", repr(e))
        self._override_cache = ov

    def _overrides(self):
        ov = getattr(self, "_override_cache", None)
        if ov is None:
            try:
                from mpos import SharedPreferences
                ov = SharedPreferences(NICKNAME_PREFS).get_dict("sound_overrides", {}) or {}
            except Exception:
                ov = {}
            self._override_cache = ov
        return ov

    def quiet_hours(self):
        """{enabled, start, end}: no sounds from `start` to `end`, minutes after local midnight."""
        out = {"enabled": False, "start": 22 * 60, "end": 7 * 60}
        try:
            from mpos import SharedPreferences
            saved = SharedPreferences(NICKNAME_PREFS).get_dict("quiet_hours", {}) or {}
            out["enabled"] = bool(saved.get("enabled", False))
            for k in ("start", "end"):
                v = saved.get(k)
                if isinstance(v, int) and 0 <= v < 24 * 60:
                    out[k] = v
        except Exception:
            pass
        return out

    def set_quiet_hours(self, enabled=None, start=None, end=None):
        cur = self.quiet_hours()
        if enabled is not None:
            cur["enabled"] = bool(enabled)
        for k, v in (("start", start), ("end", end)):
            if isinstance(v, int) and 0 <= v < 24 * 60:
                cur[k] = v
        try:
            ed = self._editor()
            ed.put_dict("quiet_hours", cur)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: quiet hours error:", repr(e))
        self._quiet_cache = cur
        return cur

    def _local_minute(self):
        """Minutes after local midnight, None while the clock is not set."""
        now = unix_time()
        if now < CLOCK_VALID_AFTER:
            return None
        tz = 0
        try:
            import time
            import mpos.time
            tz = ui_model.tz_offset(mpos.time.localtime(), time.gmtime())
        except Exception:
            pass
        return ((now + tz) // 60) % (24 * 60)

    def _quiet_now(self):
        q = getattr(self, "_quiet_cache", None)
        if q is None:
            q = self._quiet_cache = self.quiet_hours()
        if not q["enabled"] or q["start"] == q["end"]:
            return False
        m = self._local_minute()
        if m is None:
            return False
        if q["start"] < q["end"]:
            return q["start"] <= m < q["end"]
        return m >= q["start"] or m < q["end"]

    def _chime(self, kind, key=None):
        """Beep for an incoming channel message, direct message or advert: a per-channel or
        per-contact override wins ("on" even with the buzzer off, "off" always), otherwise
        the settings decide (buzzer on, and All or this kind chosen). At most one beep per
        TUNE_GAP_MS. Nothing at all during quiet hours."""
        mode = self._overrides().get(key) if key is not None else None
        if mode == "off" or self._quiet_now():
            return
        if mode != "on":
            cfg = getattr(self, "_sound_cache", None)
            if cfg is None:
                cfg = self._sound_cache = self.sound_settings()
            if kind == "mention" and not (cfg["all"] or cfg.get("mention")):
                kind = "channel"          # a mention is still a channel message
            if not cfg["enabled"] or not (cfg["all"] or cfg.get(kind)):
                return
        now = self._now_ms()
        last = getattr(self, "_last_tune_ms", None)
        if last is not None and tdiff(now, last) < TUNE_GAP_MS:
            return
        self._last_tune_ms = now
        try:
            self._play_tune(kind)
        except Exception as e:
            print("MeshCore: buzzer error:", repr(e))

    def has_buzzer(self):
        try:
            from mpos import AudioManager
            return AudioManager.find_output_by_kind("buzzer") is not None
        except Exception:
            return False

    def _play_tune(self, kind):
        """Play TUNES[kind] on the device's buzzer output (no-op without one)."""
        from mpos import AudioManager
        out = AudioManager.find_output_by_kind("buzzer")
        if out is None:
            return
        AudioManager.player(rtttl=TUNES[kind], output=out,
                            stream_type=AudioManager.STREAM_NOTIFICATION).start()

    def test_sound(self):
        self._play_tune("test")

    # --- repeaters and room servers ---------------------------------------- #
    def server_session(self, pubkey_hex):
        """Login state and latest results for a repeater or room server:
        {state: idle|pending|ok|failed, role, admin, permissions, error, pending,
         results: {status|neighbours|telemetry|owner|ping|trace: {data, at}}}."""
        s = self._sessions.get(pubkey_hex)
        if s is None:
            s = {"state": "idle", "role": None, "admin": False, "permissions": 0,
                 "error": None, "pending": None, "results": {}}
            self._sessions[pubkey_hex] = s
        return s

    def remembered_password(self, pubkey_hex):
        """The password that last logged in to this server, if it was to be remembered."""
        return self._passwords().get(pubkey_hex)

    def _passwords(self):
        pw = getattr(self, "_pw_cache", None)
        if pw is None:
            try:
                from mpos import SharedPreferences
                pw = SharedPreferences(NICKNAME_PREFS, filename="servers.json").get_dict(
                    "passwords", {}) or {}
            except Exception:
                pw = {}
            self._pw_cache = pw
        return pw

    def _keep_password(self, pubkey_hex, password):
        """Store (or with None, drop) the password for this server."""
        pw = dict(self._passwords())
        if password is None:
            if pubkey_hex not in pw:
                return
            pw.pop(pubkey_hex)
        elif pw.get(pubkey_hex) == password:
            return
        else:
            pw[pubkey_hex] = password
        try:
            ed = self._editor("servers.json")
            ed.put_dict("passwords", pw)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: password store error:", repr(e))
        self._pw_cache = pw

    def login(self, pubkey_hex, password=None, remember=True, auto=False):
        """Log in to a repeater or room server. password None: the remembered one, else
        guest; "" is always guest. A password that works is remembered (remember=False
        forgets it). The reply, or a timeout, arrives as a "server" event. Returns (ok, err)
        for the sending. `auto`: a login the app makes by itself to get back in."""
        if password is None:
            password = self.remembered_password(pubkey_hex) or ""
        pub, _ = self.get_identity()
        if pub is None:
            return (False, "no identity")
        if pubkey_hex not in self._contacts:
            node = self._nodes.get(pubkey_hex) or {}
            ok, err = self.add_contact(pubkey_hex, node.get("name"), node.get("type", 2))
            if not ok:
                return (False, err)
        contact = self._contacts[pubkey_hex]
        secret = self._node_secret(contact)
        if secret is None:
            return (False, "no shared secret")
        room = contact.get("type") == ADV_TYPE_ROOM
        payload = meshcore_server.build_login(
            secret, pub, int(pubkey_hex[:2], 16), self._timestamp(unique=True), password,
            contact.get("sync_since", 0) if room else None)
        s = self.server_session(pubkey_hex)
        s["state"] = "pending"
        s["error"] = None
        if not auto:
            s["relogin_tried"] = False   # one automatic login again per login by hand
        self._server_send(pubkey_hex, contact, PAYLOAD_TYPE_ANON_REQ, payload,
                          {"kind": "login", "tag": None, "password": password,
                           "remember": remember, "auto": auto})
        return (True, None)

    _REQUESTS = {"status": meshcore_server.REQ_GET_STATUS,
                 "neighbours": meshcore_server.REQ_GET_NEIGHBOURS,
                 "telemetry": meshcore_server.REQ_GET_TELEMETRY,
                 "owner": meshcore_server.REQ_GET_OWNER_INFO}

    def request_server(self, pubkey_hex, kind):
        """Ask a logged-in server for "status", "neighbours", "telemetry" or "owner"; the
        answer lands in server_session(...)["results"][kind]. One request at a time."""
        s = self.server_session(pubkey_hex)
        if s["state"] != "ok":
            return (False, "log in first")
        contact = self._contacts.get(pubkey_hex)
        pub, _ = self.get_identity()
        secret = self._node_secret(contact) if contact else None
        if secret is None or pub is None:
            return (False, "no shared secret")
        params = None
        if kind == "neighbours":
            params = meshcore_server.neighbours_params(count=20, order=2, prefix_len=4)
        elif kind == "telemetry":
            params = bytes([0]) + bytes(3) + self._rand4()   # 0 = every permitted kind
        tag = self._timestamp(unique=True)
        payload = meshcore_server.build_request(secret, pub, int(pubkey_hex[:2], 16), tag,
                                                self._REQUESTS[kind], params)
        s["error"] = None
        self._server_send(pubkey_hex, contact, PAYLOAD_TYPE_REQ, payload,
                          {"kind": kind, "tag": tag})
        return (True, None)

    def ping(self, pubkey_hex):
        """A zero-hop trace to the node: round trip, SNR there and back."""
        return self._send_trace(pubkey_hex, "ping", [int(pubkey_hex[:2], 16)])

    def trace(self, pubkey_hex):
        """A trace along the learned route to the node and back: SNR at every hop."""
        contact = self._contacts.get(pubkey_hex) or {}
        path = list(contact.get("path") or b"")
        return self._send_trace(pubkey_hex, "trace", path + [int(pubkey_hex[:2], 16)] + path[::-1])

    def _send_trace(self, pubkey_hex, kind, hashes):
        tag = int.from_bytes(self._rand4(), "little")
        payload = meshcore_server.build_trace(tag, 0, bytes(hashes))
        pkt = MeshCorePacket(make_header(ROUTE_TYPE_DIRECT, PAYLOAD_TYPE_TRACE),
                             encode_path_len(0), b"", payload)
        s = self.server_session(pubkey_hex)
        s["error"] = None
        airtime = self._time_on_air_ms(len(pkt.to_bytes()))
        s["trace"] = {"kind": kind, "tag": tag, "t0": self._now_ms(), "hops": len(hashes),
                      "deadline": self._deadline(meshcore_server.timeout_ms(airtime, len(hashes)))}
        rec = s["trace"]
        self._enqueue_tx(pkt.to_bytes(), on_sent=lambda ok: rec.update(t0=self._now_ms()))
        self._notify("server", pubkey_hex)
        return (True, None)

    def _rand4(self):
        try:
            import os
            return os.urandom(4)
        except Exception:
            return bytes(self._rand_byte() for _ in range(4))

    def _deadline(self, ms):
        # The firmware's estimate plus room for our own TX queue and duty cycle.
        return tadd(self._now_ms(), ms + 3000)

    def _server_send(self, pubkey_hex, contact, ptype, payload, pending):
        """Send to the server along its route; `pending` (None for a keep-alive) is what the
        session then waits for."""
        route, path_raw, path = self._route(contact)
        pkt = MeshCorePacket(make_header(route, ptype), path_raw, path, payload)
        raw = pkt.to_bytes()
        hops = (path_raw & 63) if route == ROUTE_TYPE_DIRECT else None
        if pending is not None:
            pending["deadline"] = self._deadline(
                meshcore_server.timeout_ms(self._time_on_air_ms(len(raw)), hops))
            self.server_session(pubkey_hex)["pending"] = pending
        try:
            self._remember(pkt.packet_hash())
        except Exception:
            pass
        self._enqueue_tx(raw)
        self._notify("server", pubkey_hex)

    def _handle_response(self, pkt):
        self_hash = self.node_id()
        if self_hash is None or len(pkt.payload) < 2 or pkt.payload[0] != (self_hash & 0xFF):
            return False
        got = meshcore_dm.decode_envelope(pkt.payload, self_hash,
                                          self._contact_candidates(pkt.payload[1]))
        if got is None:
            return False
        return self._on_server_reply(got[0].hex(), got[1])

    def _server_active(self, pubkey_hex):
        """We heard from the server: the session is alive, the next keep-alive waits."""
        s = self._sessions.get(pubkey_hex)
        if s and s.get("ka_ms"):
            now = self._now_ms()
            s["last_activity"] = now
            s["next_ping"] = tadd(now, s["ka_ms"])

    def _send_keep_alive(self, pubkey_hex, s):
        """REQ_TYPE_KEEP_ALIVE with our sync_since, direct only (a room answers only those);
        its ACK proves the session is alive and lets the room push posts again."""
        import hashlib
        import struct
        s["next_ping"] = tadd(self._now_ms(), s["ka_ms"])
        contact = self._contacts.get(pubkey_hex)
        pub, _ = self.get_identity()
        secret = self._node_secret(contact) if contact else None
        if secret is None or pub is None or self._route(contact)[0] != ROUTE_TYPE_DIRECT:
            return
        params = struct.pack("<I", contact.get("sync_since", 0) & 0xFFFFFFFF)
        tag = self._timestamp()          # the room only refuses an older one; no flash write
        pt = struct.pack("<IB", tag & 0xFFFFFFFF, meshcore_server.REQ_KEEP_ALIVE) + params
        s["ka_ack"] = hashlib.sha256(pt + bytes(pub)).digest()[:4]
        payload = meshcore_server.build_request(secret, pub, int(pubkey_hex[:2], 16), tag,
                                                meshcore_server.REQ_KEEP_ALIVE, params)
        self._server_send(pubkey_hex, contact, PAYLOAD_TYPE_REQ, payload, None)

    def _keep_alive_acked(self, ack):
        for pubkey_hex, s in self._sessions.items():
            if s.get("ka_ack") is not None and s["ka_ack"] == bytes(ack):
                s["ka_ack"] = None
                self._server_active(pubkey_hex)
                return True
        return False

    def _relogin(self, pubkey_hex, s, why):
        """Log in again by ourselves, once per login by hand: with the remembered password,
        else as a guest."""
        s["relogin_tried"] = True
        if not self.is_contact(pubkey_hex):
            self._sessions.pop(pubkey_hex, None)
            return
        self.login(pubkey_hex, None, auto=True)
        s["error"] = why + ", logging in again"
        self._notify("server", pubkey_hex)

    def _on_server_reply(self, pubkey_hex, plaintext):
        s = self._sessions.get(pubkey_hex)
        pending = s and s.get("pending")
        if s:
            self._server_active(pubkey_hex)
        if not pending:
            return False
        if pending["kind"] == "login":
            r = meshcore_server.parse_login_reply(plaintext)
            if r is None:
                return False
            s.update(state="ok", role=r["role"], admin=r["admin"], permissions=r["permissions"],
                     server_ts=r["server_ts"], fw_level=r["fw_level"], pending=None, error=None,
                     ka_ack=None)
            room = (self._contacts.get(pubkey_hex) or {}).get("type") == ADV_TYPE_ROOM
            s["ka_ms"] = (r["keep_alive_s"] or (ROOM_KEEP_ALIVE_S if room else 0)) * 1000
            self._server_active(pubkey_hex)
            now = unix_time()
            skew = r["server_ts"] - now if now >= CLOCK_VALID_AFTER else 0
            s["clock_skew_s"] = skew if abs(skew) > CLOCK_SKEW_WARN_S else None
            if not pending["remember"]:
                self._keep_password(pubkey_hex, None)
            elif pending["password"]:
                self._keep_password(pubkey_hex, pending["password"])
            self._note_heard_ts(r["server_ts"])
            self._notify("server", pubkey_hex)
            return True
        tag, body = meshcore_server.parse_response(plaintext)
        if tag != pending["tag"]:
            return False                 # a late reply to an older request
        kind = pending["kind"]
        room = (self._contacts.get(pubkey_hex) or {}).get("type") == ADV_TYPE_ROOM
        if kind == "status":
            data = meshcore_server.parse_status(body, room=room)
        elif kind == "neighbours":
            total, rows = meshcore_server.parse_neighbours(body, prefix_len=4)
            data = {"total": total, "rows": rows}
        elif kind == "telemetry":
            data = meshcore_server.parse_lpp(body)
        else:
            data = meshcore_server.parse_owner_info(body)
        s["results"][kind] = {"data": data, "at": self._timestamp()}   # mesh time if no clock
        s["pending"] = None
        s["error"] = None
        self._notify("server", pubkey_hex)
        return True

    def _handle_trace(self, pkt):
        if len(pkt.payload) < 9:
            return False
        try:
            r = meshcore_server.parse_trace(pkt.payload, pkt.path, pkt.snr)
        except Exception:
            return False
        for pubkey_hex, s in self._sessions.items():
            t = s.get("trace")
            if not t or t["tag"] != r["tag"]:
                continue
            if len(r["hop_snrs"]) < len(r["hashes"]):
                return True              # still on its way: a repeater passing it on
            r["rtt_ms"] = tdiff(self._now_ms(), t["t0"])
            s["results"][t["kind"]] = {"data": r, "at": self._timestamp()}
            s["trace"] = None
            self._notify("server", pubkey_hex)
            return True
        return False

    def _server_tick(self):
        now = self._now_ms()
        for pubkey_hex, s in list(self._sessions.items()):   # _relogin may drop one
            name = (self._contacts.get(pubkey_hex) or {}).get("name") or pubkey_hex[:8]
            p = s.get("pending")
            if p and tdiff(now, p["deadline"]) > 0:
                s["pending"] = None
                s["error"] = "no answer from %s" % name
                if p["kind"] == "login":
                    s["state"] = "failed"
                    if p.get("auto"):
                        s["error"] = ("no answer from %s: out of range, or it has forgotten "
                                      "you (log in with the password)" % name)
                elif (s["state"] == "ok" and not s.get("relogin_tried")
                      and (self.remembered_password(pubkey_hex) or s.get("role") == "guest")):
                    # never as a guest behind an admin's back
                    self._relogin(pubkey_hex, s, "no answer from %s" % name)
                    continue
                self._notify("server", pubkey_hex)
            elif s["state"] == "ok" and s.get("ka_ms") and not p:
                contact = self._contacts.get(pubkey_hex)
                if contact is None or self._route(contact)[0] != ROUTE_TYPE_DIRECT:
                    # no keep-alive without a direct route (a room answers only those), so
                    # nothing to expire either: a flooded login every few minutes is no help
                    s["last_activity"] = now
                    continue
                if tdiff(now, s["last_activity"]) > s["ka_ms"] * 5 // 2:
                    if not s.get("relogin_tried"):
                        self._relogin(pubkey_hex, s, "%s went quiet" % name)
                        continue
                    s["state"] = "failed"
                    s["error"] = "lost the session with %s" % name
                    self._notify("server", pubkey_hex)
                elif tdiff(now, s["next_ping"]) >= 0:
                    self._send_keep_alive(pubkey_hex, s)
            t = s.get("trace")
            if t and tdiff(now, t["deadline"]) > 0:
                s["trace"] = None
                s["error"] = "no answer to the %s" % t["kind"]
                self._notify("server", pubkey_hex)

    def _handle_room_post(self, got, pkt, contact):
        """A post a room server pushes to us: timestamp, author key prefix, text. Acked with a
        hash over the post and OUR key (BaseChatMesh, TXT_TYPE_SIGNED_PLAIN)."""
        import hashlib
        core = got["core"]
        if len(core) < 9:
            return False
        room_hex = got["pubkey"].hex()
        self._server_active(room_hex)
        prefix = core[5:9].hex()
        try:
            text = core[9:].decode("utf-8")
        except Exception:
            text = "".join("\\x%02x" % b for b in core[9:])
        author = None
        for pk, n in list(self._contacts.items()) + list(self._nodes.items()):
            if pk.startswith(prefix):
                author = n.get("name")
                break
        pub, _ = self.get_identity()
        if pub is not None and prefix == bytes(pub[:4]).hex():
            author = self.nickname()
        msg = {"ts": got["timestamp"], "sender": author or prefix.upper(), "text": text,
               "snr": pkt.snr, "hops": self._hops(pkt), "rx_ms": self._now_ms(),
               "incoming": True, "author": prefix}
        if self.is_blocked_name(msg["sender"]):
            pass                          # blocked author: still acked below, never shown
        elif not self._dup_message(("room", room_hex, prefix, text), msg["ts"]):
            self._add_dm(room_hex, msg)
            self._chime("dm", room_hex)
            self._bump_unread(room_hex)
            self._notify("dm", (room_hex, msg))
            self._post_dm_notification(room_hex, contact.get("name") or room_hex[:8], msg)
        if got["timestamp"] > contact.get("sync_since", 0):
            contact["sync_since"] = got["timestamp"]
            self._save_contacts()
        if pub is not None:
            got = dict(got)
            got["ack_hash"] = hashlib.sha256(bytes(core) + bytes(pub)).digest()[:4]
            try:
                self._send_ack(got, pkt)
            except Exception as e:
                print("MeshCore: room ack error:", repr(e))
        return True

    def send_dm(self, pubkey_hex, text):
        """Encrypt + flood a direct text message to a contact. Returns (ok, err)."""
        text = (text or "").strip()
        if not text:
            return (False, "empty message")
        pub, prv = self.get_identity()
        if pub is None:
            return (False, "no identity -- generate one first")
        contact = self._contacts.get(pubkey_hex)
        if contact is None:
            return (False, "not a contact -- add them first")
        secret = self._node_secret(contact)
        if secret is None:
            return (False, "no shared secret")
        try:
            import binascii
            import time
            ts = self._timestamp()
            dst_hash = binascii.unhexlify(pubkey_hex.encode())[0]
        except Exception as e:
            print("MeshCore: dm encode error:", repr(e))
            return (False, str(e))
        msg = {"ts": ts, "sender": self.nickname(), "text": text, "rssi": None,
               "incoming": False, "ack": None, "delivered": False,
               "attempt": 0, "failed": False, "tx": False}
        rec = {"kind": "dm", "pubkey": pubkey_hex, "msg": msg, "text": text, "ts": ts,
               "secret": secret, "pub": pub, "dst_hash": dst_hash, "attempt": 0, "acks": []}
        try:
            self._tx_dm(rec)
        except Exception as e:
            print("MeshCore: dm encode error:", repr(e))
            return (False, str(e))
        self._add_dm(pubkey_hex, msg)
        self._retries.append(rec)          # resent by _retry_tick if no ACK comes back
        self._notify("dm", (pubkey_hex, msg))
        return (True, None)

    def _tx_dm(self, rec):
        """Build and queue one attempt of a DM. Same text and same timestamp every time --
        only the 2-bit attempt counter differs, which is what makes the packet hash (and the
        expected ack, which is hashed over the flags byte) fresh, so repeaters will actually
        re-flood it. Every attempt's ack stays registered: a late ack for an earlier attempt
        still proves delivery."""
        payload, expected_ack = meshcore_dm.encode_dm(
            rec["secret"], rec["pub"], rec["dst_hash"], rec["text"], rec["ts"],
            attempt=rec["attempt"])
        route, path_raw, path = self._route(self._contacts.get(rec["pubkey"]))
        rec["direct"] = (route == ROUTE_TYPE_DIRECT)
        pkt = MeshCorePacket(make_header(route, PAYLOAD_TYPE_TXT_MSG),
                             path_raw, path, payload)
        try:
            self._remember(pkt.packet_hash())   # de-dupe the repeater's echo of our own DM
        except Exception:
            pass
        ack_hex = expected_ack.hex()
        rec["acks"].append(ack_hex)
        rec["msg"]["ack"] = ack_hex
        self._register_pending(ack_hex, rec["pubkey"], rec["msg"])
        rec["sent_ms"] = None              # the retry clock starts when it is on the air
        self._enqueue_tx(pkt.to_bytes(), on_sent=lambda ok: self._on_sent(rec, ok))

    def _add_dm(self, pubkey_hex, msg):
        lst = self._dm_messages.setdefault(pubkey_hex, [])
        lst.append(msg)
        if len(lst) > MAX_MESSAGES:
            del lst[0]
        self._save_history(pubkey_hex)   # persist per-contact chat history

    def get_dm_messages(self, pubkey_hex):
        """A direct chat's messages; in a room, without the posts of blocked sender names."""
        msgs = self._dm_messages.get(pubkey_hex, [])
        names = self._block_store()["names"]
        if not names or (self._contacts.get(pubkey_hex) or {}).get("type") != ADV_TYPE_ROOM:
            return list(msgs)
        return [x for x in msgs if not (x.get("incoming") and x.get("sender") in names)]

    # --- contacts (persisted) ---------------------------------------------- #
    def get_contacts(self):
        """The saved contact list -- the ONLY nodes we can chat with."""
        return sorted(self._contacts.values(),
                      key=lambda c: (c.get("seq", 0), c.get("name") or ""), reverse=True)

    def get_contact(self, pubkey_hex):
        return self._contacts.get(pubkey_hex)

    def is_contact(self, pubkey_hex):
        return pubkey_hex in self._contacts

    def get_learned_companions(self):
        """Nodes heard via advert (kept across restarts), most recent first: companions,
        repeaters and rooms. Adding a companion makes it a persisted contact you can chat
        with."""
        return sorted(self._nodes.values(), key=lambda n: n.get("seq", 0), reverse=True)

    def get_contact_nodes(self):
        """The contacts, each with what was last heard of it as a node (name, SNR, hops,
        when), most recently heard first; contacts never heard come last."""
        out = []
        for c in self._contacts.values():
            n = self._nodes.get(c["pubkey"])
            if n is not None:
                merged = dict(c)
                merged.update(n)
                out.append(merged)
            else:
                out.append(dict(c))
        out.sort(key=lambda n: n.get("seq", -1) if n.get("pubkey") in self._nodes else -1,
                 reverse=True)
        return out

    # --- blocking --------------------------------------------------------------- #
    def _block_store(self):
        b = getattr(self, "_blocked_cache", None)
        if b is None:
            try:
                from mpos import SharedPreferences
                saved = SharedPreferences(NICKNAME_PREFS).get_dict("blocked", {}) or {}
            except Exception:
                saved = {}
            b = {"keys": dict(saved.get("keys") or {}), "names": list(saved.get("names") or [])}
            self._blocked_cache = b
        return b

    def _save_blocked(self, b):
        self._blocked_cache = b
        try:
            ed = self._editor()
            ed.put_dict("blocked", b)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: block list error:", repr(e))
        self._notify("contacts", None)

    def blocked(self):
        """[{kind: key|name, value, label}]: blocked contacts, then blocked sender names."""
        b = self._block_store()
        return ([{"kind": "key", "value": k, "label": v} for k, v in b["keys"].items()] +
                [{"kind": "name", "value": n, "label": n} for n in b["names"]])

    def is_blocked_key(self, pubkey_hex):
        return pubkey_hex in self._block_store()["keys"]

    def is_blocked_name(self, name):
        return name in self._block_store()["names"]

    def block_key(self, pubkey_hex, label=None):
        """Hide the direct messages of this node (they are still acknowledged)."""
        b = self._block_store()
        b["keys"][pubkey_hex] = label or self.chat_name(pubkey_hex)
        self._save_blocked(b)

    def unblock_key(self, pubkey_hex):
        b = self._block_store()
        if b["keys"].pop(pubkey_hex, None) is not None:
            self._save_blocked(b)

    def block_name(self, name):
        """Hide channel messages and room posts from this sender name."""
        b = self._block_store()
        if name and name not in b["names"]:
            b["names"].append(name)
            self._save_blocked(b)

    def unblock_name(self, name):
        b = self._block_store()
        if name in b["names"]:
            b["names"].remove(name)
            self._save_blocked(b)

    def is_favourite(self, pubkey_hex):
        return bool((self._contacts.get(pubkey_hex) or {}).get("fav"))

    def set_favourite(self, pubkey_hex, on):
        """Star a contact (listed first, and under Favourites). False if it is no contact."""
        c = self._contacts.get(pubkey_hex)
        if c is None:
            return False
        c["fav"] = bool(on)
        self._save_contacts()
        self._notify("contacts", None)
        return True

    def last_message_times(self):
        """{pubkey: timestamp of the latest direct message, either way}."""
        return {pk: msgs[-1].get("ts", 0) for pk, msgs in self._dm_messages.items() if msgs}

    def contact_sort(self):
        """How the contact lists are sorted: heard, name, nearest or message."""
        try:
            from mpos import SharedPreferences
            v = SharedPreferences(NICKNAME_PREFS).get_string("contact_sort", "heard")
        except Exception:
            v = "heard"
        return v if v in CONTACT_SORTS else "heard"

    def set_contact_sort(self, key):
        if key not in CONTACT_SORTS:
            return
        try:
            ed = self._editor()
            ed.put_string("contact_sort", key)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: contact sort error:", repr(e))

    def discovered_count(self):
        """Nodes heard that are not contacts."""
        return sum(1 for pk in self._nodes if pk not in self._contacts)

    def forget_node(self, pubkey_hex):
        """Drop a node from the discovered list (a contact stays a contact)."""
        if self._nodes.pop(pubkey_hex, None) is not None:
            self._nodes_dirty = True
            self._notify("node", None)

    def clear_discovered(self):
        """Drop every heard node that is not a contact."""
        for pk in [pk for pk in self._nodes if pk not in self._contacts]:
            del self._nodes[pk]
        self._nodes_dirty = True
        self._notify("node", None)

    def add_contact(self, pubkey_hex, name=None, node_type=ADV_TYPE_CHAT):
        """Add a learned companion (or explicit pubkey) to the saved contact list."""
        if not pubkey_hex or len(pubkey_hex) != 64:
            return (False, "invalid public key")
        if pubkey_hex in self._contacts:
            return (True, None)  # already a contact
        node = self._nodes.get(pubkey_hex, {})
        if not name:
            name = node.get("name") or pubkey_hex[0:2]
        self._contacts[pubkey_hex] = {
            "pubkey": pubkey_hex,
            "id": pubkey_hex[0:2],
            "name": name,
            "type": node_type,
            "type_name": {2: "rptr", 3: "room", 4: "sensor"}.get(node_type, "chat"),
            "secret": None,
            "rssi": node.get("rssi"),
            "seq": node.get("seq", 0),
            "lat": node.get("lat"),
            "lon": node.get("lon"),
            "heard_ts": node.get("heard_ts"),
        }
        self._dm_messages.setdefault(pubkey_hex, [])
        self._save_contacts()
        self._spawn_secret_precompute()   # derive the X25519 secret off the UI thread
        self._notify("contacts", None)
        return (True, None)

    def remove_contact(self, pubkey_hex, delete_chat=False):
        """No longer a contact. Its chat stays (listed under its last name, read only until
        the node is added again) unless `delete_chat`."""
        c = self._contacts.pop(pubkey_hex, None)
        if c is None:
            return False
        self._sessions.pop(pubkey_hex, None)
        self._keep_password(pubkey_hex, None)
        if delete_chat or not self._dm_messages.get(pubkey_hex):
            self.remove_chat(pubkey_hex)
        else:
            self._dm_names[pubkey_hex] = c.get("name") or pubkey_hex[:2]
            self._dirty_history.add(pubkey_hex)
        self._save_contacts()
        self._notify("contacts", None)
        return True

    def remove_chat(self, key):
        """Remove a chat: a channel is left; a DM's history goes (the contact stays)."""
        if self.get_channel(key) is not None:
            self.remove_channel(key)
            self._messages.pop(key, None)
            self._dirty_channels.add(key)
        else:
            self._dm_messages.pop(key, None)
            self._dm_names.pop(key, None)
            self._dirty_history.add(key)
        self._unread.pop(key, None)
        self._mentions.discard(key)
        self._unread_dirty = True
        if not self._worker_running:
            self._flush_dirty()
        self._notify("dm" if self.get_channel(key) is None else "message", (key, None))

    def chat_name(self, pubkey_hex):
        """The name to show for a direct chat: the contact's, the name kept with a former
        contact's chat, the node's, or the id."""
        c = self._contacts.get(pubkey_hex) or {}
        n = self._nodes.get(pubkey_hex) or {}
        return (c.get("name") or self._dm_names.get(pubkey_hex) or n.get("name")
                or pubkey_hex[:2])

    def get_dm_chats(self):
        """Direct chats: the conversations (with a contact, a room, or a former contact whose
        chat was kept) that hold messages or unread ones: [{pubkey, name, type, contact}].
        A contact without messages is in Contacts, not in Chats."""
        out = []
        for c in self._contacts.values():
            if c.get("type", 1) in (2, 4):      # repeaters and sensors are nodes, not chats
                continue
            if not self._dm_messages.get(c["pubkey"]) and not self._unread.get(c["pubkey"]):
                continue
            out.append({"pubkey": c["pubkey"], "name": c.get("name"), "type": c.get("type", 1),
                        "id": c.get("id"), "contact": True})
        for pk, msgs in self._dm_messages.items():
            if pk in self._contacts or not msgs:
                continue
            node = self._nodes.get(pk) or {}
            out.append({"pubkey": pk, "name": self._dm_names.get(pk) or node.get("name") or pk[:2],
                        "type": node.get("type", 1), "id": pk[:2], "contact": False})
        return out

    # --- contact / history persistence (SharedPreferences) ----------------- #
    def _load_contacts(self):
        try:
            from mpos import SharedPreferences
            p = SharedPreferences(NICKNAME_PREFS)
            saved = p.get_dict("contacts", {}) or {}
            legacy = p.get_dict("dm_history", None)
        except Exception as e:
            print("MeshCore: load contacts error:", repr(e))
            saved, legacy = {}, None
        histories = self._read_store(DM_HISTORY_FILE, "h")
        if legacy:
            # DM history used to live in config.json: carry it over, and drop it from there
            # on the next flush.
            for k, v in legacy.items():
                histories.setdefault(k, v)
            self._migrate_dm_history = True
            self._dirty_history.update(k for k in legacy if k in saved)
        for pub_hex, entry in saved.items():
            try:
                self._contacts[pub_hex] = {
                    "pubkey": pub_hex,
                    "id": pub_hex[0:2],
                    "name": entry.get("name") or pub_hex[0:2],
                    "type": entry.get("type", ADV_TYPE_CHAT),
                    "type_name": {2: "rptr", 3: "room", 4: "sensor"}.get(entry.get("type"), "chat"),
                    "secret": None,
                    "rssi": None,
                    "seq": 0,
                    "path": self._unhex(entry.get("path")),
                    "path_raw": entry.get("path_raw", 0),
                    "route_mode": entry.get("route_mode", "auto"),
                    "sync_since": entry.get("sync_since", 0),
                    "lat": entry.get("lat"),
                    "lon": entry.get("lon"),
                    "heard_ts": entry.get("heard_ts"),
                    "fav": bool(entry.get("fav")),
                }
                self._dm_messages[pub_hex] = self._clean_history(histories.get(pub_hex), dm=True)
            except Exception as e:
                print("MeshCore: skipping bad contact %r: %s" % (pub_hex, e))
        names = self._read_store(DM_HISTORY_FILE, "n")
        for pub_hex, msgs in histories.items():
            if pub_hex not in self._contacts and msgs and pub_hex in names:
                self._dm_messages[pub_hex] = self._clean_history(msgs, dm=True)
                self._dm_names[pub_hex] = names[pub_hex]

    def _load_nodes(self):
        """The nodes heard before the restart. Their age comes back from the time they were
        heard; without a set clock that is not known, and they count as a day old."""
        try:
            from mpos import SharedPreferences
            saved = SharedPreferences(NICKNAME_PREFS, filename=NODES_FILE).get_dict("n", {}) or {}
        except Exception:
            return
        if not isinstance(saved, dict):
            return
        now_ms = self._now_ms()
        now_s = unix_time()
        clock_ok = now_s >= CLOCK_VALID_AFTER
        for pk, entry in saved.items():
            try:
                if len(pk) != 64 or not isinstance(entry, dict):
                    continue
                node = {k: entry[k] for k in _NODE_STORED if k in entry}
                node["pubkey"] = pk
                node["id"] = pk[0:2]
                node["type_name"] = ADV_TYPE_NAMES.get(node.get("type"), "?")
                age_s = 86400
                if clock_ok and node.get("heard_ts"):
                    age_s = max(0, now_s - node["heard_ts"])
                node["heard_ms"] = tadd(now_ms, -min(age_s, 5 * 86400) * 1000)
                self._nodes[pk] = node
                self._seq = max(self._seq, node.get("seq", 0))
            except Exception as e:
                print("MeshCore: skipping stored node %r: %s" % (pk, e))
        while len(self._nodes) > MAX_NODES:
            self._evict_oldest_node()

    def _save_contacts(self):
        try:
            from mpos import SharedPreferences
            data = {}
            for h, c in self._contacts.items():
                entry = {"name": c["name"], "type": c.get("type", ADV_TYPE_CHAT)}
                if c.get("sync_since"):                   # room: newest post we have
                    entry["sync_since"] = c["sync_since"]
                for k in ("lat", "lon", "heard_ts"):
                    if c.get(k) is not None:
                        entry[k] = c[k]
                path = c.get("path")
                if path:                                  # the learned or typed route
                    entry["path"] = path.hex()
                    entry["path_raw"] = c.get("path_raw") or len(path)
                if c.get("route_mode", "auto") != "auto":
                    entry["route_mode"] = c["route_mode"]
                if c.get("fav"):
                    entry["fav"] = True
                data[h] = entry
            ed = self._editor()
            ed.put_dict("contacts", data)
            self._commit(ed)
        except Exception as e:
            print("MeshCore: save contacts error:", repr(e))

    def _save_history(self, pubkey_hex):
        """Mark a contact's DM history dirty; the worker coalesces the flash write (see
        _flush_due). Writes immediately if there is no worker to coalesce."""
        if pubkey_hex not in self._contacts:
            return  # only contacts' history is stored
        self._dirty_history.add(pubkey_hex)
        if not self._worker_running:
            self._flush_dirty()

    def _delete_history(self, pubkey_hex):
        self._dirty_history.add(pubkey_hex)      # no longer a contact -> dropped on flush
        if not self._worker_running:
            self._flush_dirty()

    def _mentions_us(self, text):
        nick = self._nick or self.default_nickname()
        return bool(nick) and ("@[%s]" % nick) in (text or "")

    @staticmethod
    def _read_store(filename, key):
        """The dict under `key` in a history file, or {} when it is missing or unreadable."""
        try:
            from mpos import SharedPreferences
            d = SharedPreferences(NICKNAME_PREFS, filename=filename).get_dict(key, {})
            return d if isinstance(d, dict) else {}
        except Exception as e:
            print("MeshCore: %s read error: %r" % (filename, e))
            return {}

    @staticmethod
    def _clean_history(lst, dm):
        """Stored messages that are well-formed. An outgoing message that was still in
        flight when the device went down is offered for resend: no retry survives a reboot."""
        out = []
        if not isinstance(lst, list):
            return out
        for m in lst:
            if not isinstance(m, dict) or not isinstance(m.get("text"), str):
                continue
            m = dict(m)
            if not m.get("incoming"):
                if dm and not m.get("delivered") and not m.get("failed"):
                    m["failed"] = True
                elif not dm and not m.get("heard"):
                    m["unheard"] = True
            out.append(m)
        return out[-MAX_MESSAGES:]

    def _load_channel_history(self):
        stored = self._read_store(CH_HISTORY_FILE, "ch")
        for ch in self._channels:
            msgs = self._clean_history(stored.get(ch.name), dm=False)
            if msgs:
                self._messages[ch.name] = msgs

    def _load_unread(self):
        try:
            from mpos import SharedPreferences
            p = SharedPreferences(NICKNAME_PREFS, filename=UNREAD_FILE)
            counts = p.get_dict("u", {})
            mentions = p.get_list("m", [])
        except Exception as e:
            print("MeshCore: unread read error:", repr(e))
            return
        if isinstance(counts, dict):
            for k, v in counts.items():
                if isinstance(v, int) and v > 0:
                    self._unread[k] = v
        if isinstance(mentions, list):
            self._mentions = set(k for k in mentions if isinstance(k, str))

    def _flush_due(self):
        """Worker idle hook: write pending history at most every FLUSH_EVERY_MS."""
        if not (self._dirty_history or self._dirty_channels or self._unread_dirty):
            return
        now = self._now_ms()
        if self._last_flush_ms is not None and tdiff(now, self._last_flush_ms) < FLUSH_EVERY_MS:
            return
        self._flush_dirty()

    def _flush_dirty(self, force=False):
        """Persist DM history, channel history, unread state, contacts and heard nodes that
        changed (heard nodes at most every NODES_FLUSH_MS unless forced)."""
        self._last_flush_ms = self._now_ms()
        try:
            from mpos import SharedPreferences
        except Exception:
            return
        if getattr(self, "_contacts_dirty", False):
            self._contacts_dirty = False
            self._save_contacts()
        last = getattr(self, "_nodes_flush_ms", None)
        if getattr(self, "_nodes_dirty", False) and (
                force or last is None or tdiff(self._last_flush_ms, last) >= NODES_FLUSH_MS):
            self._nodes_dirty = False
            self._nodes_flush_ms = self._last_flush_ms
            try:
                data = {}
                for pk, n in self._nodes.items():
                    data[pk] = {k: n[k] for k in _NODE_STORED if n.get(k) is not None}
                ed = self._editor(NODES_FILE)
                ed.put_dict("n", data)
                self._commit(ed)
            except Exception as e:
                self._nodes_dirty = True
                print("MeshCore: save nodes error:", repr(e))
        if self._dirty_history or getattr(self, "_migrate_dm_history", False):
            pending = set(self._dirty_history)
            self._dirty_history.clear()
            try:
                data = {k: v for k, v in self._dm_messages.items()
                        if v and (k in self._contacts or k in self._dm_names)}
                ed = self._editor(DM_HISTORY_FILE)
                ed.put_dict("h", data)
                ed.put_dict("n", {k: v for k, v in self._dm_names.items() if k in data})
                self._commit(ed)
                if getattr(self, "_migrate_dm_history", False):
                    ed = self._editor()
                    ed.put_dict("dm_history", {})     # moved to its own file
                    self._commit(ed)
                    self._migrate_dm_history = False
            except Exception as e:
                self._dirty_history.update(pending)      # try again on the next flush
                print("MeshCore: save DM history error:", repr(e))
        if self._dirty_channels:
            pending = set(self._dirty_channels)
            self._dirty_channels.clear()
            try:
                data = {}
                for ch in self._channels:
                    msgs = self._messages.get(ch.name) or []
                    if msgs:
                        data[ch.name] = [{k: m.get(k) for k in _CH_STORED if k in m}
                                         for m in msgs[-CH_HISTORY_CAP:]]
                ed = self._editor(CH_HISTORY_FILE)
                ed.put_dict("ch", data)
                self._commit(ed)
            except Exception as e:
                self._dirty_channels.update(pending)
                print("MeshCore: save channel history error:", repr(e))
        if self._unread_dirty:
            self._unread_dirty = False
            try:
                ed = self._editor(UNREAD_FILE)
                ed.put_dict("u", dict(self._unread))
                ed.put_list("m", sorted(self._mentions))
                self._commit(ed)
            except Exception as e:
                self._unread_dirty = True
                print("MeshCore: save unread error:", repr(e))

    def _post_dm_notification(self, pubkey_hex, name, msg):
        """Notify for an incoming DM unless the app is foreground (mirrors channels)."""
        try:
            from mpos import Notification, NotificationManager, get_foreground_app
        except Exception:
            return
        try:
            if get_foreground_app() == MESHCORE_APP:
                return
            intent = None
            try:
                from mpos import Intent
                from thread_activity import DMChatActivity
                intent = Intent(activity_class=DMChatActivity, extras={"pubkey": pubkey_hex})
            except Exception:
                intent = None
            text = msg.get("text", "")
            if len(text) > 80:
                text = text[:77] + "..."
            NotificationManager.notify(Notification(
                notification_id="meshcore-dm:%s" % pubkey_hex,
                title="DM %s" % name,
                text=text,
                intent=intent,
                app_fullname=MESHCORE_APP,
            ))
        except Exception as e:
            print("MeshCore: dm notify error:", repr(e))

    def _post_notification(self, channel_name, msg):
        """Raise a system notification for an incoming message, unless the MeshCore app
        is currently in the foreground.  This is what surfaces messages received in the
        background (app closed / on the home screen) so they aren't missed."""
        try:
            from mpos import Notification, NotificationManager, get_foreground_app
        except Exception as e:
            print("MeshCore: notifications unavailable:", repr(e))
            return
        try:
            if get_foreground_app() == MESHCORE_APP:
                return  # user is already in the app; the chat view updates live
            # Best-effort tap-to-open the channel; falls back to no intent if the UI
            # module isn't importable yet (e.g. app never opened this session).
            intent = None
            try:
                from mpos import Intent
                from thread_activity import ChannelChatActivity
                intent = Intent(activity_class=ChannelChatActivity,
                                extras={"channel": channel_name})
            except Exception:
                intent = None
            text = "%s: %s" % (msg.get("sender", "?"), msg.get("text", ""))
            if len(text) > 80:
                text = text[:77] + "..."
            NotificationManager.notify(Notification(
                notification_id="meshcore:%s" % channel_name,
                title="# %s" % channel_name,
                text=text,
                intent=intent,
                app_fullname=MESHCORE_APP,
            ))
        except Exception as e:
            print("MeshCore: notify error:", repr(e))

    def _log(self, record):
        self._packets.insert(0, record)
        if len(self._packets) > MAX_PACKETS:
            self._packets.pop()
        self._notify("packet", record)

    def _add_message(self, channel_name, msg):
        lst = self._messages.setdefault(channel_name, [])
        lst.append(msg)
        if len(lst) > MAX_MESSAGES:
            del lst[0]

    # --- send --------------------------------------------------------------- #
    def send_group_text(self, channel_name, text):
        ch = self.get_channel(channel_name)
        if ch is None or not text:
            return False
        try:
            import time
            ts = self._timestamp()
        except Exception:
            ts = 0
        # reflect our own message locally right away (the actual TX happens shortly)
        msg = {"ts": ts, "sender": self.nickname(), "text": text, "rssi": None,
               "incoming": False, "tx": False, "heard": 0, "unheard": False}
        rec = {"kind": "ch", "channel": channel_name, "ch": ch, "msg": msg, "text": text,
               "ts": ts, "attempt": 0}
        self._tx_group(rec)
        self._add_message(channel_name, msg)
        self._retries.append(rec)          # marked unheard by _retry_tick if nothing echoes it
        self._notify("message", (channel_name, msg))
        return True

    def _tx_group(self, rec):
        """Build and queue a channel message. We remember our own packet hash: hearing it
        come back means a repeater re-flooded it, which is the only delivery proof a channel
        offers."""
        payload = encode_group_text(rec["ch"], self.nickname(), rec["text"], rec["ts"])
        pkt = MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                             encode_path_len(0), b"", payload)
        try:
            h = pkt.packet_hash()
            self._remember(h)
            self._remember_own(h, rec["channel"], rec["msg"])
        except Exception:
            pass
        rec["sent_ms"] = None
        self._enqueue_tx(pkt.to_bytes(), on_sent=lambda ok: self._on_sent(rec, ok),
                         channel=rec["channel"])

    def _on_sent(self, rec, ok):
        """A queued message went on the air (or the radio refused it)."""
        rec["sent_ms"] = self._now_ms()    # the retry clock runs even if the TX failed
        if not ok:
            return
        msg = rec["msg"]
        if not msg.get("tx"):
            msg["tx"] = True
            if rec["kind"] == "ch":
                self._notify("message", (rec["channel"], msg))
            else:
                self._notify("dm", (rec["pubkey"], msg))

    def _thread(self, key):
        """(the message list, its event name) of a channel name or a contact pubkey."""
        if key in self._dm_messages or key in self._contacts:
            return self._dm_messages.setdefault(key, []), "dm"
        return self._messages.setdefault(key, []), "message"

    def delete_message(self, key, msg):
        """Remove one message from a chat (by identity). False when it is not there."""
        msgs, event = self._thread(key)
        for i, m in enumerate(msgs):
            if m is msg:
                del msgs[i]
                self._notify(event, (key, None))
                return True
        return False

    def clear_history(self, key):
        """Remove every message of a chat."""
        msgs, event = self._thread(key)
        del msgs[:]
        self._notify(event, (key, None))

    def resend(self, key, msg):
        """Send a failed DM or an unheard channel message again, as a new message (fresh
        timestamp, attempt 0) that replaces the old one. Returns False for anything still
        in flight or not ours."""
        if msg.get("incoming"):
            return False
        lst = self._messages.get(key)
        is_channel = lst is not None and self.get_channel(key) is not None
        if not is_channel:
            lst = self._dm_messages.get(key)
        if lst is None or not any(m is msg for m in lst):
            return False
        if not (msg.get("failed") or msg.get("unheard")):
            return False
        self._cancel_retry(msg)
        for i, m in enumerate(lst):
            if m is msg:
                del lst[i]
                break
        if is_channel:
            return self.send_group_text(key, msg["text"])
        ok, _ = self.send_dm(key, msg["text"])
        return ok

    def _time_on_air_ms(self, payload_len):
        """LoRa time on air (ms) of a packet on the active preset."""
        return meshcore_presets.airtime_ms(self.radio_preset(), payload_len)

    def _transmit(self, raw):
        if simulation_mode:
            print("MeshCoreManager: SIM TX %s" % raw.hex())
            return True
        if not self._radio_ready or self._radio is None:
            # Guard: send() needs the radio in non-blocking mode (setBlockingCallback ran);
            # transmitting before bring-up completes fails with "no attribute 'blocking'".
            print("MeshCoreManager: cannot TX, radio not ready")
            return False
        import time
        ok = False
        self._radio_lock.acquire()
        try:
            _, result = self._radio.send(raw)   # non-blocking: returns immediately
            print("MeshCoreManager: TX result %s"
                  % meshcore_radio.status_name(self._radio, result))
            # A driver whose send() returns before the packet is out needs the airtime waited
            # out before RX is re-armed; the polled driver waits for TX_DONE itself.
            if not getattr(self._radio, "blocking_send", False):
                time.sleep_ms(self._time_on_air_ms(len(raw)) + 120)
            ok = (result == 0)
            if ok:
                self._last_tx_ms = self._now_ms()
                self._tx_count += 1
                self._log_stat(self._tx_log, (self._last_tx_ms, self._time_on_air_ms(len(raw))))
        except Exception as e:
            print("MeshCoreManager: TX exception:", repr(e))
        finally:
            # Clear any latched IRQ, then re-arm receive.
            try:
                self._radio.clearIrqStatus()
            except Exception:
                pass
            try:
                self._radio.startReceive()
            except Exception:
                pass
            self._radio_lock.release()
        return ok

    # --- accessors (UI) ----------------------------------------------------- #
    def get_nodes(self):
        # most-recently-heard first
        return sorted(self._nodes.values(), key=lambda n: n.get("seq", 0), reverse=True)

    def get_node(self, pubkey_hex):
        return self._nodes.get(pubkey_hex)

    def get_channels(self):
        return list(self._channels)

    def get_channel_names(self):
        return [c.name for c in self._channels]

    def get_channel(self, name):
        for c in self._channels:
            if c.name == name:
                return c
        return None

    def get_messages(self, channel_name):
        """The channel's messages, without those of blocked sender names."""
        names = self._block_store()["names"]
        msgs = self._messages.get(channel_name, [])
        if not names:
            return list(msgs)
        return [x for x in msgs if not (x.get("incoming") and x.get("sender") in names)]

    def get_packets(self):
        return list(self._packets)

    def clear(self):
        self._packets = []
        self._count = 0

    # --- subscribers -------------------------------------------------------- #
    def add_subscriber(self, cb):
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def remove_subscriber(self, cb):
        try:
            self._subscribers.remove(cb)
        except ValueError:
            pass

    def _notify(self, event, data):
        # Every change to a message is announced, so this is where history goes dirty.
        if event == "message":
            self._dirty_channels.add(data[0])
        elif event == "dm" and (data[0] in self._contacts or data[0] in self._dm_names):
            self._dirty_history.add(data[0])
        for cb in list(self._subscribers):
            try:
                cb(event, data)
            except Exception as e:
                print("MeshCoreManager: subscriber error:", repr(e))

    # --- simulation (desktop) ---------------------------------------------- #
    def _start_simulation(self):
        if self._sim_started:
            return
        self._sim_started = True
        try:
            import _thread
            _thread.start_new_thread(self._sim_loop, ())
        except Exception:
            self._sim_feed()

    def _sim_loop(self):
        import time
        time.sleep(1)
        self._sim_feed()

    def _sim_feed(self):
        import struct
        # a chat node advert + a repeater advert + a public message
        def advert(pk0, flags, name):
            pk = bytes([pk0]) + bytes(range(31))
            return pk + struct.pack("<I", 0x662d5a10) + b"\x00" * 64 + bytes([flags]) + name
        from meshcore_advert import ADV_TYPE_CHAT, ADV_TYPE_REPEATER, ADV_NAME_MASK
        self._ingest(MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_ADVERT),
                                    encode_path_len(0), b"",
                                    advert(0xa1, ADV_TYPE_CHAT | ADV_NAME_MASK, b"SimChat")).to_bytes(),
                     rssi=-80, snr=8.0)
        self._ingest(MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_ADVERT),
                                    encode_path_len(0), b"",
                                    advert(0xb2, ADV_TYPE_REPEATER | ADV_NAME_MASK, b"RoofRepeater")).to_bytes(),
                     rssi=-95, snr=5.5)
        grp = encode_group_text(PUBLIC_CHANNEL, "SimChat", "hello from sim", 0x662d5a10)
        self._ingest(MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                                    encode_path_len(0), b"", grp).to_bytes(),
                     rssi=-80, snr=8.0)
