"""Build the app's bundled fonts: Archivo Narrow Regular/SemiBold and IBM Plex Mono Regular,
subset to Latin-1 plus the few symbols the UI prints. Needs fontTools.

    python3 tools/make_fonts.py ARCHIVO_NARROW_VARIABLE.ttf IBM_PLEX_MONO_REGULAR.ttf IBM_PLEX_MONO_MEDIUM.ttf

Sources (SIL Open Font License 1.1): google/fonts ofl/archivonarrow/ArchivoNarrow[wght].ttf
and ofl/ibmplexmono/IBMPlexMono-{Regular,Medium}.ttf. "Plex" is a Reserved Font Name, so the subset
of IBM Plex Mono is renamed "Mesh Mono" (the OFL forbids a modified version keeping it).
The delivery icons (tick, cross, arrow, hourglass) come from LVGL's built-in symbol font.
"""
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "eu.axistem.micropymesh", "fonts")
EXTRA = "·→×…−›°€‘’“”–—•≈"
UNICODES = list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) + [ord(c) for c in EXTRA]


def _subset(font, path):
    opts = subset.Options()
    opts.layout_features = ["kern"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.hinting = False
    opts.desubroutinize = True
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=UNICODES)
    sub.subset(font)
    font.save(path)
    print("%-32s %6d bytes" % (os.path.basename(path), os.path.getsize(path)))


def _rename(font, old_names, new_name):
    """Replace the family name in the naming records (OFL Reserved Font Name rule); the
    copyright, trademark and licence records stay as they are."""
    for rec in font["name"].names:
        if rec.nameID not in (1, 3, 4, 6, 16, 17, 18, 21, 22):
            continue
        try:
            text = rec.toUnicode()
        except Exception:
            continue
        for old in old_names:
            if old in text:
                text = text.replace(old, new_name if " " in old else new_name.replace(" ", ""))
        rec.string = text


def main():
    archivo, plex, plex_medium = sys.argv[1], sys.argv[2], sys.argv[3]
    for weight, name in ((400, "ArchivoNarrow-Regular.ttf"), (600, "ArchivoNarrow-SemiBold.ttf"),
                         (700, "ArchivoNarrow-Bold.ttf")):
        font = instancer.instantiateVariableFont(TTFont(archivo), {"wght": weight})
        _subset(font, os.path.join(OUT, name))
    for src, name in ((plex, "MeshMono-Regular.ttf"), (plex_medium, "MeshMono-Medium.ttf")):
        mono = TTFont(src)
        _rename(mono, ("IBM Plex Mono", "IBMPlexMono", "Plex"), "Mesh Mono")
        _subset(mono, os.path.join(OUT, name))


if __name__ == "__main__":
    main()
