# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""A TrueType reader: outlines, advances, and pair kerning.

Reads the tables a text engraver needs from a TrueType (glyf) font:
cmap (formats 4 and 12), head, hhea, hmtx, maxp, loca, glyf (simple and
composite glyphs), OS/2 for the cap height, and pair kerning from GPOS
(PairPos formats 1 and 2, through extension lookups) or, failing that,
the old kern table. Outlines come out flattened: each contour a list of
(x, y) in font units, y up, closed.
"""
import struct

FLATTEN_UNITS = 4.0     # the most a flattened curve strays from the curve, in font units


class FontError(Exception):
    pass


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def _i16(b, o):
    return struct.unpack_from(">h", b, o)[0]


def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


class Font:
    def __init__(self, data):
        self.data = data
        if len(data) < 12 or _u32(data, 0) not in (0x00010000, 0x74727565):
            raise FontError("not a TrueType font")
        n = _u16(data, 4)
        self.tables = {}
        for i in range(n):
            o = 12 + 16 * i
            tag = data[o:o + 4].decode("latin-1")
            self.tables[tag] = (_u32(data, o + 8), _u32(data, o + 12))
        for need in ("cmap", "head", "hhea", "hmtx", "maxp", "loca", "glyf"):
            if need not in self.tables:
                raise FontError("the font has no %s table" % need)
        head = self._t("head")
        self.units_per_em = _u16(head, 18)
        self._long_loca = _i16(head, 50) == 1
        self.num_glyphs = _u16(self._t("maxp"), 4)
        hhea = self._t("hhea")
        self.ascent = _i16(hhea, 4)
        self.descent = _i16(hhea, 6)
        self.line_gap = _i16(hhea, 8)
        self._num_hmetrics = _u16(hhea, 34)
        self._hmtx = self._t("hmtx")
        self._loca = self._read_loca()
        self._glyf_off = self.tables["glyf"][0]
        self._cmap = self._read_cmap()
        self.cap_height = self._read_cap_height()
        self._outlines = {}
        self._kern = None

    def _t(self, tag):
        off, ln = self.tables[tag]
        return self.data[off:off + ln]

    # --- the character map ---------------------------------------------------

    def _read_cmap(self):
        cmap = self._t("cmap")
        best = None
        for i in range(_u16(cmap, 2)):
            pid, eid, off = struct.unpack_from(">HHI", cmap, 4 + 8 * i)
            fmt = _u16(cmap, off)
            rank = {(3, 10): 0, (0, 4): 1, (0, 3): 2, (3, 1): 3, (0, 1): 4}.get((pid, eid))
            if rank is None or fmt not in (4, 12):
                continue
            if best is None or rank < best[0]:
                best = (rank, off, fmt)
        if best is None:
            raise FontError("the font has no Unicode character map")
        _, off, fmt = best
        m = {}
        if fmt == 4:
            segx2 = _u16(cmap, off + 6)
            ends = off + 14
            starts = ends + segx2 + 2
            deltas = starts + segx2
            ranges = deltas + segx2
            for s in range(segx2 // 2):
                end = _u16(cmap, ends + 2 * s)
                start = _u16(cmap, starts + 2 * s)
                delta = _i16(cmap, deltas + 2 * s)
                ro = _u16(cmap, ranges + 2 * s)
                for c in range(start, min(end, 0xFFFE) + 1):
                    if ro == 0:
                        g = (c + delta) & 0xFFFF
                    else:
                        gi = ranges + 2 * s + ro + 2 * (c - start)
                        g = _u16(cmap, gi)
                        if g:
                            g = (g + delta) & 0xFFFF
                    if g:
                        m[c] = g
        else:
            ngroups = _u32(cmap, off + 12)
            for i in range(ngroups):
                start, end, gid = struct.unpack_from(">III", cmap, off + 16 + 12 * i)
                for c in range(start, min(end, start + 0xFFFF) + 1):
                    m[c] = gid + (c - start)
        return m

    def glyph_id(self, ch):
        return self._cmap.get(ord(ch), 0)

    def has(self, ch):
        return ord(ch) in self._cmap

    # --- metrics -----------------------------------------------------------------

    def advance(self, gid):
        i = min(gid, self._num_hmetrics - 1)
        return _u16(self._hmtx, 4 * i)

    def _read_cap_height(self):
        if "OS/2" in self.tables:
            os2 = self._t("OS/2")
            if _u16(os2, 0) >= 2 and len(os2) >= 90:
                cap = _i16(os2, 88)
                if cap > 0:
                    return cap
        gid = self.glyph_id("H")
        if gid:
            ys = [y for c in self.outline(gid) for _, y in c]
            if ys:
                return max(ys)
        return int(self.units_per_em * 0.7)

    # --- outlines ----------------------------------------------------------------

    def _read_loca(self):
        loca = self._t("loca")
        n = self.num_glyphs + 1
        if self._long_loca:
            return list(struct.unpack_from(">%dI" % n, loca, 0))
        return [2 * v for v in struct.unpack_from(">%dH" % n, loca, 0)]

    def outline(self, gid):
        """The glyph's contours, flattened: [[(x, y), ...], ...], each closed."""
        if gid in self._outlines:
            return self._outlines[gid]
        contours = [self._flatten(pts) for pts in self._glyph_points(gid, 0)]
        self._outlines[gid] = contours
        return contours

    def _glyph_points(self, gid, depth):
        """Contours as lists of (x, y, on_curve), composites resolved."""
        if gid >= self.num_glyphs or depth > 8:
            return []
        start, end = self._loca[gid], self._loca[gid + 1]
        if end <= start:
            return []
        b = self.data
        o = self._glyf_off + start
        ncont = _i16(b, o)
        if ncont >= 0:
            return self._simple(b, o, ncont)
        return self._composite(b, o + 10, depth)

    def _simple(self, b, o, ncont):
        ends = struct.unpack_from(">%dH" % ncont, b, o + 10)
        npts = (ends[-1] + 1) if ncont else 0
        p = o + 10 + 2 * ncont
        ilen = _u16(b, p)
        p += 2 + ilen
        flags = []
        while len(flags) < npts:
            f = b[p]
            p += 1
            flags.append(f)
            if f & 8:
                r = b[p]
                p += 1
                flags.extend([f] * r)
        flags = flags[:npts]
        xs, ys = [], []
        v = 0
        for f in flags:
            if f & 2:
                d = b[p]
                p += 1
                v += d if f & 16 else -d
            elif not f & 16:
                v += _i16(b, p)
                p += 2
            xs.append(v)
        v = 0
        for f in flags:
            if f & 4:
                d = b[p]
                p += 1
                v += d if f & 32 else -d
            elif not f & 32:
                v += _i16(b, p)
                p += 2
            ys.append(v)
        out, s = [], 0
        for e in ends:
            out.append([(xs[i], ys[i], bool(flags[i] & 1)) for i in range(s, e + 1)])
            s = e + 1
        return out

    def _composite(self, b, p, depth):
        out = []
        while True:
            flags = _u16(b, p)
            gid = _u16(b, p + 2)
            p += 4
            if flags & 1:
                a1, a2 = _i16(b, p), _i16(b, p + 2)
                p += 4
            else:
                a1, a2 = struct.unpack_from(">bb", b, p)
                p += 2
            xx, xy, yx, yy = 1.0, 0.0, 0.0, 1.0
            if flags & 8:
                xx = yy = _i16(b, p) / 16384.0
                p += 2
            elif flags & 0x40:
                xx, yy = _i16(b, p) / 16384.0, _i16(b, p + 2) / 16384.0
                p += 4
            elif flags & 0x80:
                xx, xy, yx, yy = (_i16(b, p + 2 * k) / 16384.0 for k in range(4))
                p += 8
            sub = self._glyph_points(gid, depth + 1)
            if flags & 2:
                dx, dy = a1, a2
            else:
                # Point matching: rare in the fonts this reads; place it unmoved.
                dx = dy = 0
            for c in sub:
                out.append([(x * xx + y * yx + dx, x * xy + y * yy + dy, on) for x, y, on in c])
            if not flags & 0x20:
                break
        return out

    @staticmethod
    def _flatten(pts):
        """A contour of on- and off-curve points as a closed polyline."""
        n = len(pts)
        if n == 0:
            return []
        # Start on an on-curve point; if there is none, at the midpoint of the first two.
        k = next((i for i, p in enumerate(pts) if p[2]), None)
        if k is None:
            a, c = pts[0], pts[1 % n]
            start = ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
            seq = pts
        else:
            start = (pts[k][0], pts[k][1])
            seq = pts[k + 1:] + pts[:k + 1]
        out = [start]
        cur = start
        ctrl = None
        for x, y, on in seq:
            if on:
                if ctrl is None:
                    out.append((x, y))
                else:
                    out.extend(_quad(cur, ctrl, (x, y)))
                    ctrl = None
                cur = (x, y)
            else:
                if ctrl is not None:
                    mid = ((ctrl[0] + x) / 2, (ctrl[1] + y) / 2)
                    out.extend(_quad(cur, ctrl, mid))
                    cur = mid
                ctrl = (x, y)
        if ctrl is not None:
            out.extend(_quad(cur, ctrl, start))
        if out[-1] != start:
            out.append(start)
        return out

    # --- kerning -----------------------------------------------------------------

    def kern(self, left, right):
        if self._kern is None:
            self._kern = _Kerning(self)
        return self._kern.value(left, right)


def _quad(p0, p1, p2):
    # The deviation of a quadratic from its chord is at most |p0 - 2 p1 + p2| / 4.
    dx = p0[0] - 2 * p1[0] + p2[0]
    dy = p0[1] - 2 * p1[1] + p2[1]
    dev = (dx * dx + dy * dy) ** 0.5 / 4
    n = max(1, min(32, int((dev / FLATTEN_UNITS) ** 0.5 + 1)))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


class _Kerning:
    """Pair adjustments of the x advance: GPOS PairPos, else kern format 0."""

    def __init__(self, font):
        self.font = font
        self.pairs = {}                 # (left, right) -> value, from format 1 and kern
        self.classed = []               # (coverage, classdef1, classdef2, class2count, values)
        try:
            if "GPOS" in font.tables:
                self._gpos(font._t("GPOS"))
        except (struct.error, IndexError):
            self.pairs, self.classed = {}, []
        if not self.pairs and not self.classed and "kern" in font.tables:
            try:
                self._kern(font._t("kern"))
            except (struct.error, IndexError):
                self.pairs = {}

    def value(self, left, right):
        v = self.pairs.get((left, right))
        if v is not None:
            return v
        for cov, cd1, cd2, n2, vals in self.classed:
            if left not in cov:
                continue
            c1 = cd1.get(left, 0)
            c2 = cd2.get(right, 0)
            return vals[c1 * n2 + c2]
        return 0

    def _kern(self, k):
        n = _u16(k, 2)
        o = 4
        for _ in range(n):
            ln = _u16(k, o + 2)
            cov = _u16(k, o + 4)
            if cov >> 8 == 0 and cov & 1:
                npairs = _u16(k, o + 6)
                for i in range(npairs):
                    l, r, v = struct.unpack_from(">HHh", k, o + 14 + 6 * i)
                    self.pairs[(l, r)] = v
            o += ln

    def _gpos(self, g):
        lookups_off = _u16(g, 8)
        feats_off = _u16(g, 6)
        # The lookups the 'kern' feature names; all pair lookups if no feature says.
        wanted = set()
        nfeat = _u16(g, feats_off)
        for i in range(nfeat):
            tag = g[feats_off + 2 + 6 * i:feats_off + 6 + 6 * i]
            if tag == b"kern":
                fo = feats_off + _u16(g, feats_off + 6 + 6 * i)
                for j in range(_u16(g, fo + 2)):
                    wanted.add(_u16(g, fo + 4 + 2 * j))
        nlook = _u16(g, lookups_off)
        for li in range(nlook):
            if wanted and li not in wanted:
                continue
            lo = lookups_off + _u16(g, lookups_off + 2 + 2 * li)
            ltype = _u16(g, lo)
            nsub = _u16(g, lo + 4)
            for si in range(nsub):
                so = lo + _u16(g, lo + 6 + 2 * si)
                t = ltype
                if t == 9:
                    t = _u16(g, so + 2)
                    so = so + _u32(g, so + 4)
                if t == 2:
                    self._pairpos(g, so)

    @staticmethod
    def _value_size(fmt):
        return 2 * bin(fmt & 0xFF).count("1")

    @staticmethod
    def _x_advance(g, o, fmt):
        # The fields come in bit order; x advance is bit 2 (0x0004).
        if not fmt & 4:
            return 0
        skip = bin(fmt & 3).count("1")
        return _i16(g, o + 2 * skip)

    def _pairpos(self, g, so):
        fmt = _u16(g, so)
        cov = _coverage(g, so + _u16(g, so + 2))
        vf1, vf2 = _u16(g, so + 4), _u16(g, so + 6)
        s1, s2 = self._value_size(vf1), self._value_size(vf2)
        if fmt == 1:
            nsets = _u16(g, so + 8)
            for i in range(nsets):
                if i >= len(cov[1]):
                    break
                left = cov[1][i]
                po = so + _u16(g, so + 10 + 2 * i)
                npv = _u16(g, po)
                rec = 2 + s1 + s2
                for j in range(npv):
                    ro = po + 2 + rec * j
                    right = _u16(g, ro)
                    v = self._x_advance(g, ro + 2, vf1)
                    if v and (left, right) not in self.pairs:
                        self.pairs[(left, right)] = v
        elif fmt == 2:
            cd1 = _classdef(g, so + _u16(g, so + 8))
            cd2 = _classdef(g, so + _u16(g, so + 10))
            n1, n2 = _u16(g, so + 12), _u16(g, so + 14)
            rec = s1 + s2
            vals = []
            for c1 in range(n1):
                for c2 in range(n2):
                    vals.append(self._x_advance(g, so + 16 + rec * (c1 * n2 + c2), vf1))
            self.classed.append((cov[0], cd1, cd2, n2, vals))


def _coverage(g, o):
    """(set of glyphs, glyphs in coverage order)."""
    fmt = _u16(g, o)
    order = []
    if fmt == 1:
        n = _u16(g, o + 2)
        order = list(struct.unpack_from(">%dH" % n, g, o + 4))
    else:
        n = _u16(g, o + 2)
        for i in range(n):
            start, end, _ = struct.unpack_from(">HHH", g, o + 4 + 6 * i)
            order.extend(range(start, end + 1))
    return set(order), order


def _classdef(g, o):
    fmt = _u16(g, o)
    m = {}
    if fmt == 1:
        start = _u16(g, o + 2)
        n = _u16(g, o + 4)
        for i in range(n):
            c = _u16(g, o + 6 + 2 * i)
            if c:
                m[start + i] = c
    elif fmt == 2:
        n = _u16(g, o + 2)
        for i in range(n):
            s, e, c = struct.unpack_from(">HHH", g, o + 4 + 6 * i)
            if c:
                for gid in range(s, e + 1):
                    m[gid] = c
    return m
