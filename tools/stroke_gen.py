#!/usr/bin/env python3
# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""stroke_gen.py - the single-line fonts the package ships.

Reads SVG fonts of single strokes - the Hershey fonts and the EMS fonts as
Inkscape distributes them (share/inkscape/extensions/svg_fonts) - and
writes each as share/stroke/<id>.json: every glyph as polylines in the
font's own units (y up, the baseline at 0), with its advance. Curves are
flattened here, to within 0.5 units, so the service draws lines only.
The cap height is measured from the glyph "H", since the fonts' own
cap-height attribute does not describe their glyphs.

Writes share/stroke/index.json, the list the service reads, and copies the
licenses the data carries (the Hershey acknowledgment, the OFL) and each
font's own notice (NOTICE.txt).

Usage: stroke_gen.py <svg_fonts directory> [share/stroke]
"""
import html
import json
import math
import os
import re
import sys

# (file, id, display name). The Hershey fonts first, then the EMS fonts.
FONTS = [
    ("HersheySans1.svg", "hershey-sans", "Hershey Sans"),
    ("HersheySansMed.svg", "hershey-sans-med", "Hershey Sans Medium"),
    ("HersheySerifMed.svg", "hershey-serif", "Hershey Serif"),
    ("HersheySerifMedItalic.svg", "hershey-serif-italic", "Hershey Serif Italic"),
    ("HersheyScript1.svg", "hershey-script", "Hershey Script"),
    ("HersheyScriptMed.svg", "hershey-script-med", "Hershey Script Medium"),
    ("HersheyGothEnglish.svg", "hershey-gothic", "Hershey Gothic English"),
    ("EMSReadability.svg", "ems-readability", "EMS Readability"),
    ("EMSReadabilityItalic.svg", "ems-readability-italic", "EMS Readability Italic"),
    ("EMSTech.svg", "ems-tech", "EMS Tech"),
    ("EMSOsmotron.svg", "ems-osmotron", "EMS Osmotron"),
    ("EMSNixish.svg", "ems-nixish", "EMS Nixish"),
    ("EMSNixishItalic.svg", "ems-nixish-italic", "EMS Nixish Italic"),
    ("EMSAllure.svg", "ems-allure", "EMS Allure"),
    ("EMSElfin.svg", "ems-elfin", "EMS Elfin"),
    ("EMSFelix.svg", "ems-felix", "EMS Felix"),
]

HERSHEY_NOTICE = """The Hershey Fonts were originally created by Dr. A. V. Hershey while
working at the U. S. National Bureau of Standards.

The format of the Font data in the distribution these files were made
from was originally created by James Hurt, Cognition, Inc., 900
Technology Park Drive, Billerica, MA 01821 (mit-eddie!ci-dandelion!hurt).

This distribution of the Hershey Fonts may be used by anyone for any
purpose, commercial or otherwise, providing that this acknowledgment is
distributed with the font data. The data here is converted from the SVG
fonts prepared by Windell H. Oskay (Evil Mad Scientist Laboratories).
"""

TOL = 0.5   # font units: the most a flattened curve strays from the curve
NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
TOK = re.compile(r"[MmLlCcQqZzHhVv]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def cubic(p0, p1, p2, p3):
    d = max(math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3), 1e-9)
    n = max(2, min(64, int(math.ceil(math.sqrt(d / TOL)))))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def quad(p0, p1, p2):
    return cubic(p0, (p0[0] + 2 / 3 * (p1[0] - p0[0]), p0[1] + 2 / 3 * (p1[1] - p0[1])),
                 (p2[0] + 2 / 3 * (p1[0] - p2[0]), p2[1] + 2 / 3 * (p1[1] - p2[1])), p2)


def parse_path(d):
    """Path data as polylines of (x, y)."""
    toks = TOK.findall(d)
    polys, cur, pos, start = [], None, (0.0, 0.0), (0.0, 0.0)
    i, cmd = 0, None

    def nums(k):
        nonlocal i
        vals = [float(t) for t in toks[i:i + k]]
        if len(vals) != k or any(not NUM.fullmatch(t) for t in toks[i:i + k]):
            raise SystemExit("short path data in %r" % d[:80])
        i += k
        return vals

    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur is not None and cur[-1] != start:
                    cur.append(start)
                pos = start
                continue
        elif cmd is None:
            raise SystemExit("path without a command: %r" % d[:80])
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = pos if rel else (0.0, 0.0)
        if c == "M":
            x, y = nums(2)
            pos = start = (ox + x, oy + y)
            cur = [pos]
            polys.append(cur)
            cmd = "l" if rel else "L"          # further pairs are line-tos
        elif c == "L":
            x, y = nums(2)
            pos = (ox + x, oy + y)
            cur.append(pos)
        elif c == "H":
            (x,) = nums(1)
            pos = ((pos[0] if rel else 0) + x, pos[1])
            cur.append(pos)
        elif c == "V":
            (y,) = nums(1)
            pos = (pos[0], (pos[1] if rel else 0) + y)
            cur.append(pos)
        elif c == "C":
            v = nums(6)
            p1, p2, p3 = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3]), (ox + v[4], oy + v[5])
            cur.extend(cubic(pos, p1, p2, p3))
            pos = p3
        elif c == "Q":
            v = nums(4)
            p1, p2 = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3])
            cur.extend(quad(pos, p1, p2))
            pos = p2
        else:
            raise SystemExit("unsupported path command %r" % cmd)
    return [p for p in polys if len(p) >= 2]


def attr(tag, name):
    m = re.search(r'\s%s="([^"]*)"' % re.escape(name), tag)
    return html.unescape(m.group(1)) if m else None


def convert(path):
    text = open(path, encoding="utf-8").read()
    font = re.search(r"<font\s[^>]*>", text).group(0)
    face = re.search(r"<font-face\s[^>]*>", text, re.S).group(0)
    default_adv = float(attr(font, "horiz-adv-x") or 500)
    upm = float(attr(face, "units-per-em") or 1000)
    glyphs = {}
    for tag in re.findall(r"<glyph\s[^>]*>", text, re.S):
        u = attr(tag, "unicode")
        if not u or len(u) != 1:
            continue
        adv = float(attr(tag, "horiz-adv-x") or default_adv)
        d = attr(tag, "d") or ""
        lines = []
        for poly in parse_path(d):
            flat = []
            for x, y in poly:
                flat += [round(x, 1), round(y, 1)]
            lines.append(flat)
        glyphs[u] = {"a": round(adv, 1), "p": lines}
    h = glyphs.get("H")
    if not h or not h["p"]:
        raise SystemExit("%s: no H to measure the cap height by" % path)
    cap = max(max(line[1::2]) for line in h["p"])
    ys = [y for g in glyphs.values() for line in g["p"] for y in line[1::2]]
    meta = re.search(r"<metadata>(.*?)</metadata>", text, re.S)
    return {"notice": meta.group(1).strip() if meta else "", "units_per_em": upm, "cap_height": cap,
            "ascent": max(ys), "descent": min(ys), "glyphs": glyphs}


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "share", "stroke")
    os.makedirs(out, exist_ok=True)
    index, notices = [], []
    for fname, fid, name in FONTS:
        data = convert(os.path.join(src, fname))
        notices.append("%s (%s)\n%s\n" % (name, fname, data.pop("notice")))
        data["name"] = name
        data["license"] = "Hershey" if fid.startswith("hershey") else "OFL-1.1"
        with open(os.path.join(out, fid + ".json"), "w", newline="\n") as f:
            json.dump(data, f, separators=(",", ":"))
        index.append({"id": fid, "name": name, "file": fid + ".json", "license": data["license"],
                      "license_file": "HERSHEY.txt" if data["license"] == "Hershey" else "OFL.txt"})
        print("%-24s %3d glyphs  cap %.0f" % (fid, len(data["glyphs"]), data["cap_height"]))
    with open(os.path.join(out, "HERSHEY.txt"), "w", newline="\n") as f:
        f.write(HERSHEY_NOTICE)
    with open(os.path.join(out, "NOTICE.txt"), "w", newline="\n") as f:
        f.write("Where each font's data comes from, as its SVG font says.\n\n" + "\n".join(notices))
    with open(os.path.join(src, "OFL.txt"), encoding="utf-8") as f:
        ofl = f.read()
    with open(os.path.join(out, "OFL.txt"), "w", newline="\n") as f:
        f.write(ofl)
    with open(os.path.join(out, "index.json"), "w", newline="\n") as f:
        json.dump(index, f, indent=1)
        f.write("\n")


if __name__ == "__main__":
    main()
