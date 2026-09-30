# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""One cycle's program: every item's text, as G-code for the machine.

The program's first laser line comes before anything moves: the machine
waits there for the operator's press on its button, and only then moves
the lens to the material and the head to the first item. Each item starts
with a move in machine coordinates (G53), whatever work offsets a Grbl
sender left behind, and its own moves are relative (G91) on a grid of
0.01 mm, so the program is short and every item lands where it was laid
out. The laser runs in M4, whose power follows the speed through every
corner. The head goes back to the start point at the end.

A package's service has a quarter of the processor, so the writer is
written for speed: a fill row is written by one loop, not a call a move.
"""
import math

from . import fill, text

WORK_W, WORK_H = 495.0, 279.0       # the work area, mm, as the machine is set up by default
RAPID_MM_MIN = 12000.0
PROGRAM_MAX = 2 * 1024 * 1024       # what the machine takes from a package
GAP_RAPID_MM = 3.0                  # a gap in a fill row shorter than this is crossed at the fill speed, dark
SEG_OVERHEAD_S = 0.004              # a rough cost of each short segment's speed change, for the estimate
RAPID_S_PER_MM = 60.0 / RAPID_MM_MIN


class JobError(Exception):
    """A cycle that cannot be made, in words for the operator."""


def _num(v):
    """Hundredths of a millimeter (an int) as the shortest decimal."""
    if v % 100 == 0:
        return str(v // 100)
    return ("%.2f" % (v / 100.0)).rstrip("0")


class Writer:
    def __init__(self):
        self.lines = []
        self.px = self.py = None        # hundredths of a mm, absolute
        self.rel = False
        self.s = None
        self.f = None
        self.seconds = 0.0

    def emit(self, line):
        self.lines.append(line)

    def jump(self, x, y):
        """A dark move to (x, y) in machine coordinates."""
        ix, iy = int(round(x * 100)), int(round(y * 100))
        if self.rel:
            self.lines.append("G90")
            self.rel = False
        if self.px is not None:
            self.seconds += math.hypot(ix - self.px, iy - self.py) / 100.0 * RAPID_S_PER_MM + 0.05
        self.lines.append("G53 G0 X%s Y%s" % (_num(ix), _num(iy)))
        self.px, self.py = ix, iy

    def _relative(self):
        if not self.rel:
            self.lines.append("G91")
            self.rel = True

    def strokes(self, paths, s, f):
        """Polylines, each reached dark and followed lit."""
        self._relative()
        ap = self.lines.append
        px, py, cs, cf = self.px, self.py, self.s, self.f
        secs = 0.0
        per_mm = 60.0 / f
        for p in _order(paths, (px / 100.0, py / 100.0)):
            if len(p) < 2:
                continue
            ix, iy = int(round(p[0][0] * 100)), int(round(p[0][1] * 100))
            dx, dy = ix - px, iy - py
            if dx or dy:
                ap("G0" + ("X" + _num(dx) if dx else "") + ("Y" + _num(dy) if dy else ""))
                secs += math.hypot(dx, dy) / 100.0 * RAPID_S_PER_MM + 0.02
                px, py = ix, iy
            for x, y in p[1:]:
                ix, iy = int(round(x * 100)), int(round(y * 100))
                dx, dy = ix - px, iy - py
                if not dx and not dy:
                    continue
                line = "G1" + ("X" + _num(dx) if dx else "") + ("Y" + _num(dy) if dy else "")
                if cs != s:
                    line += "S%d" % s
                    cs = s
                if cf != f:
                    line += "F%d" % f
                    cf = f
                ap(line)
                secs += math.hypot(dx, dy) / 100.0 * per_mm + SEG_OVERHEAD_S
                px, py = ix, iy
        self.px, self.py, self.s, self.f = px, py, cs, cf
        self.seconds += secs

    def fill(self, rows, s, f):
        """Fill rows, back and forth: each span lit; a short gap in a row crossed dark at the fill speed, a long
        one and the step to the next row at the rapid rate."""
        self._relative()
        ap = self.lines.append
        px, py, cs, cf = self.px, self.py, self.s, self.f
        secs = 0.0
        per_mm = 60.0 / f
        gap = GAP_RAPID_MM * 100
        forward = True
        for y, spans in rows:
            iy = int(round(y * 100))
            seq = spans if forward else [(b, a) for a, b in reversed(spans)]
            first = True
            for a, b in seq:
                ia, ib = int(round(a * 100)), int(round(b * 100))
                dx, dy = ia - px, iy - py
                if first or dx > gap or -dx > gap:
                    if dx or dy:
                        ap("G0" + ("X" + _num(dx) if dx else "") + ("Y" + _num(dy) if dy else ""))
                        secs += math.hypot(dx, dy) / 100.0 * RAPID_S_PER_MM + 0.02
                elif dx:
                    line = "G1X" + _num(dx)
                    if cs != 0:
                        line += "S0"
                        cs = 0
                    ap(line)
                    secs += abs(dx) / 100.0 * per_mm + SEG_OVERHEAD_S
                first = False
                dx = ib - ia
                if dx:
                    line = "G1X" + _num(dx)
                    if cs != s:
                        line += "S%d" % s
                        cs = s
                    if cf != f:
                        line += "F%d" % f
                        cf = f
                    ap(line)
                    secs += abs(dx) / 100.0 * per_mm + SEG_OVERHEAD_S
                px, py = ib, iy
            forward = not forward
        self.px, self.py, self.s, self.f = px, py, cs, cf
        self.seconds += secs

    def text(self):
        return "\n".join(self.lines) + "\n"


def _order(paths, start):
    """Polylines in a short travel order: nearest end first, reversed when its far end is nearer."""
    left = list(paths)
    out, cx, cy = [], start[0], start[1]
    while left:
        best, bi, rev = None, 0, False
        for i, p in enumerate(left):
            d0 = (p[0][0] - cx) ** 2 + (p[0][1] - cy) ** 2
            d1 = (p[-1][0] - cx) ** 2 + (p[-1][1] - cy) ** 2
            if best is None or d0 < best:
                best, bi, rev = d0, i, False
            if d1 < best:
                best, bi, rev = d1, i, True
        p = left.pop(bi)
        p = p[::-1] if rev else p
        out.append(p)
        cx, cy = p[-1]
    return out


def _power(pct):
    return int(round(max(0.0, min(100.0, float(pct))) * 10))


def _feed(mm_min):
    return int(round(max(60.0, min(RAPID_MM_MIN, float(mm_min)))))


def style_of(profile, face):
    if face.kind == "line":
        return "line"
    st = profile.get("style", "fill")
    return st if st in ("fill", "outline", "fill_outline") else "fill"


def lay_out(profile, face, texts, start):
    """Every slot's block in marking order: [(slot index, box left, box top, Block)]."""
    places = text.slots(profile)
    if not places:
        raise JobError("Every slot of the grid is skipped: there is nothing to mark")
    if len(texts) < len(places):
        raise JobError("The texts and the grid's slots do not match")
    out = []
    for (idx, row, col), lines in zip(places, texts):
        ox, oy = text.slot_origin(profile, start, row, col)
        out.append((idx, ox, oy, text.lay_out(face, lines, profile)))
    return out


def extent(blocks, start):
    """(min x, min y, max x, max y) of every lit point and of the start point, on the bed."""
    lo_x = hi_x = start[0]
    lo_y = hi_y = start[1]
    for _, ox, oy, block in blocks:
        ink = block.ink()
        if ink:
            lo_x, lo_y = min(lo_x, ox + ink[0]), min(lo_y, oy + ink[1])
            hi_x, hi_y = max(hi_x, ox + ink[2]), max(hi_y, oy + ink[3])
    return lo_x, lo_y, hi_x, hi_y


def build(profile, face, texts, start, heading="", blocks=None):
    """The cycle's program for these texts, one per slot in order, with the grid's start point at `start`.

    Returns (program, info): info has the blocks laid out, the program's size, an estimate of its time, and
    what reaches past the work area."""
    blocks = blocks if blocks is not None else lay_out(profile, face, texts, start)
    laser = profile.get("laser", {})
    line = laser.get("line", {})
    fl = laser.get("fill", {})
    style = style_of(profile, face)
    z = float(profile.get("z", 0.0))
    w = Writer()
    w.emit("; Serializer%s" % ("" if not heading else ": " + heading))
    w.emit("G21 G90 G94 M5")
    w.emit("M103 P%d" % (1 if profile.get("tray") == "out" else 0))
    w.emit("M4 S0")
    w.emit("G53 G0 Z%s" % _num(int(round(z * 100))))
    for n, (_, ox, oy, block) in enumerate(blocks, 1):
        paths = block.moved(ox, oy)
        if not paths:
            continue
        w.emit("; item %d" % n)
        w.jump(paths[0][0][0], paths[0][0][1])
        if style in ("fill", "fill_outline"):
            rows = fill.rows(paths, float(fl.get("interval", 0.1)))
            for _ in range(max(1, int(fl.get("passes", 1)))):
                w.fill(rows, _power(fl.get("power", 30)), _feed(fl.get("speed", 6000)))
        if style in ("line", "outline", "fill_outline"):
            for _ in range(max(1, int(line.get("passes", 1)))):
                w.strokes(paths, _power(line.get("power", 30)), _feed(line.get("speed", 1200)))
    w.emit("M5")
    w.jump(start[0], start[1])
    w.emit("M2")
    program = w.text()
    box = extent(blocks, start)
    return program, {"blocks": blocks, "bytes": len(program), "lines": len(w.lines), "seconds": w.seconds,
                     "bounds": box, "outside": _outside(*box)}


def estimate(profile, face, texts, start):
    """What a cycle comes to - its size and time - from its first item's program, with every item laid out
    for where they reach: a preview's answer, at a small part of a whole build's cost. Same keys as build()'s
    info, and "estimated"."""
    blocks = lay_out(profile, face, texts, start)
    one, info1 = build(profile, face, texts, start, blocks=blocks[:1])
    none, info0 = build(profile, face, texts, start, blocks=[])
    n = len(blocks)
    per_bytes = info1["bytes"] - info0["bytes"]
    per_s = info1["seconds"] - info0["seconds"]
    box = extent(blocks, start)
    travel = 0.0
    for a, b in zip(blocks, blocks[1:]):
        travel += math.hypot(b[1] - a[1], b[2] - a[2]) * RAPID_S_PER_MM + 0.05
    return {"blocks": blocks, "bytes": info0["bytes"] + per_bytes * n, "seconds": info0["seconds"] + per_s * n + travel,
            "bounds": box, "outside": _outside(*box), "estimated": True}


def problems(info):
    """What keeps a program from running, in words: [] when nothing does."""
    out = []
    if info["outside"]:
        out.append("Part of the layout is outside the work area (%s). Move the start point, the offset, or the "
                   "grid." % info["outside"])
    if info["bytes"] > PROGRAM_MAX:
        out.append("One cycle makes a program of %.1f MB, and the machine takes 2 MB at most. Mark fewer items "
                   "at a time, widen the fill's line interval, or use a single-line font." % (
                       info["bytes"] / 1048576.0))
    return out


def _outside(lo_x, lo_y, hi_x, hi_y):
    """Where the program reaches past the work area, in words, or "" when it stays inside."""
    out = []
    if lo_x < 0:
        out.append("%.1f mm past the left edge" % -lo_x)
    if hi_x > WORK_W:
        out.append("%.1f mm past the right edge" % (hi_x - WORK_W))
    if lo_y < 0:
        out.append("%.1f mm past the back" % -lo_y)
    if hi_y > WORK_H:
        out.append("%.1f mm past the front" % (hi_y - WORK_H))
    return ", ".join(out)
