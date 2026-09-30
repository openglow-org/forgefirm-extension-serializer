# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""Profiles, the run's own record, and the log of cycles, in the service's data directory.

A profile is everything one job needs: the text, its counters and codes,
the layout, the laser, and the operator's notes. Every file is written
whole and renamed into place. What the page sends is held to the
profile's form here - a number to its range, a choice to its choices -
so a profile on disk is always one the service can use.
"""
import copy
import json
import os
import re
import secrets

from . import serials

MAX_PROFILES = 64
NOTES_MAX = 1000
TEXT_MAX = 400
COUNTERS_MAX = 6
CODES_MAX = 6
LOG_KEEP = 500

DEFAULT = {
    "name": "New profile",
    "notes": "",
    "text": "SN {Serial}",
    "font": "hershey-sans",
    "style": "fill",
    "size_mm": 4.0,
    "line_spacing": 1.5,
    "letter_spacing_mm": 0.0,
    "box": {"w": 40.0, "h": 10.0, "halign": "left", "valign": "top"},
    "rotation": 0,
    "grid": {"cols": 1, "rows": 1, "pitch_x": 50.0, "pitch_y": 25.0, "skip": []},
    "start": {"mode": "current", "x": 10.0, "y": 10.0},
    "offset": {"x": 0.0, "y": 0.0},
    "laser": {"line": {"power": 20.0, "speed": 1200.0, "passes": 1},
              "fill": {"power": 20.0, "speed": 6000.0, "interval": 0.1, "passes": 1}},
    "z": 3.0,
    "tray": "in",
    "counters": [{"name": "Serial", "type": "dec", "alphabet": "", "no_lookalikes": False, "width": 6,
                  "first": "000001", "last": "", "step": 1, "next": "000001", "advance": "item",
                  "carry_from": "", "at_end": "stop"}],
    "codes": [],
    "run": {"press_wait_s": 240, "min_open_s": 2.0},
}

COUNTER_DEFAULT = DEFAULT["counters"][0]
ID = re.compile(r"^p-[0-9a-f]{8}$")


class StoreError(Exception):
    """A profile the store cannot take or find, in words for the operator."""


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return max(lo, min(hi, v))


def _int(v, lo, hi, default):
    return int(round(_num(v, lo, hi, default)))


def _choice(v, choices, default):
    return v if v in choices else default


def _text(v, limit, default=""):
    if not isinstance(v, str):
        return default
    v = "".join(ch for ch in v.replace("\r\n", "\n") if ch == "\n" or ch.isprintable())
    return v[:limit]


def _counter(c):
    d = COUNTER_DEFAULT
    c = c if isinstance(c, dict) else {}
    return {
        "name": _text(c.get("name"), 24, d["name"]).strip(),
        "type": _choice(c.get("type"), ("dec", "alpha", "alnum", "hex", "custom"), "dec"),
        "alphabet": _text(c.get("alphabet"), 64),
        "no_lookalikes": bool(c.get("no_lookalikes")),
        "width": _int(c.get("width"), 0, serials.MAX_WIDTH, d["width"]),
        "first": _text(c.get("first"), serials.MAX_WIDTH + 1).strip(),
        "last": _text(c.get("last"), serials.MAX_WIDTH + 1).strip(),
        "step": _int(c.get("step"), 1, 1000000, 1),
        "next": _text(c.get("next"), serials.MAX_WIDTH + 2).strip(),
        "advance": _choice(c.get("advance"), ("item", "cycle", "carry"), "item"),
        "carry_from": _text(c.get("carry_from"), 24).strip(),
        "at_end": _choice(c.get("at_end"), ("stop", "wrap"), "stop"),
    }


def _code(c):
    c = c if isinstance(c, dict) else {}
    vals = c.get("values") if isinstance(c.get("values"), list) else []
    return {
        "name": _text(c.get("name"), 24).strip(),
        "of": _choice(c.get("of"), tuple(serials.CODE_OF), "month"),
        "base_year": _int(c.get("base_year"), 1970, 2200, 2026),
        "values": [_text(v, 8).strip() for v in vals[:60]],
    }


def clean(p):
    """A profile in the profile's form: every key there, every value in its range."""
    d = DEFAULT
    p = p if isinstance(p, dict) else {}
    box, grid, start, off = (p.get(k) if isinstance(p.get(k), dict) else {} for k in ("box", "grid", "start", "offset"))
    laser = p.get("laser") if isinstance(p.get("laser"), dict) else {}
    line = laser.get("line") if isinstance(laser.get("line"), dict) else {}
    fl = laser.get("fill") if isinstance(laser.get("fill"), dict) else {}
    run = p.get("run") if isinstance(p.get("run"), dict) else {}
    cols = _int(grid.get("cols"), 1, 50, 1)
    rows = _int(grid.get("rows"), 1, 50, 1)
    skip = sorted({int(i) for i in grid.get("skip", []) if isinstance(i, (int, float)) and 0 <= i < cols * rows}) \
        if isinstance(grid.get("skip"), list) else []
    return {
        "name": (_text(p.get("name"), 60).strip() or d["name"]).replace("\n", " "),
        "notes": _text(p.get("notes"), NOTES_MAX),
        "text": _text(p.get("text"), TEXT_MAX, d["text"]),
        "font": _text(p.get("font"), 40, d["font"]),
        "style": _choice(p.get("style"), ("fill", "outline", "fill_outline"), "fill"),
        "size_mm": _num(p.get("size_mm"), 0.5, 100.0, d["size_mm"]),
        "line_spacing": _num(p.get("line_spacing"), 0.5, 5.0, d["line_spacing"]),
        "letter_spacing_mm": _num(p.get("letter_spacing_mm"), -5.0, 20.0, 0.0),
        "box": {"w": _num(box.get("w"), 1.0, 495.0, 40.0), "h": _num(box.get("h"), 1.0, 279.0, 10.0),
                "halign": _choice(box.get("halign"), ("left", "center", "right"), "left"),
                "valign": _choice(box.get("valign"), ("top", "middle", "bottom"), "top")},
        "rotation": _choice(p.get("rotation"), (0, 90, 180, 270), 0),
        "grid": {"cols": cols, "rows": rows, "pitch_x": _num(grid.get("pitch_x"), 0.0, 495.0, 50.0),
                 "pitch_y": _num(grid.get("pitch_y"), 0.0, 279.0, 25.0), "skip": skip},
        "start": {"mode": _choice(start.get("mode"), ("current", "fixed"), "current"),
                  "x": _num(start.get("x"), 0.0, 495.0, 10.0), "y": _num(start.get("y"), 0.0, 279.0, 10.0)},
        "offset": {"x": _num(off.get("x"), -495.0, 495.0, 0.0), "y": _num(off.get("y"), -279.0, 279.0, 0.0)},
        "laser": {"line": {"power": _num(line.get("power"), 0.0, 100.0, 20.0),
                           "speed": _num(line.get("speed"), 60.0, 12000.0, 1200.0),
                           "passes": _int(line.get("passes"), 1, 10, 1)},
                  "fill": {"power": _num(fl.get("power"), 0.0, 100.0, 20.0),
                           "speed": _num(fl.get("speed"), 60.0, 12000.0, 6000.0),
                           "interval": _num(fl.get("interval"), 0.02, 2.0, 0.1),
                           "passes": _int(fl.get("passes"), 1, 10, 1)}},
        "z": _num(p.get("z"), -5.0, 80.0, 3.0),
        "tray": _choice(p.get("tray"), ("in", "out"), "in"),
        "counters": [_counter(c) for c in (p.get("counters") or [])[:COUNTERS_MAX]]
        if isinstance(p.get("counters"), list) else copy.deepcopy(d["counters"]),
        "codes": [_code(c) for c in (p.get("codes") or [])[:CODES_MAX]] if isinstance(p.get("codes"), list) else [],
        "run": {"press_wait_s": _int(run.get("press_wait_s"), 30, 3600, 240),
                "min_open_s": _num(run.get("min_open_s"), 0.0, 30.0, 2.0)},
    }


def merge(base, patch):
    """base with patch laid over it, dictionaries key by key; lists and values replaced."""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _write(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Store:
    def __init__(self, root):
        self.root = root
        self.pdir = os.path.join(root, "profiles")
        os.makedirs(self.pdir, exist_ok=True)
        self.state_path = os.path.join(root, "state.json")
        self.log_path = os.path.join(root, "log.jsonl")
        if not self.ids():
            self.create(example())

    # --- profiles ---------------------------------------------------------------------

    def ids(self):
        return sorted(f[:-5] for f in os.listdir(self.pdir) if f.endswith(".json") and ID.match(f[:-5]))

    def list(self):
        out = []
        for pid in self.ids():
            p = self.get(pid)
            if p:
                out.append({"id": pid, "name": p["name"]})
        return sorted(out, key=lambda e: e["name"].lower())

    def get(self, pid):
        if not ID.match(pid or ""):
            return None
        p = _read(os.path.join(self.pdir, pid + ".json"))
        if p is None:
            return None
        p = clean(p)
        p["id"] = pid
        return p

    def must(self, pid):
        p = self.get(pid)
        if p is None:
            raise StoreError("That profile is gone")
        return p

    def put(self, pid, p):
        p = clean(p)
        _write(os.path.join(self.pdir, pid + ".json"), p)
        p["id"] = pid
        return p

    def create(self, p=None, name=None):
        if len(self.ids()) >= MAX_PROFILES:
            raise StoreError("There are %d profiles, the most there can be. Delete one first." % MAX_PROFILES)
        p = clean(p if p is not None else DEFAULT)
        if name:
            p["name"] = name
        taken = {e["name"] for e in self.list()}
        base, n = p["name"], 2
        while p["name"] in taken:
            p["name"] = "%s (%d)" % (base, n)
            n += 1
        pid = "p-" + secrets.token_hex(4)
        return self.put(pid, p)

    def patch(self, pid, patch):
        p = self.must(pid)
        p.pop("id", None)
        return self.put(pid, merge(p, patch))

    def delete(self, pid):
        self.must(pid)
        os.unlink(os.path.join(self.pdir, pid + ".json"))
        if not self.ids():
            self.create(example())

    def set_counters(self, pid, values):
        """The counters' next values after a cycle: {name: next}."""
        p = self.must(pid)
        for c in p["counters"]:
            if c["name"] in values:
                c["next"] = values[c["name"]]
        p.pop("id", None)
        return self.put(pid, p)

    # --- the run's own record ---------------------------------------------------------

    def state(self):
        return _read(self.state_path, {}) or {}

    def save_state(self, doc):
        _write(self.state_path, doc)

    # --- the log ----------------------------------------------------------------------

    def log(self, entry):
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        try:
            if os.path.getsize(self.log_path) > 2 * 1024 * 1024:
                with open(self.log_path) as f:
                    keep = f.readlines()[-LOG_KEEP:]
                tmp = self.log_path + ".tmp"
                with open(tmp, "w") as f:
                    f.writelines(keep)
                os.replace(tmp, self.log_path)
        except OSError:
            pass

    def recent(self, n):
        try:
            with open(self.log_path) as f:
                lines = f.readlines()[-n:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


def example():
    """The first profile on a new machine: one serial plate, to show how it goes."""
    p = copy.deepcopy(DEFAULT)
    p.update({
        "name": "Example: serial plate",
        "notes": "An example to start from. Put the plate on the bed, align the laser to its top-left corner "
                 "with the alignment tool, and press Start.",
        "text": "SN {Serial}\n{Year}-{Month}-{Day}",
        "font": "hershey-sans",
        "size_mm": 3.0,
        "box": {"w": 40.0, "h": 12.0, "halign": "left", "valign": "middle"},
        "offset": {"x": 3.0, "y": 3.0},
    })
    p["counters"][0].update({"width": 6, "first": "000001", "next": "000001"})
    return p
