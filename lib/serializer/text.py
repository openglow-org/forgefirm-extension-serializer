# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""Laying out one item's text in its box.

Coordinates are the bed's: x to the right, y toward the front of the
machine, in millimeters - the machine's own, whose X0 Y0 is the back-left
corner. The text box is placed by its top-left corner on the bed. The text
size is the height of its capitals; a block of lines runs from the top of
the first line's capitals to the baseline of the last, and that block is
what is aligned in the box, so that a letter with a tail (g, y) never moves
the text.
"""

ROTATIONS = (0, 90, 180, 270)


class Block:
    """One item's text, laid out in its box, the box's top-left at (0, 0) on the bed."""

    def __init__(self, paths, closed, box_w, box_h, width, height, missing):
        self.paths = paths              # [[(x, y), ...], ...]
        self.closed = closed            # the paths are contours to fill (an outline font)
        self.box_w = box_w              # the box on the bed, after the rotation
        self.box_h = box_h
        self.width = width              # the text itself, before the rotation: its widest line
        self.height = height            # and its block, capitals to the last baseline
        self.missing = missing          # characters the font does not have

    def ink(self):
        """(min x, min y, max x, max y) of what is drawn, or None."""
        xs = [x for p in self.paths for x, _ in p]
        ys = [y for p in self.paths for _, y in p]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def moved(self, dx, dy):
        return [[(x + dx, y + dy) for x, y in p] for p in self.paths]


def line_width(face, text, scale, tracking):
    w, prev, n = 0.0, None, 0
    for ch in text:
        if not face.has(ch):
            ch = " "
        adv = face.glyph(ch)[0] if ch != " " else face.space()
        w += (adv + face.kern(prev, ch)) * scale
        prev = ch
        n += 1
    return w + tracking * max(0, n - 1)


def lay_out(face, lines, profile):
    """The item's text as a Block, from the profile's text settings."""
    size = float(profile.get("size_mm", 5.0))
    spacing = float(profile.get("line_spacing", 1.5))
    tracking = float(profile.get("letter_spacing_mm", 0.0))
    box = profile.get("box", {})
    bw, bh = float(box.get("w", 40.0)), float(box.get("h", 10.0))
    halign = box.get("halign", "left")
    valign = box.get("valign", "top")
    rot = int(profile.get("rotation", 0))
    scale = size / float(face.cap_height)
    pitch = size * spacing
    n = max(1, len(lines))
    height = size + pitch * (n - 1)
    top = 0.0 if valign == "top" else (bh - height) / 2.0 if valign == "middle" else bh - height
    paths, missing, width = [], [], 0.0
    for i, text in enumerate(lines):
        w = line_width(face, text, scale, tracking)
        width = max(width, w)
        x = 0.0 if halign == "left" else (bw - w) / 2.0 if halign == "center" else bw - w
        base = top + size + pitch * i
        prev = None
        for ch in text:
            if not face.has(ch):
                if ch not in missing:
                    missing.append(ch)
                ch = " "
            x += face.kern(prev, ch) * scale
            if ch == " ":
                adv, gpaths = face.space(), []
            else:
                adv, gpaths = face.glyph(ch)
            for p in gpaths:
                paths.append([(x + gx * scale, base - gy * scale) for gx, gy in p])
            x += adv * scale + tracking
            prev = ch
    paths = [rotate(p, rot, bw, bh) for p in paths]
    rw, rh = (bh, bw) if rot in (90, 270) else (bw, bh)
    return Block(paths, face.kind == "outline", rw, rh, width, height, missing)


def rotate(path, rot, bw, bh):
    """A path of the unrotated box turned clockwise, as seen from above, in the rotated box."""
    if rot == 90:
        return [(bh - y, x) for x, y in path]
    if rot == 180:
        return [(bw - x, bh - y) for x, y in path]
    if rot == 270:
        return [(y, bw - x) for x, y in path]
    return list(path)


def slots(profile):
    """The grid's slots in marking order, row by row from the top-left: [(index, row, col), ...], skipped ones
    left out."""
    grid = profile.get("grid", {})
    cols = max(1, int(grid.get("cols", 1)))
    rows = max(1, int(grid.get("rows", 1)))
    skip = set(grid.get("skip", []))
    return [(r * cols + c, r, c) for r in range(rows) for c in range(cols) if r * cols + c not in skip]


def slot_origin(profile, start, row, col):
    """The top-left of a slot's box on the bed, from the start point."""
    grid = profile.get("grid", {})
    off = profile.get("offset", {})
    return (start[0] + float(off.get("x", 0.0)) + col * float(grid.get("pitch_x", 0.0)),
            start[1] + float(off.get("y", 0.0)) + row * float(grid.get("pitch_y", 0.0)))
