# MicroPy Mesh

**MicroPy Mesh** is a [MeshCore](https://meshcore.io/) LoRa messenger app for
[MicroPythonOS](https://micropythonos.com) devices with a built-in SX1262 radio. It is laid out
for 480×480 touch screens and developed on the Seeed SenseCAP Indicator D1L (ESP32-S3). The
app id is **`eu.axistem.micropymesh`**.
It is inspired by [fri3d-meshcore](https://github.com/lucid-void/fri3d-meshcore) by lucid-void.

- **Chats:** Public, `#hashtag` and private channels (keys in hex or base64, new random keys,
  shared and joined by QR or `meshcore://` link); direct messages (X25519 + AES-128 + HMAC)
  with delivery acknowledgements, retries and optional extra acks; room servers. Day dividers,
  an emoji picker, tappable links (contacts, channels, `#hashtags`, places on the map), block
  senders, mark all as read. Interoperable with the MeshCore apps.
- **Contacts:** saved contacts and a list of discovered nodes (add with +); favourites; sort by
  last heard, name, distance or last message; auto-add by type and hop count; add by public
  key or `meshcore://` contact card.
- **Routing:** per contact auto / flood / a typed path; path hash size 1, 2 or 3 bytes; region
  scopes with a default and a per-channel override; automatic flood and zero-hop adverts.
- **Repeaters and rooms:** guest or admin login with remembered passwords, status, neighbours,
  telemetry, ping, trace; room sessions kept alive, logging in again by itself; a warning
  when a server's clock is off.
- **Message details:** hops, the path with repeater names, SNR, RSSI, region, delivery.
- **Map:** offline tiles from the SD card with pins for contacts, a discovered-nodes map, your
  own position (typed, picked on the map, or from a GPS).
- **Identity:** an on-device Ed25519 key pair and signed adverts; your contact as a QR for the
  MeshCore app to scan; export, or a new identity.
- **Look:** follows the system light or dark mode and accent colour, or the app's own choice.
- **Background radio service:** keeps receiving while the app is closed, with sounds per kind
  and quiet hours.

Protocol logic is pure Python and unit-tested on the desktop.

## Requirements

**MicroPythonOS with a board that publishes its radio** as `LoRaManager.radioChip` (the polled
SX126x driver). The board must also provide a reset hook (`LoRaManager.board_reset`).

**The native `meshcrypto` module is recommended.** It makes signing and verification take
milliseconds; without it the app falls back to pure-Python Ed25519/X25519, which takes seconds
per operation on the device.

Optional, used when the OS offers them: `SDCardManager` (map tiles, identity export),
`AudioManager` with a buzzer output (sounds), `GPSManager` with an NMEA source (own position),
`AppearanceManager` (light/dark mode and accent colour). The app works without each of them.

## Map tiles

The map reads 256 px PNG tiles in the usual web-map layout from the SD card:
`<SD>/maps/<style>/{z}/{x}/{y}.png`, one folder per style. `light` and `dark` follow the app's
theme; any other folder can be picked in Settings › Appearance › Map. An optional
`<style>/credit.txt` holds the attribution line shown on the map. 8-bit palette PNGs with
filter type 0 on every row decode fastest; other PNGs work too.

## Layout

```
eu.axistem.micropymesh/    # the app payload — exactly what ships in the .mpk
  MANIFEST.JSON              # app manifest (launcher activity + boot_completed service)
  icon_64x64.png             # app icon (the 64 px from logo/)
  main_activity.py           # main screen: Chats / Contacts / Map / Radio / Settings tabs
  tab_chats.py, tab_nodes.py, tab_map.py, tab_radio.py, tab_settings.py, ui_tabs.py
  thread_activity.py         # channel and direct-message threads
  node_activity.py           # node detail (login, status, neighbours, telemetry)
  routing_pages.py           # routing, message details, path hash size, regions, scopes
  quick_actions.py           # long-press menus
  settings_pages.py          # settings sub-pages (name, location, identity, contact QR, ...)
  map_model.py, map_tiles.py, map_view.py   # map maths, tile decoding, the map widget
  setup_activity.py          # first-run setup and radio preset choice
  meshcore_manager.py        # radio owner + background service (singleton)
  meshcore_packet.py         # packet parse/serialize
  meshcore_channel.py        # group-channel codec (AES-128 + HMAC)
  meshcore_crypto.py         # Ed25519 / X25519
  meshcore_advert.py         # advert parse/build + share URIs
  meshcore_dm.py             # direct-message + ack codec
  meshcore_server.py         # repeater / room requests and replies
  meshcore_region.py         # region scopes (transport codes)
  meshcore_radio.py          # driver adapter + radio lock helpers
  meshcore_presets.py        # radio presets and LoRa airtime
  meshcore_boot_service.py   # boot_completed service (starts the radio if enabled)
  ui_model.py                # what the screens show (pure Python, desktop-tested)
  ui_theme.py                # widgets, fonts, shared styles, action sheets
  ui_palette.py              # light and dark colours, accent
  fonts/                     # Archivo Narrow + Mesh Mono (subset of IBM Plex Mono), OFL
logo/                        # the app logo: 8–256 px, transparent and on a tile (_tile), 8 px
                             # radio-tower variant (_tower), Awtrix GIFs and the Piskel project
tests/                       # desktop unit tests (CPython); fake_mpos.py stands in for the OS
tests/graphical/             # screen tests on the MicroPythonOS desktop build (480x480)
tools/check_app.py           # bundle checks (compiles, manifest, icon)
tools/run_tests.sh           # the desktop test suite
tools/make_fonts.py          # builds the subset fonts (needs fontTools)
tools/run_graphical.sh       # run tests/graphical (MPOS_DIR = a MicroPythonOS checkout)
tools/deploy.sh              # install the app on a device over Wi-Fi
tools/repl_type.py, tools/screenshot.sh   # device helpers
tools/engine_probe.py        # run the engine on a device without the UI
build_mpk.py                 # build the .mpk locally (no external deps)
```

## Install for development

```
HOST_IP=<this machine on the device's LAN> PYTHON=python3 tools/deploy.sh --start
```

`deploy.sh` builds the `.mpk`, serves it briefly from this machine and types the install
command into the device's REPL over USB serial (`tools/repl_type.py`, needs pyserial). The
package itself travels over Wi-Fi, because bulk copies over a serial REPL are unreliable on
some boards.

## Develop

Run the desktop tests (the app directory goes on `PYTHONPATH`):

```
for t in tests/test_*.py; do PYTHONPATH=eu.axistem.micropymesh python3 "$t"; done
python3 tools/check_app.py eu.axistem.micropymesh --slug eu.axistem.micropymesh
```

Both run in CI on every push (`.github/workflows/ci.yml`). The screen tests need a
MicroPythonOS checkout with its unix build:

```
MPOS_DIR=/path/to/MicroPythonOS tools/run_graphical.sh
```

Two rules that the desktop tests check and the device enforces:

- MicroPythonOS runs an entry point (`main_activity.py`, `meshcore_boot_service.py`) as a
  script and takes the app folder off `sys.path` once the app runs. Never import an entry
  point, and import a module inside a function only if the entry point loads it at start.
- Add LVGL event callbacks with `ui_theme.on(obj, event, fn)`. The LVGL binding never calls a
  callback again after it has raised once; `on` prints the error and keeps the control working.

To build the package:

```
python3 build_mpk.py          # -> eu.axistem.micropymesh_<version>.mpk
```

## License & credits

MIT. See [LICENSE](LICENSE).

Adapts, or interoperates with, these MIT-licensed works (full notices in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)):

- **[python-pure25519](https://github.com/warner/python-pure25519)** © Brian Warner: Ed25519 math.
- **[meshcore-pi](https://github.com/brianwiddas/meshcore-pi)** © Brian Widdas: X25519 and identity
  crypto, used as the reference implementation.
- **[MeshCore](https://github.com/ripplebiz/MeshCore)** © Scott Powell: the protocol and wire-format
  reference.
- **[MicroPython](https://github.com/micropython/micropython)** © Damien P. George: the logo the app
  icon is drawn after (a snake on a chip, here with a radio tower and mesh nodes).

The text font is **[Archivo Narrow](https://github.com/Omnibus-Type/ArchivoNarrow)** © The
Archivo Narrow Project Authors. The font for IDs and numbers is **Mesh Mono**, a subset of
[IBM Plex Mono](https://github.com/IBM/plex) © IBM Corp., renamed because "Plex" is a Reserved
Font Name. Both are used under the **SIL Open Font License 1.1**; the licences ship in
`eu.axistem.micropymesh/fonts/`.

MESHCORE is a trademark of its owner. This is an independent, community-built client; it is not
affiliated with or endorsed by the MeshCore project.
