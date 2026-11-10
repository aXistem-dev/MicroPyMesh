# Third-party notices

This app adapts and interoperates with the following third-party works. The four code,
reference and artwork works are each used under the MIT License; their original copyright
notices are reproduced below, followed by the MIT license text (identical for all four). The bundled
font is used under the SIL Open Font License 1.1.

## python-pure25519
Copyright (c) 2015 Brian Warner and contributors
https://github.com/warner/python-pure25519

Ed25519 field/point arithmetic and EdDSA sign/verify are vendored and adapted (made
pure-Python and MicroPython-compatible) in `eu.axistem.micropymesh/meshcore_crypto.py`.

## meshcore-pi
Copyright (c) 2025 Brian Widdas
https://github.com/brianwiddas/meshcore-pi

The X25519 shared-secret derivation and MeshCore's identity-key convention (a node's private
key is the 64-byte SHA512(seed)) were ported into `meshcore_crypto.py`. meshcore-pi was also
used as an independent cross-implementation reference to validate the packet, channel, advert
and direct-message codecs.

## MeshCore
Copyright (c) 2025 Scott Powell / rippleradios.com
https://github.com/ripplebiz/MeshCore

Used as the protocol / wire-format reference (packet header, group-channel and direct-message
crypto, advert layout, and the PATH/ACK acknowledgement format). No source code is copied; the
interoperable wire format is re-implemented in pure Python from the specification and source.

MESHCORE is a trademark of its owner: this app is an independent, community-built client and
is not affiliated with or endorsed by the MeshCore project.

## MicroPython
Copyright (c) 2013-2026 Damien P. George
https://github.com/micropython/micropython

**The app icon** (`eu.axistem.micropymesh/icon_64x64.png`, and every size in `logo/`) is an
original drawing after the MicroPython logo, which ships in that MIT-licensed repository under
`logo/`: a red snake with a yellow belly coiled on a microcontroller chip. The drawing adds a
radio tower and mesh nodes for MeshCore; no part of the MicroPython artwork is copied.

## Archivo Narrow
Copyright 2019 The Archivo Narrow Project Authors
https://github.com/Omnibus-Type/ArchivoNarrow

Licensed under the SIL Open Font License, Version 1.1 -- **not** the MIT License that covers
the rest of this app. The full text ships with the fonts at
`eu.axistem.micropymesh/fonts/OFL.txt`, as the OFL requires.

`ArchivoNarrow-Regular.ttf` and `ArchivoNarrow-SemiBold.ttf` are instances (weights 400 and
600) of the upstream variable font, subset to Latin-1 plus a few symbols by
`tools/make_fonts.py`. The font carries no Reserved Font Name, so the modified copies keep the
family name. They are bundled and rendered, never sold on their own.

## Mesh Mono (a subset of IBM Plex Mono)
Copyright 2017 IBM Corp., with Reserved Font Name "Plex"
https://github.com/IBM/plex

Licensed under the SIL Open Font License, Version 1.1; the full text ships at
`eu.axistem.micropymesh/fonts/OFL-IBMPlexMono.txt`.

`MeshMono-Regular.ttf` is IBM Plex Mono Regular subset to Latin-1 plus a few symbols by
`tools/make_fonts.py`. Because "Plex" is a Reserved Font Name, the modified font is renamed
"Mesh Mono"; its copyright and trademark records are unchanged. IBM Plex is a trademark of
IBM Corp.

---

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
