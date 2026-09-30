#!/usr/bin/env python3
# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""bridge.py - pastes the SDK's page client into the page.

A package's page is one file, so the panel's bridge client
(forgeext's sdk/js/ffx-bridge.js) is pasted into ui/index.html, in the
script element that starts with the "ffx-bridge.js ... pasted whole"
comment. Run it again to bring the pasted copy up to the SDK's.

Usage: bridge.py [path/to/ffx-bridge.js]   (default: ../forgeext/sdk/js/ffx-bridge.js)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "..", "ui", "index.html")
MARK = "/* ffx-bridge.js, extension API 0.1, from the forgeext SDK (sdk/js/ffx-bridge.js), pasted whole. */\n"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "forgeext", "sdk", "js", "ffx-bridge.js")
    with open(src, encoding="utf-8") as f:
        bridge = f.read().rstrip("\n") + "\n"
    with open(PAGE, encoding="utf-8") as f:
        page = f.read()
    start = page.index(MARK) + len(MARK)
    end = page.index("</script>", start)
    page = page[:start] + bridge + page[end:]
    if re.search(r"@@\w+@@", page):
        raise SystemExit("a placeholder is left in the page")
    with open(PAGE, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    print("pasted %d bytes of %s" % (len(bridge), os.path.basename(src)))


if __name__ == "__main__":
    main()
