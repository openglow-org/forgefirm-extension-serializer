# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The fonts the package ships, behind one face each.

Two kinds: single-line fonts (share/stroke, strokes the laser follows)
and outline fonts (share/fonts, TrueType shapes it fills or traces).
A face answers the same questions for both: a glyph's advance and its
paths in font units (y up, the baseline at 0), pair kerning, and the cap
height the text size is measured by.
"""
import json
import os

from . import ttf

LINE, OUTLINE = "line", "outline"


class Face:
    def __init__(self, entry, kind, share):
        self.id = entry["id"]
        self.name = entry["name"]
        self.kind = kind
        self.category = entry.get("category", "Single-line")
        self._path = os.path.join(share, entry["file"])
        self._font = None
        self._stroke = None

    def _load(self):
        if self.kind == OUTLINE:
            if self._font is None:
                with open(self._path, "rb") as f:
                    self._font = ttf.Font(f.read())
            return self._font
        if self._stroke is None:
            with open(self._path) as f:
                self._stroke = json.load(f)
        return self._stroke

    @property
    def cap_height(self):
        f = self._load()
        return f.cap_height if self.kind == OUTLINE else f["cap_height"]

    @property
    def descent(self):
        """How far below the baseline the font reaches, in font units (positive)."""
        f = self._load()
        return -f.descent if self.kind == OUTLINE else -f["descent"]

    def has(self, ch):
        f = self._load()
        if ch == " ":
            return True
        return f.has(ch) if self.kind == OUTLINE else ch in f["glyphs"]

    def glyph(self, ch):
        """(advance, paths) for a character the font has; paths are closed contours for an outline font."""
        f = self._load()
        if self.kind == OUTLINE:
            gid = f.glyph_id(ch)
            return f.advance(gid), f.outline(gid)
        g = f["glyphs"].get(ch)
        if g is None:
            return f["glyphs"].get(" ", {"a": f["cap_height"] * 0.5})["a"], []
        return g["a"], [list(zip(p[0::2], p[1::2])) for p in g["p"]]

    def space(self):
        return self.glyph(" ")[0]

    def kern(self, a, b):
        if self.kind != OUTLINE or a is None:
            return 0
        f = self._load()
        return f.kern(f.glyph_id(a), f.glyph_id(b))

    def info(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "category": self.category}


class Catalog:
    def __init__(self, share):
        self.share = share
        self.faces = {}
        stroke = os.path.join(share, "stroke")
        outline = os.path.join(share, "fonts")
        for base, kind in ((stroke, LINE), (outline, OUTLINE)):
            try:
                with open(os.path.join(base, "index.json")) as f:
                    entries = json.load(f)
            except OSError:
                continue
            for e in entries:
                self.faces[e["id"]] = Face(e, kind, base)

    def get(self, fid):
        return self.faces.get(fid)

    def list(self):
        return [f.info() for f in self.faces.values()]
