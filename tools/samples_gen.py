#!/usr/bin/env python3
# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""samples_gen.py - the font picker's samples, made once, shipped in share/samples.json.

The picker shows every font drawn in itself. Drawing all of them takes
the service many seconds on the machine's quarter of the processor, so
they are drawn here, by the service's own code, and the service hands
out the file. tests/api_test.py fails when the file no longer matches
what the code draws: run this again after a change to the fonts or to
the layout.

Usage: samples_gen.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
from serializer import api, fonts  # noqa: E402


def main():
    cat = fonts.Catalog(os.path.join(ROOT, "share"))
    out = {fid: api.sample(cat.get(fid), api.SAMPLE_TEXT) for fid in sorted(cat.faces)}
    with open(os.path.join(ROOT, "share", "samples.json"), "w", newline="\n") as f:
        json.dump(out, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    print("%d samples" % len(out))


if __name__ == "__main__":
    main()
