# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""Filling outlines with lines.

The shapes are filled with lines along the bed's X axis, one every
`interval` millimeters, at the power the operator set. A point is inside
when the outlines wind around it (the nonzero rule, the one TrueType
glyphs are drawn by), so a letter's counters stay open and overlapping
letters fill once. The rows sit on one grid for the whole bed (y = (k +
1/2) x interval), so neighboring letters and lines share their rows.
"""
import math


def rows(contours, interval):
    """[(y, [(x0, x1), ...]), ...]: each row's spans, left to right, rows front-most last."""
    if interval <= 0:
        raise ValueError("the line interval is more than 0")
    edges = []
    for c in contours:
        for i in range(len(c) - 1):
            (x0, y0), (x1, y1) = c[i], c[i + 1]
            if y0 == y1:
                continue
            if y0 < y1:
                edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0), 1))
            else:
                edges.append((y1, y0, x1, (x0 - x1) / (y0 - y1), -1))
    if not edges:
        return []
    edges.sort()
    ymin = edges[0][0]
    ymax = max(e[1] for e in edges)
    k = math.ceil(ymin / interval - 0.5)
    out, active, nxt = [], [], 0
    while True:
        y = (k + 0.5) * interval
        if y >= ymax:
            break
        while nxt < len(edges) and edges[nxt][0] <= y:
            active.append(edges[nxt])
            nxt += 1
        active = [e for e in active if e[1] > y]
        if active:
            xs = sorted((e[2] + (y - e[0]) * e[3], e[4]) for e in active)
            spans, w, start = [], 0, 0.0
            for x, d in xs:
                was = w
                w += d
                if was == 0 and w != 0:
                    start = x
                elif was != 0 and w == 0:
                    if spans and start - spans[-1][1] < 1e-6:
                        spans[-1] = (spans[-1][0], max(x, spans[-1][1]))
                    elif x - start > 1e-6:
                        spans.append((start, x))
            if spans:
                out.append((y, spans))
        k += 1
    return out
