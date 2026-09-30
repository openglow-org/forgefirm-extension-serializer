#!/usr/bin/env python3
# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""fetch_fonts.py - the outline fonts the package ships, from Google Fonts.

Downloads each family and weight below as a static TrueType file (the
Google Fonts CSS API answers a client it does not know with TrueType
URLs), and the family's license from the google/fonts repository, into
share/fonts/. Writes share/fonts/index.json, the list the service reads.
Run it by hand when the list changes; the files it writes are committed.

Usage: fetch_fonts.py [share/fonts]
"""
import json
import os
import re
import sys
import urllib.request

UA = "forgefirm-serializer-fetch"

# (family, weights, category). The category is the font picker's group.
FAMILIES = [
    ("Roboto", [400, 700], "Sans"),
    ("Open Sans", [400, 700], "Sans"),
    ("Inter", [400, 700], "Sans"),
    ("Montserrat", [400, 700], "Sans"),
    ("Barlow", [500], "Sans"),
    ("Barlow Condensed", [500], "Sans"),
    ("Oswald", [500], "Sans"),
    ("Bebas Neue", [400], "Sans"),
    ("Roboto Slab", [400, 700], "Serif"),
    ("Merriweather", [400], "Serif"),
    ("Libre Baskerville", [400], "Serif"),
    ("EB Garamond", [500], "Serif"),
    ("Playfair Display", [400], "Serif"),
    ("Roboto Mono", [400, 700], "Monospace"),
    ("JetBrains Mono", [400], "Monospace"),
    ("IBM Plex Mono", [400], "Monospace"),
    ("Share Tech Mono", [400], "Monospace"),
    ("B612 Mono", [400], "Monospace"),
    ("Orbitron", [500], "Display"),
    ("Russo One", [400], "Display"),
    ("Black Ops One", [400], "Display"),
    ("Allerta Stencil", [400], "Display"),
    ("Saira Stencil One", [400], "Display"),
    ("Dancing Script", [500], "Script"),
    ("Great Vibes", [400], "Script"),
    ("Pacifico", [400], "Script"),
]

WEIGHT_NAMES = {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}
LICENSES = [("ofl", "OFL.txt", "OFL-1.1"), ("apache", "LICENSE.txt", "Apache-2.0"), ("ufl", "UFL.txt", "UFL-1.0")]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def slug(family):
    return re.sub(r"[^a-z0-9]", "", family.lower())


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "share", "fonts")
    os.makedirs(os.path.join(out, "licenses"), exist_ok=True)
    index = []
    for family, weights, category in FAMILIES:
        s = slug(family)
        lic_id = None
        for folder, name, spdx in LICENSES:
            try:
                text = get("https://raw.githubusercontent.com/google/fonts/main/%s/%s/%s" % (folder, s, name))
            except Exception:
                continue
            with open(os.path.join(out, "licenses", s + ".txt"), "wb") as f:
                f.write(text)
            lic_id = spdx
            break
        if not lic_id:
            raise SystemExit("no license found for %s" % family)
        css = get("https://fonts.googleapis.com/css?family=%s:%s" % (
            family.replace(" ", "+"), ",".join(str(w) for w in weights))).decode()
        faces = re.findall(r"font-weight:\s*(\d+);.*?src:\s*url\(([^)]+\.ttf)\)", css, re.S)
        got = {int(w): u for w, u in faces}
        for w in weights:
            if w not in got:
                raise SystemExit("%s %d: no TrueType file in the answer" % (family, w))
            fid = "%s-%d" % (s, w)
            data = get(got[w])
            with open(os.path.join(out, fid + ".ttf"), "wb") as f:
                f.write(data)
            label = family if len(weights) == 1 and w == 400 else "%s %s" % (family, WEIGHT_NAMES.get(w, str(w)))
            index.append({"id": fid, "name": label, "category": category, "file": fid + ".ttf",
                          "license": lic_id, "license_file": "licenses/%s.txt" % s})
            print("%-28s %7d bytes  %s" % (fid, len(data), lic_id))
    with open(os.path.join(out, "index.json"), "w", newline="\n") as f:
        json.dump(index, f, indent=1)
        f.write("\n")


if __name__ == "__main__":
    main()
