"""Colours of the app: by default MicroPythonOS's light or dark mode and its primary colour;
the app's own setting can pin either. Text on accent fills stays readable whatever the accent.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_ui_palette.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402,F401

import ui_palette as P  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def test_follows_the_os_by_default():
    light = P.resolve(os_light=True, os_accent=0x2196F3)
    dark = P.resolve(os_light=False, os_accent=0x2196F3)
    _assert(light["BG"] == P.LIGHT["BG"] and dark["BG"] == P.DARK["BG"], (light["BG"], dark["BG"]))
    _assert(light["ACCENT"] == 0x2196F3 and dark["ACCENT"] == 0x2196F3, "os accent")


def test_the_app_can_pin_the_theme_and_the_accent():
    p = P.resolve("dark", "A855F7", os_light=True, os_accent=0x2196F3)
    _assert(p["BG"] == P.DARK["BG"] and p["ACCENT"] == 0xA855F7, p)
    p = P.resolve("light", "system", os_light=False, os_accent=0x2196F3)
    _assert(p["BG"] == P.LIGHT["BG"] and p["ACCENT"] == 0x2196F3, p)


def test_without_an_os_accent_the_design_accent_is_used():
    _assert(P.resolve(os_light=False, os_accent=None)["ACCENT"] == P.DESIGN_ACCENT, "fallback")


def test_a_bad_saved_accent_falls_back_to_the_os():
    _assert(P.resolve("system", "zz", os_light=False, os_accent=0x123456)["ACCENT"] == 0x123456, "bad")


def test_text_on_the_accent_is_readable():
    _assert(P.resolve(os_light=False, os_accent=0xF4A93B)["ON_ACCENT"] == P.DARK_TEXT, "amber")
    _assert(P.resolve(os_light=True, os_accent=0x1E3A8A)["ON_ACCENT"] == P.LIGHT_TEXT, "navy")
    _assert(P.resolve(os_light=True, os_accent=0xFFEB3B)["ON_ACCENT"] == P.DARK_TEXT, "yellow")


def test_both_palettes_have_the_same_colours():
    _assert(set(P.LIGHT) == set(P.DARK), set(P.LIGHT) ^ set(P.DARK))
    _assert(len(P.LIGHT["SENDER_COLORS"]) == len(P.DARK["SENDER_COLORS"]), "senders")


def test_accent_choices_start_with_system():
    _assert(P.ACCENTS[0] == ("System", "system"), P.ACCENTS[0])
    for name, value in P.ACCENTS[1:]:
        _assert(len(value) == 6 and int(value, 16) >= 0, (name, value))


if __name__ == "__main__":
    fake_mpos.run_all(globals())
