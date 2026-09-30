#!/usr/bin/env python3
# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""org.openglow.serializer's service: serial numbers and dates marked on items, cycle after cycle.

The operator lays out one item or a grid of them, writes the text with
its counters and dates, and presses Start on the package's page. Each
cycle's program goes to the machine when the lid closes, and the machine
marks it when the operator presses its button. The service keeps the
profiles, the counters, and the run (lib/serializer); the page is its
screen.
"""
import os
import sys
import threading

PKG = os.environ.get("FFX_PKG") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PKG, "lib"))
import ffx  # noqa: E402
from serializer import api, fonts, runner, store  # noqa: E402


class Machine:
    """The machine, through the extension API (lib/ffx.py)."""

    def status(self):
        return ffx.machine.status()

    def cool(self):
        return ffx.machine.cool()

    def events(self, since, wait):
        return ffx.events(since, wait)

    def sender_out(self, out):
        return ffx.sender_out(out)

    def sender_state(self):
        return ffx.sender_state()

    def write_program(self, name, text):
        return ffx.write_program(name, text)

    def job(self, name):
        return ffx.job(name)

    def job_abort(self):
        return ffx.job_abort()


def main():
    me = ffx.me()
    st = store.Store(os.environ["FFX_DATA"])
    cat = fonts.Catalog(os.path.join(PKG, "share"))
    m = Machine()
    run = runner.Runner(m, st, cat, me["id"])
    calls = api.Api(st, cat, run, m)

    def handle(method, path, body):
        try:
            return calls.handle(method, path, body)
        except api.CallError as e:
            raise ffx.CallError(e.status, e.words)

    ffx.serve(handle, background=True)
    threading.Thread(target=run.events_loop, name="events", daemon=True).start()
    print("serializer %s: %d profiles, %d fonts" % (me["version"], len(st.ids()), len(cat.faces)), flush=True)
    run.loop()


if __name__ == "__main__":
    main()
