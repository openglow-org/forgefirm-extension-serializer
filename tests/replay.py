# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""Plays a program the Serializer wrote, the way the controller reads it, for the tests.

Only the words the Serializer writes are known; any other word fails the
test. Returns every move, absolute in machine coordinates, with whether
the laser was lit on it.
"""
import re

WORD = re.compile(r"([A-Z])([-+]?\d*\.?\d+)")


class Move:
    def __init__(self, x0, y0, x1, y1, lit, s, f, rapid):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.lit, self.s, self.f, self.rapid = lit, s, f, rapid


def play(program):
    """(moves, notes): notes has "arm_line" (the index of M4 S0), "first_motion" (the index of the first move),
    "z", "tray", "end" (the last line's words)."""
    x = y = None
    rel = False
    motion = None
    s = 0
    f = None
    laser = False
    moves = []
    notes = {"arm_line": None, "first_motion": None, "z": None, "tray": None, "lines": 0}
    for i, raw in enumerate(program.split("\n")):
        line = raw.split(";")[0].strip()
        if not line:
            continue
        notes["lines"] += 1
        words = WORD.findall(line.replace(" ", ""))
        g53 = False
        nx = ny = nz = None
        for letter, val in words:
            v = float(val)
            if letter == "G":
                if v == 90:
                    rel = False
                elif v == 91:
                    rel = True
                elif v == 53:
                    g53 = True
                elif v in (0, 1):
                    motion = int(v)
                elif v in (21, 94):
                    pass
                else:
                    raise AssertionError("unknown G%s in %r" % (val, raw))
            elif letter == "M":
                if v == 4:
                    laser = True
                    if s == 0 and notes["arm_line"] is None:
                        notes["arm_line"] = i
                elif v == 5:
                    laser = False
                elif v == 103:
                    pass
                elif v == 2:
                    notes["end"] = i
                else:
                    raise AssertionError("unknown M%s in %r" % (val, raw))
            elif letter == "P":
                notes["tray"] = "out" if v == 1 else "in"
            elif letter == "S":
                s = v
            elif letter == "F":
                f = v
            elif letter == "X":
                nx = v
            elif letter == "Y":
                ny = v
            elif letter == "Z":
                nz = v
            else:
                raise AssertionError("unknown word %s in %r" % (letter, raw))
        if "M4" in line.replace(" ", "") and notes["arm_line"] is None and s == 0:
            notes["arm_line"] = i
        if nx is None and ny is None and nz is None:
            continue
        if notes["first_motion"] is None:
            notes["first_motion"] = i
        if nz is not None:
            if not g53:
                raise AssertionError("a Z move outside machine coordinates: %r" % raw)
            notes["z"] = nz
            continue
        if g53:
            if rel:
                raise AssertionError("G53 in relative mode: %r" % raw)
            tx = nx if nx is not None else x
            ty = ny if ny is not None else y
        elif rel:
            if x is None:
                raise AssertionError("a relative move before any absolute one: %r" % raw)
            tx = x + (nx or 0.0)
            ty = y + (ny or 0.0)
        else:
            raise AssertionError("an absolute move without G53: %r" % raw)
        if x is not None:
            lit = laser and motion == 1 and s > 0 and not g53
            moves.append(Move(x, y, tx, ty, lit, s, f, motion == 0 or g53))
        x, y = round(tx, 6), round(ty, 6)
    return moves, notes
