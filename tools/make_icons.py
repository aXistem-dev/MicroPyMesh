"""Rasterise the UI's line icons to small white PNGs (alpha only); the app tints them with
LVGL's image recolour. Needs cairosvg.

    python3 tools/make_icons.py
"""
import os

import cairosvg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "eu.axistem.micropymesh", "icons")

S = 'fill="none" stroke="#fff" stroke-linecap="round" stroke-linejoin="round"'
ICONS = {
    # name: (px, stroke width, svg body)
    "plus": (24, 2, '<path d="M12 5v14M5 12h14"/>'),
    "chat": (22, 2, '<path d="M4 5h16v11H8l-4 4z"/>'),
    "nodes": (22, 2, '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/>'
                     '<circle cx="11" cy="18" r="2.5"/><path d="M8 7l7.6 0.8M7.2 8.2l2.8 7.6'
                     'M16.6 10.2l-4 5.8"/>'),
    "radio": (22, 2, '<path d="M12 13v8"/><circle cx="12" cy="11" r="2"/><path d="M7.8 6.8a6 6 0 0 0 0 8.4'
                     'M16.2 6.8a6 6 0 0 1 0 8.4M4.9 3.9a10 10 0 0 0 0 14.2M19.1 3.9a10 10 0 0 1 0 14.2"/>'),
    "settings": (22, 2, '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3'
                        'M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>'),
    "back": (24, 2, '<path d="M15 5l-7 7 7 7"/>'),
    "send": (22, 2.2, '<path d="M5 12h13M12 5l7 7-7 7"/>'),
    "chevron_down": (12, 2.5, '<path d="M6 9l6 6 6-6"/>'),
    "chevron_right": (18, 2, '<path d="M9 5l7 7-7 7"/>'),
    "check": (14, 3, '<path d="M4 12l5 5L20 6"/>'),
    "retry": (14, 2.5, '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>'),
    "search": (22, 2, '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>'),
    "advert": (18, 2, '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4'
                      'M16.2 7.8a6 6 0 0 1 0 8.4"/>'),
    "star": (18, 2, '<path d="M12 3l2.8 5.8 6.2.9-4.5 4.4 1 6.2L12 17.4l-5.5 2.9 1-6.2L3 9.7l6.2-.9z"/>'),
    "lock": (18, 2, '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
    "close": (18, 2, '<path d="M6 6l12 12M18 6L6 18"/>'),
    "map": (22, 2, '<path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14"/>'),
    "minus": (24, 2, '<path d="M5 12h14"/>'),
    "fit": (22, 2, '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><circle cx="12" cy="12" r="2.5"/>'),
    "me": (22, 2, '<circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="1.5" fill="#fff"/>'
                  '<path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>'),
    "person_add": (22, 2, '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6'
                          'M18 8v6M15 11h6"/>'),
    "kebab": (22, 0, '<g fill="#fff" stroke="none"><circle cx="12" cy="5" r="2"/>'
                     '<circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></g>'),
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (px, width, body) in sorted(ICONS.items()):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 24 24" '
               '%s stroke-width="%s">%s</svg>' % (px, px, S, width, body))
        path = os.path.join(OUT, name + ".png")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=path)
        print("%-14s %2d px  %4d bytes" % (name, px, os.path.getsize(path)))


if __name__ == "__main__":
    main()
