"""Radio presets, the firmware preamble rule and LoRa airtime.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_presets.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

APP = "eu.axistem.micropymesh"


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _presets():
    fake_mpos.install()
    import meshcore_presets
    return meshcore_presets


# The presets the MeshCore apps offer (as listed by the RRY map bot), in that order.
OFFICIAL = [
    ("au", "Australia", 915.800, 250, 10, 5),
    ("au-vic", "Australia: Victoria", 916.675, 62.5, 7, 8),
    ("eu-narrow", "EU/UK (Narrow)", 869.618, 62.5, 8, 8),
    ("eu-long", "EU/UK (Long Range)", 869.525, 250, 11, 5),
    ("eu-medium", "EU/UK (Medium Range)", 869.525, 250, 10, 5),
    ("cz-narrow", "Czech Republic (Narrow)", 869.525, 62.5, 7, 5),
    ("eu-433-long", "EU 433MHz (Long Range)", 433.650, 250, 11, 5),
    ("nz", "New Zealand", 917.375, 250, 11, 5),
    ("nz-narrow", "New Zealand (Narrow)", 917.375, 62.5, 7, 5),
    ("pt-433", "Portugal 433", 433.375, 62.5, 9, 6),
    ("pt-868", "Portugal 868", 869.618, 62.5, 7, 6),
    ("us-ca", "USA/Canada (Recommended)", 910.525, 62.5, 7, 5),
    ("vn", "Vietnam", 920.250, 250, 11, 5),
]


def test_presets_are_the_official_list():
    p = _presets()
    got = [(x["id"], x["name"], x["freq"], x["bw"], x["sf"], x["cr"]) for x in p.PRESETS]
    _assert(got == OFFICIAL, got)
    _assert(p.DEFAULT_PRESET == "eu-narrow")


def test_short_names_for_the_header_pill():
    p = _presets()
    _assert(p.by_id("eu-narrow")["short"] == "EU Narrow")
    _assert(all(len(x["short"]) <= 12 for x in p.PRESETS), [x["short"] for x in p.PRESETS])
    _assert(p.short_name({"id": "custom", "freq": 868.1, "bw": 125, "sf": 9, "cr": 5}) == "868.100")


def test_preamble_rule():
    p = _presets()
    _assert(p.preamble_for(8) == 32)
    _assert(p.preamble_for(7) == 32)
    _assert(p.preamble_for(11) == 16)


def test_airtime_eu_narrow_40_bytes():
    p = _presets()
    eu = p.by_id("eu-narrow")
    _assert(p.airtime_ms(eu, 40) == 542, p.airtime_ms(eu, 40))


def test_airtime_eu_legacy():
    p = _presets()
    long_range = p.by_id("eu-long")
    _assert(p.airtime_ms(long_range, 40) == 559, p.airtime_ms(long_range, 40))


def test_airtime_uses_ldro_for_slow_symbols():
    # SF12 at 125 kHz: 32.768 ms symbols, so low-data-rate optimisation is on (DE=1).
    p = _presets()
    custom = {"id": "custom", "freq": 869.525, "bw": 125, "sf": 12, "cr": 5}
    _assert(p.airtime_ms(custom, 40) == 2236, p.airtime_ms(custom, 40))


def test_describe_eu_narrow():
    p = _presets()
    _assert(p.describe(p.by_id("eu-narrow")) == "869.618 MHz · 62.5 kHz · SF8 · CR 4/8",
            p.describe(p.by_id("eu-narrow")))


def test_radio_kwargs_match_the_driver():
    p = _presets()
    kw = p.radio_kwargs(p.by_id("eu-long"))
    _assert(kw["preambleLength"] == 16)
    _assert(kw["syncWord"] == 0x12)
    _assert(kw["power"] == 22)
    # The driver looks the bandwidth up by str(bw): "250", never "250.0".
    _assert(str(kw["bw"]) == "250", kw["bw"])
    _assert(str(p.radio_kwargs(p.by_id("eu-narrow"))["bw"]) == "62.5")


def test_resolve_falls_back_to_default():
    p = _presets()
    _assert(p.resolve({"id": "nope"})["id"] == "eu-narrow")
    _assert(p.resolve(None)["id"] == "eu-narrow")
    _assert(p.resolve({"id": "pt-433"})["freq"] == 433.375)
    custom = {"id": "custom", "freq": 868.0, "bw": 125, "sf": 9, "cr": 5}
    _assert(p.resolve(custom) == custom)


def test_manager_default_preset_is_eu_narrow():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    _assert(m.radio_preset()["id"] == "eu-narrow")


def test_set_preset_persists():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.set_radio_preset("pt-433")
    _assert(env.prefs(APP).get("radio") == {"id": "pt-433"}, env.prefs(APP))
    _assert(m.radio_preset()["freq"] == 433.375)


def test_bring_up_uses_the_stored_preset():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.set_radio_preset("cz-narrow")
    m._radio_ready = False
    _assert(m._bring_up_radio() is True)
    _assert(m.chip.cfg["freq_khz"] == 869525, m.chip.cfg)
    _assert(m.chip.cfg["preamble_len"] == 32, m.chip.cfg)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
