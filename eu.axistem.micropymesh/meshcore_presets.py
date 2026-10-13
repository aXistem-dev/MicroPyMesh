"""Radio presets (region / mesh profiles) and LoRa airtime.

Hardware-independent and unit-testable on the desktop. Everyone who wants to talk must use
the same frequency, bandwidth, spreading factor and coding rate; the preamble length and
sync word follow from MeshCore's firmware (RadioLibWrappers.h: 32 preamble symbols for
SF <= 8, else 16; private sync word 0x12).
"""

# The presets the MeshCore apps offer (names and values as the RRY map bot lists them).
PRESETS = [
    {"id": "au", "short": "AU", "name": "Australia", "freq": 915.800, "bw": 250, "sf": 10, "cr": 5},
    {"id": "au-vic", "short": "AU Victoria", "name": "Australia: Victoria", "freq": 916.675, "bw": 62.5, "sf": 7, "cr": 8},
    {"id": "eu-narrow", "short": "EU Narrow", "name": "EU/UK (Narrow)", "freq": 869.618, "bw": 62.5, "sf": 8, "cr": 8},
    {"id": "eu-long", "short": "EU Long", "name": "EU/UK (Long Range)", "freq": 869.525, "bw": 250, "sf": 11, "cr": 5},
    {"id": "eu-medium", "short": "EU Medium", "name": "EU/UK (Medium Range)", "freq": 869.525, "bw": 250, "sf": 10, "cr": 5},
    {"id": "cz-narrow", "short": "CZ Narrow", "name": "Czech Republic (Narrow)", "freq": 869.525, "bw": 62.5, "sf": 7, "cr": 5},
    {"id": "eu-433-long", "short": "EU 433 Long", "name": "EU 433MHz (Long Range)", "freq": 433.650, "bw": 250, "sf": 11, "cr": 5},
    {"id": "nz", "short": "NZ", "name": "New Zealand", "freq": 917.375, "bw": 250, "sf": 11, "cr": 5},
    {"id": "nz-narrow", "short": "NZ Narrow", "name": "New Zealand (Narrow)", "freq": 917.375, "bw": 62.5, "sf": 7, "cr": 5},
    {"id": "pt-433", "short": "PT 433", "name": "Portugal 433", "freq": 433.375, "bw": 62.5, "sf": 9, "cr": 6},
    {"id": "pt-868", "short": "PT 868", "name": "Portugal 868", "freq": 869.618, "bw": 62.5, "sf": 7, "cr": 6},
    {"id": "us-ca", "short": "US/CA", "name": "USA/Canada (Recommended)", "freq": 910.525, "bw": 62.5, "sf": 7, "cr": 5},
    {"id": "vn", "short": "VN", "name": "Vietnam", "freq": 920.250, "bw": 250, "sf": 11, "cr": 5},
]
DEFAULT_PRESET = "eu-narrow"
SYNC_WORD = 0x12
DEFAULT_POWER = 22


def short_name(p):
    """A few characters for the Chats header pill: the preset's short name, or the
    frequency for a custom setting."""
    return p.get("short") or ("%.3f" % float(p["freq"]))


def preamble_for(sf):
    return 32 if sf <= 8 else 16


def by_id(preset_id):
    for p in PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def resolve(stored):
    """The preset a stored `radio` pref stands for: a known id, a complete custom dict,
    or the default when it is missing or unreadable."""
    if isinstance(stored, dict):
        p = by_id(stored.get("id"))
        if p is not None:
            return p
        if all(k in stored for k in ("freq", "bw", "sf", "cr")):
            return stored
    return by_id(DEFAULT_PRESET)


def _bw_value(bw):
    # The driver looks bandwidths up by str(bw) ("62.5", "125", "250"), so whole numbers
    # must be ints.
    return int(bw) if float(bw) == int(bw) else float(bw)


def radio_kwargs(p, power=DEFAULT_POWER):
    """begin() keywords for a preset."""
    return dict(
        freq=float(p["freq"]), bw=_bw_value(p["bw"]), sf=int(p["sf"]), cr=int(p["cr"]),
        syncWord=SYNC_WORD, preambleLength=preamble_for(int(p["sf"])),
        implicit=False, crcOn=True, power=power,
    )


def airtime_ms(p, payload_len):
    """LoRa time on air in ms (Semtech formula: explicit header, CRC on, low-data-rate
    optimisation when a symbol lasts 16 ms or more)."""
    sf = int(p["sf"])
    bw_hz = float(p["bw"]) * 1000
    cr = int(p["cr"]) - 4                 # 4/(4+cr)
    t_sym = (1 << sf) / bw_hz
    de = 1 if t_sym >= 0.016 else 0
    num = 8 * payload_len - 4 * sf + 28 + 16
    den = 4 * (sf - 2 * de)
    n_payload = 8 + max(((num + den - 1) // den) * (cr + 4), 0)
    t = (preamble_for(sf) + 4.25) * t_sym + n_payload * t_sym
    return int(t * 1000 + 0.5)


def _num(v):
    s = ("%.3f" % v).rstrip("0").rstrip(".")
    return s


def describe(p):
    return "%s MHz · %s kHz · SF%d · CR 4/%d" % (
        "%.3f" % float(p["freq"]), _num(float(p["bw"])), int(p["sf"]), int(p["cr"]))
