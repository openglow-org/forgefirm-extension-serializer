# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The page's calls (ffx.service.call), answered by the service.

  GET  /state                        the run, the profiles, and the profile the page shows
  GET  /fonts                        the fonts, single-line and outline
  POST /samples {fonts, text}        a line of text in each of those fonts, for the font picker
  POST /profile {id}                 one profile, whole
  POST /profile/new {name, from}     a new profile, or a copy of `from`
  POST /profile/patch {id, set}      part of a profile changed (merged key by key)
  POST /profile/delete {id}
  POST /select {id}                  the profile the page shows
  POST /preview {id, clock, units}   what the next cycle marks: the first item's text as drawn, every slot's
                                     text, the program's size and time, and what would stop it
  POST /run/start {id, clock, units}
  POST /run/stop {now}
  POST /run/send                     send the cycle again, after it was canceled
  POST /log {limit}                  the last cycles marked

`clock` is the browser's {epoch_ms, tz_min}: the machine has no date of its own.
"""
import hashlib
import json
import os
import threading
import time

from . import gcode, serials, store as store_mod, text
from .runner import Refused, fmt_len, homed, z_problem

ANSWER_MAX = 62 * 1024              # the host takes 64 KiB back from a service, headers and all
ANSWER_BUDGET = 48 * 1024           # what one list (the log, the samples) may take of it
SAMPLE_FONTS_MAX = 12
CATEGORY_ORDER = {"Sans": 0, "Serif": 1, "Monospace": 2, "Display": 3, "Script": 4}


SAMPLE_TEXT = "Serial 0123"
SAMPLE_BUDGET = ANSWER_BUDGET // SAMPLE_FONTS_MAX


def sample(face, words):
    """A line of text in one font, drawn small, for the font picker."""
    prof = {"size_mm": 5.0, "line_spacing": 1.5, "box": {"w": 200.0, "h": 8.0}}
    block = text.lay_out(face, [words], prof)
    unit, paths = encode_fit(block.paths, SAMPLE_BUDGET)
    ink = block.ink() or (0, 0, 0, 0)
    return {"unit": unit, "closed": block.closed, "paths": paths, "w": round(ink[2], 3), "h": 8.0}


class CallError(Exception):
    def __init__(self, status, words):
        super().__init__(words)
        self.status = status
        self.words = words


def encode(paths, unit):
    """Paths as flat integer lists in `unit` mm: the first point, then each step from the one before."""
    out = []
    for p in paths:
        flat, px, py = [], None, None
        for x, y in p:
            ix, iy = int(round(x / unit)), int(round(y / unit))
            if px is None:
                flat += [ix, iy]
            elif ix != px or iy != py:
                flat += [ix - px, iy - py]
            else:
                continue
            px, py = ix, iy
        if len(flat) >= 4:
            out.append(flat)
    return out


def thin(paths, step):
    """Paths with the points closer than `step` mm to the last one kept left out; every end is kept."""
    out = []
    for p in paths:
        if len(p) < 3:
            out.append(p)
            continue
        kept = [p[0]]
        for pt in p[1:-1]:
            if abs(pt[0] - kept[-1][0]) + abs(pt[1] - kept[-1][1]) >= step:
                kept.append(pt)
        kept.append(p[-1])
        out.append(kept)
    return out


def encode_fit(paths, budget):
    """encode() at the finest unit whose JSON fits the budget, the drawing thinned if it must be:
    (unit, paths). A preview is for the eye; the program is made from the whole drawing."""
    for step, unit in ((0, 0.01), (0, 0.02), (0, 0.05), (0.2, 0.05), (0.5, 0.1), (1.0, 0.2), (2.0, 0.5)):
        enc = encode(thin(paths, step) if step else paths, unit)
        if len(json.dumps(enc, separators=(",", ":"))) <= budget:
            return unit, enc
    return unit, []


class Api:
    def __init__(self, store, catalog, runner, machine):
        self.store = store
        self.catalog = catalog
        self.runner = runner
        self.m = machine
        self.ui_path = os.path.join(store.root, "ui.json")
        self._status = (0.0, None)
        self._cache = {}
        self._last = {}                 # each profile's last preview, served while a cut runs
        self._shipped = None            # share/samples.json, read on the first ask
        self._lock = threading.Lock()

    # --- helpers ------------------------------------------------------------------------------

    def _selected(self):
        try:
            with open(self.ui_path) as f:
                pid = json.load(f).get("selected")
        except (OSError, ValueError):
            pid = None
        ids = self.store.ids()
        return pid if pid in ids else (self.store.list()[0]["id"] if ids else None)

    def _select(self, pid):
        with open(self.ui_path + ".tmp", "w") as f:
            json.dump({"selected": pid}, f)
        os.replace(self.ui_path + ".tmp", self.ui_path)

    def _machine_status(self):
        at, st = self._status
        if time.monotonic() - at < 1.0 and st is not None:
            return st
        try:
            st = self.m.status()
        except Exception:
            st = None
        self._status = (time.monotonic(), st)
        return st

    def _running_profile(self):
        run = self.runner.view().get("run")
        return run["profile"] if run else None

    @staticmethod
    def _clock(body):
        c = body.get("clock") if isinstance(body.get("clock"), dict) else {}
        try:
            return serials.Clock(float(c["epoch_ms"]), int(c["tz_min"]), time.monotonic())
        except (KeyError, TypeError, ValueError):
            raise CallError(400, "clock is {epoch_ms, tz_min}, the browser's time")

    # --- the calls ------------------------------------------------------------------------------

    def handle(self, method, path, body):
        body = body if isinstance(body, dict) else {}
        try:
            if method == "GET" and path == "/state":
                return self.state()
            if method == "GET" and path == "/fonts":
                return {"fonts": sorted(self.catalog.list(), key=lambda f: (
                    f["kind"] != "line", CATEGORY_ORDER.get(f["category"], 9), f["id"] != "hershey-sans",
                    not f["id"].startswith("hershey"), f["name"]))}
            if method != "POST":
                raise CallError(404, "there is no %s %s" % (method, path))
            if path == "/samples":
                return self.samples(body)
            if path == "/profile":
                return {"profile": self.store.must(body.get("id"))}
            if path == "/profile/new":
                src = self.store.must(body["from"]) if body.get("from") else None
                name = body.get("name") or (("Copy of " + src["name"]) if src else "New profile")
                if src:
                    src = dict(src)
                    src.pop("id", None)
                p = self.store.create(src, name[:60])
                self._select(p["id"])
                return {"profile": p, "profiles": self.store.list()}
            if path == "/profile/patch":
                pid = body.get("id")
                if pid == self._running_profile():
                    raise CallError(409, "This profile is running. Stop the run to change it.")
                patch = body.get("set") if isinstance(body.get("set"), dict) else {}
                patch.pop("id", None)
                return {"profile": self.store.patch(pid, patch), "profiles": self.store.list()}
            if path == "/profile/delete":
                pid = body.get("id")
                if pid == self._running_profile():
                    raise CallError(409, "This profile is running. Stop the run first.")
                self.store.delete(pid)
                return {"profiles": self.store.list(), "selected": self._selected()}
            if path == "/select":
                self.store.must(body.get("id"))
                self._select(body["id"])
                return {"selected": body["id"]}
            if path == "/preview":
                return self.preview(body)
            if path == "/run/start":
                pid = body.get("id")
                self.store.must(pid)
                return self.runner.command("start", pid=pid, clock=self._clock_raw(body),
                                           units=body.get("units", "metric"))
            if path == "/run/stop":
                return self.runner.command("stop", now=bool(body.get("now", True)))
            if path == "/run/send":
                return self.runner.command("send")
            if path == "/log":
                n = max(1, min(200, int(body.get("limit", 50))))
                return {"cycles": self._fit_log(self.store.recent(n))}
            raise CallError(404, "there is no %s" % path)
        except store_mod.StoreError as e:
            raise CallError(404, str(e))
        except Refused as e:
            raise CallError(409, str(e))

    def _clock_raw(self, body):
        self._clock(body)
        return body["clock"]

    @staticmethod
    def _fit_log(entries):
        out, size = [], 0
        for e in entries:
            size += len(json.dumps(e))
            if size > ANSWER_BUDGET:
                break
            out.append(e)
        return out

    def state(self):
        v = self.runner.view()
        v["profiles"] = self.store.list()
        v["selected"] = self._selected()
        return v

    def samples(self, body):
        """The picker's samples: the shipped ones (tools/samples_gen.py) for the picker's own text."""
        ids = body.get("fonts") if isinstance(body.get("fonts"), list) else []
        words = str(body.get("text") or SAMPLE_TEXT)[:24]
        if self._shipped is None:
            try:
                with open(os.path.join(self.catalog.share, "samples.json")) as f:
                    self._shipped = json.load(f)
            except (OSError, ValueError):
                self._shipped = {}
        out = {}
        for fid in ids[:SAMPLE_FONTS_MAX]:
            face = self.catalog.get(fid)
            if face is None:
                continue
            if words == SAMPLE_TEXT and fid in self._shipped:
                out[fid] = self._shipped[fid]
            else:
                out[fid] = sample(face, words)
        return {"samples": out}

    def preview(self, body):
        pid = body.get("id")
        p = self.store.must(pid)
        units = body.get("units", "metric")
        clock = self._clock(body)
        t = clock.at(time.monotonic())
        st = self._machine_status()
        key = hashlib.sha1(json.dumps([p, t.strftime("%Y%m%d%H%M"), units,
                                       (st or {}).get("pos"), homed(st)], sort_keys=True).encode()).hexdigest()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if self.runner.view().get("state") == "running":
                # A cut: the service has a sliver of the processor, and a new preview can wait for the cut's end.
                last = self._last.get(pid)
                return dict(last, stale=True) if last else {"errors": [], "warnings": [], "stale": True}
        out = self._preview(p, t, st, units)
        with self._lock:
            self._cache = {key: out}
            self._last[pid] = out
        return out

    def _preview(self, p, t, st, units):
        errors, warnings = [], []
        face = self.catalog.get(p["font"])
        if face is None:
            return {"errors": ["The font of this profile is not on the machine; pick another."]}
        places = text.slots(p)
        if p["start"]["mode"] == "fixed":
            start = (p["start"]["x"], p["start"]["y"])
        elif st and homed(st) and st.get("pos"):
            start = (float(st["pos"]["x"]), float(st["pos"]["y"]))
        else:
            start = None
            warnings.append("Home the machine to see where the items land: the start point is where the head "
                            "is when you press Start.")
        n = max(1, len(places))
        after = None
        try:
            texts, before, after = serials.layout_cycle(p, n, t)
        except serials.TemplateError as e:
            errors.append(str(e))
            texts = [p["text"].split("\n")] * n
        info = None
        if places:
            try:
                info = gcode.estimate(p, face, texts[:len(places)], start or (0.0, 0.0))
            except gcode.JobError as e:
                errors.append(str(e))
        else:
            errors.append("Every slot of the grid is skipped: there is nothing to mark.")
        out = {"errors": errors, "warnings": warnings, "next": after, "start": list(start) if start else None,
               "style": gcode.style_of(p, face), "kind": face.kind}
        if info is None:
            blocks = [(0, 0.0, 0.0, text.lay_out(face, texts[0], p))]
        else:
            blocks = info["blocks"]
            probs = gcode.problems(info)
            if start is None:
                probs = [x for x in probs if not x.startswith("Part of the layout")]
            errors.extend(probs)
            out["program"] = {"bytes": info["bytes"], "seconds": round(info["seconds"]), "items": len(places),
                              "max_bytes": gcode.PROGRAM_MAX}
        z = z_problem(p, st, units)
        if z:
            errors.append(z)
        block = blocks[0][3]
        if block.missing:
            warnings.append("The font has no %s; %s left as a space." % (
                ", ".join("'%s'" % c for c in block.missing[:6]), "it is" if len(block.missing) == 1 else "they are"))
        bw, bh = float(p["box"]["w"]), float(p["box"]["h"])
        if block.width > bw + 0.01 or block.height > bh + 0.01:
            warnings.append("The text (%s x %s) is bigger than its box (%s x %s)." % (
                fmt_len(block.width, units), fmt_len(block.height, units), fmt_len(bw, units), fmt_len(bh, units)))
        grid = p["grid"]
        slots = []
        texts_by = {idx: lines for (idx, _, _), lines in zip(places, texts)}
        for r in range(grid["rows"]):
            for c in range(grid["cols"]):
                idx = r * grid["cols"] + c
                ox, oy = text.slot_origin(p, start or (0.0, 0.0), r, c)
                slots.append({"i": idx, "x": round(ox, 2), "y": round(oy, 2), "lines": texts_by.get(idx)})
        out["slots"] = slots
        out["box_bed"] = [block.box_w, block.box_h]
        out["item"] = {"box": [block.box_w, block.box_h], "unit": 0.01, "closed": block.closed, "paths": [],
                       "ink": list(block.ink()) if block.ink() else None, "text_w": block.width,
                       "text_h": block.height, "lines": texts[0]}
        size = len(json.dumps(out, separators=(",", ":")))
        if size > ANSWER_BUDGET // 2:                    # the slots' text shortened before the drawing is
            for s in out["slots"]:
                s["lines"] = (s["lines"] or [])[:1] if s["lines"] is not None else None
            size = len(json.dumps(out, separators=(",", ":")))
        out["item"]["unit"], out["item"]["paths"] = encode_fit(block.paths, ANSWER_MAX - size - 2048)
        return out
