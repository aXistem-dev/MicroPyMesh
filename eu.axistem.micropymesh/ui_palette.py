"""The app's colours. By default they follow MicroPythonOS: its light or dark mode and its
primary colour as the accent. The app's Appearance setting can pin either one."""

DARK_TEXT = 0x10151B           # text on a light accent
LIGHT_TEXT = 0xFFFFFF          # text on a dark accent
DESIGN_ACCENT = 0xF4A93B       # when the OS gives none

DARK = {
    "BG": 0x10151B,            # app background
    "BAR": 0x0D1217,           # tab bar, composer bar
    "SURFACE": 0x19212A,       # chips, pills, cards, incoming bubbles, inputs
    "SURFACE2": 0x222C37,      # pressed state
    "LINE": 0x222C37,          # dividers
    "OUTLINE": 0x2C3846,       # outlines, secondary buttons
    "TEXT": 0xE9EEF3,
    "MUTED": 0xA3B0BD,
    "OWN": 0x1E4B78,           # outgoing bubble
    "OWN_TEXT": 0xF2F7FC,
    "CHAN_BG": 0x1F3A3F, "CHAN_FG": 0x8FD3DA,
    "DM_BG": 0x3A2E52, "DM_FG": 0xCDB8F5,
    "ROOM_BG": 0x2B3542,
    "DELIVERED": 0x6CB4FF,     # delivered check
    "FAIL": 0xFF9F7A,          # failed bubble border
    "FAIL_TEXT": 0xFFB89C,     # failed meta line
    "OK": 0x9BE0A8,            # fresh age
    "WARN": 0xF2C98A,          # older age, unheard
    "KEYBOARD": 0x0A0E13,
    "PIN_RPTR": 0x6CB4FF, "PIN_ROOM": 0x9BE0A8,
    "SENDER_COLORS": (0x8FD3DA, 0xCDB8F5, 0xF2C98A, 0x9BE0A8, 0x6CB4FF, 0xFFB89C),
}

LIGHT = {
    "BG": 0xF4F6F8,
    "BAR": 0xFFFFFF,
    "SURFACE": 0xE7EBF0,
    "SURFACE2": 0xD8DEE5,
    "LINE": 0xDCE2E8,
    "OUTLINE": 0xC3CCD6,
    "TEXT": 0x16202A,
    "MUTED": 0x56636F,
    "OWN": 0xCFE3F8,
    "OWN_TEXT": 0x0F2A44,
    "CHAN_BG": 0xD3EEF0, "CHAN_FG": 0x14666F,
    "DM_BG": 0xE6DDF8, "DM_FG": 0x56399A,
    "ROOM_BG": 0xDCE2E9,
    "DELIVERED": 0x1A6FC2,
    "FAIL": 0xD9534F,
    "FAIL_TEXT": 0xB23F1E,
    "OK": 0x1F7A43,
    "WARN": 0x8F5B00,
    "KEYBOARD": 0xD5DBE1,
    "PIN_RPTR": 0x1A6FC2, "PIN_ROOM": 0x1F7A43,
    "SENDER_COLORS": (0x14666F, 0x56399A, 0x8F5B00, 0x1F7A43, 0x1A6FC2, 0xB23F1E),
}

THEMES = (("System", "system"), ("Light", "light"), ("Dark", "dark"))
ACCENTS = (("System", "system"), ("Amber", "F4A93B"), ("Blue", "3B82F6"), ("Teal", "14B8A6"),
           ("Green", "22C55E"), ("Red", "EF4444"), ("Purple", "A855F7"), ("Pink", "EC4899"))


def on_color(c):
    """Dark or light text, whichever reads better on colour `c` (relative luminance)."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(c >> 16 & 0xFF) + 0.7152 * lin(c >> 8 & 0xFF) + 0.0722 * lin(c & 0xFF)
    return DARK_TEXT if lum > 0.18 else LIGHT_TEXT


def resolve(theme="system", accent="system", os_light=False, os_accent=None):
    """The colours to use: the OS's mode and accent, unless theme or accent pin their own."""
    light = os_light if theme == "system" else theme == "light"
    p = dict(LIGHT if light else DARK)
    a = None
    if accent != "system":
        try:
            a = int(accent, 16)
        except (TypeError, ValueError):
            a = None
    if a is None:
        a = os_accent if os_accent is not None else DESIGN_ACCENT
    p["ACCENT"] = a
    p["ON_ACCENT"] = on_color(a)
    p["LIGHT"] = light
    return p
