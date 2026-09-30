# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The text template: counters, dates, codes, and check digits.

A template is text with fields in braces: {Serial} is a counter the
operator named, {Year} a part of the date, {MonthCode} a code of the
operator's own, and {Luhn} a check digit. {{ and }} are braces as text.

A counter holds the next value to use. Values are kept as text, the way
the operator reads them; each counter's alphabet turns them into numbers
and back. A counter advances for each item, once for each cycle, or when
another counter starts over (it carries into it). At its last value it
either stops the run or starts over at its first.
"""
import datetime
import re

ALPHABETS = {
    "dec": "0123456789",
    "alpha": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "alnum": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "hex": "0123456789ABCDEF",
}
LOOKALIKES = "IOQ"

DATE_FIELDS = ["Year", "Year2", "Year1", "Month", "MonthName", "Day", "DayOfYear", "Week", "WeekYear",
               "WeekYear2", "Weekday", "WeekdayName", "Hour", "Minute"]
CHECKS = ["Luhn", "GS1", "Mod11", "Mod36"]
CODE_OF = {"year": None, "month": 12, "day": 31, "weekday": 7, "hour": 24, "week": 53}
RESERVED = {n.lower() for n in DATE_FIELDS + CHECKS}
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,23}$")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MAX_WIDTH = 16


class TemplateError(Exception):
    """A template, a counter, or a code that cannot be used, in words for the operator."""


# --- counters -------------------------------------------------------------------


def alphabet(c):
    a = c.get("alphabet", "") if c.get("type") == "custom" else ALPHABETS.get(c.get("type"), "")
    if c.get("no_lookalikes") and c.get("type") in ("alpha", "alnum"):
        a = "".join(ch for ch in a if ch not in LOOKALIKES)
    return a


def to_number(c, text):
    """A counter's value, as text, to its number; TemplateError if the text is not in its alphabet."""
    a = alphabet(c)
    if not text:
        raise TemplateError("%s has no value" % c["name"])
    n = 0
    for ch in text.upper() if c.get("type") != "custom" else text:
        i = a.find(ch)
        if i < 0:
            raise TemplateError("%s: '%s' is not one of its characters (%s)" % (c["name"], ch, a))
        n = n * len(a) + i
    return n


def to_text(c, n):
    a = alphabet(c)
    base = len(a)
    digits = []
    while n > 0:
        n, r = divmod(n, base)
        digits.append(a[r])
    s = "".join(reversed(digits)) or a[0]
    width = int(c.get("width") or 0)
    if len(s) < width:
        s = a[0] * (width - len(s)) + s
    return s


def bounds(c):
    """(first, last) as numbers. The last is the counter's own, or the most its width holds."""
    a = alphabet(c)
    width = int(c.get("width") or 0)
    first = to_number(c, c.get("first") or a[0])
    if c.get("last"):
        last = to_number(c, c["last"])
    elif width:
        last = len(a) ** width - 1
    else:
        last = len(a) ** MAX_WIDTH - 1
    return first, last


def check_counter(c, others):
    """TemplateError for a counter definition that cannot work."""
    name = c.get("name", "")
    if not NAME.match(name):
        raise TemplateError("A counter's name is a letter, then letters or digits (\"%s\")" % name)
    if name.lower() in RESERVED:
        raise TemplateError("%s is the name of a date field or a check digit; name the counter something else" % name)
    if sum(1 for o in others if o.get("name", "").lower() == name.lower()) > 1:
        raise TemplateError("Two counters are named %s" % name)
    a = alphabet(c)
    if c.get("type") not in ALPHABETS and c.get("type") != "custom":
        raise TemplateError("%s: pick what it counts in" % name)
    if len(a) < 2:
        raise TemplateError("%s: its characters need two at least" % name)
    if len(set(a)) != len(a):
        raise TemplateError("%s: a character is in its list twice" % name)
    if any(ch in "{}+" or not ch.isprintable() or ch.isspace() for ch in a):
        raise TemplateError("%s: its characters are letters, digits, and signs other than + and braces, "
                            "with no spaces" % name)
    width = int(c.get("width") or 0)
    if not 0 <= width <= MAX_WIDTH:
        raise TemplateError("%s: its width is 0 to %d characters" % (name, MAX_WIDTH))
    step = c.get("step", 1)
    if not isinstance(step, int) or step < 1:
        raise TemplateError("%s: its step is a whole number, 1 or more" % name)
    first, last = bounds(c)
    if last < first:
        raise TemplateError("%s: its last value is before its first" % name)
    next_number(c)
    if c.get("advance") == "carry":
        src = c.get("carry_from")
        if not any(o.get("name") == src and o is not c for o in others):
            raise TemplateError("%s advances when another counter starts over: pick which" % name)
        seen, cur = {name}, src
        while cur:
            if cur in seen:
                raise TemplateError("%s: the counters carry into each other in a circle" % name)
            seen.add(cur)
            nxt = next((o for o in others if o.get("name") == cur), None)
            cur = nxt.get("carry_from") if nxt and nxt.get("advance") == "carry" else None


class Counters:
    """The counters' values while a cycle is laid out; what they will be after it."""

    def __init__(self, defs):
        for c in defs:
            check_counter(c, defs)
        self.defs = {c["name"]: c for c in defs}
        self.values = {c["name"]: next_number(c) for c in defs}

    def text(self, name):
        c = self.defs[name]
        first, last = bounds(c)
        v = self.values[name]
        if v > last:
            raise TemplateError("%s is past its last value (%s). Set its next value to go on." % (
                name, to_text(c, last)))
        return to_text(c, v)

    def advance(self, name, depth=0):
        c = self.defs[name]
        first, last = bounds(c)
        v = self.values[name] + int(c.get("step", 1))
        if v > last:
            if c.get("at_end", "stop") == "wrap":
                v = first
                for other in self.defs.values():
                    if other.get("advance") == "carry" and other.get("carry_from") == name and depth < 16:
                        self.advance(other["name"], depth + 1)
            else:
                v = last + 1                   # past the end: the next use says so
        self.values[name] = v

    def item_done(self):
        for name, c in self.defs.items():
            if c.get("advance", "item") == "item":
                self.advance(name)

    def cycle_done(self):
        for name, c in self.defs.items():
            if c.get("advance") == "cycle":
                self.advance(name)

    def snapshot(self):
        """The counters' values as text, for the profile: {name: next}."""
        out = {}
        for name, c in self.defs.items():
            first, last = bounds(c)
            v = self.values[name]
            out[name] = to_text(c, v) if v <= last else to_text(c, last) + "+"
        return out


def next_number(c):
    """The counter's next value as a number. A counter past its end is stored as its last value and a "+"
    (snapshot()), which reads as one past the last."""
    text = c.get("next") or c.get("first") or alphabet(c)[:1]
    if text.endswith("+"):
        return to_number(c, text[:-1]) + 1
    return to_number(c, text)


# --- dates ------------------------------------------------------------------------


def date_value(field, t):
    iso = t.isocalendar()
    return {
        "Year": "%04d" % t.year,
        "Year2": "%02d" % (t.year % 100),
        "Year1": "%d" % (t.year % 10),
        "Month": "%02d" % t.month,
        "MonthName": MONTHS[t.month - 1],
        "Day": "%02d" % t.day,
        "DayOfYear": "%03d" % t.timetuple().tm_yday,
        "Week": "%02d" % iso[1],
        "WeekYear": "%04d" % iso[0],
        "WeekYear2": "%02d" % (iso[0] % 100),
        "Weekday": "%d" % iso[2],
        "WeekdayName": DAYS[iso[2] - 1],
        "Hour": "%02d" % t.hour,
        "Minute": "%02d" % t.minute,
    }[field]


def check_code(code, others):
    name = code.get("name", "")
    if not NAME.match(name):
        raise TemplateError("A code's name is a letter, then letters or digits (\"%s\")" % name)
    if name.lower() in RESERVED:
        raise TemplateError("%s is the name of a date field or a check digit; name the code something else" % name)
    of = code.get("of")
    if of not in CODE_OF:
        raise TemplateError("%s: pick the part of the date it codes" % name)
    vals = code.get("values") or []
    if not vals:
        raise TemplateError("%s has no codes yet" % name)
    if any(not isinstance(v, str) or "{" in v or "}" in v or len(v) > 8 for v in vals):
        raise TemplateError("%s: each code is up to 8 characters, with no braces" % name)
    if of == "year" and not isinstance(code.get("base_year"), int):
        raise TemplateError("%s: say the year its first code is for" % name)


def code_value(code, t):
    of = code["of"]
    vals = code["values"]
    if of == "year":
        i = t.year - code["base_year"]
        what = "year %d" % t.year
    elif of == "month":
        i, what = t.month - 1, "month %d" % t.month
    elif of == "day":
        i, what = t.day - 1, "day %d" % t.day
    elif of == "weekday":
        i, what = t.isocalendar()[2] - 1, DAYS[t.isocalendar()[2] - 1]
    elif of == "hour":
        i, what = t.hour, "hour %d" % t.hour
    else:
        i, what = t.isocalendar()[1] - 1, "week %d" % t.isocalendar()[1]
    if not 0 <= i < len(vals) or vals[i] == "":
        raise TemplateError("%s has no code for %s" % (code["name"], what))
    return vals[i]


# --- check digits -------------------------------------------------------------------


def check_digit(kind, text):
    """The check character for the letters and digits of `text` (digits only for the digit schemes)."""
    if kind == "Mod36":
        chars = [c for c in text.upper() if c.isascii() and c.isalnum()]
        if not chars:
            raise TemplateError("{Mod36} has no letters or digits before it on its line")
        a = ALPHABETS["alnum"]
        p = 36
        for ch in chars:
            s = (p + a.index(ch)) % 36
            if s == 0:
                s = 36
            p = (s * 2) % 37
        return a[(37 - p) % 36]
    digits = [int(c) for c in text if c.isascii() and c.isdigit()]
    if not digits:
        raise TemplateError("{%s} has no digits before it on its line" % kind)
    if kind == "Luhn":
        total = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 0:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return str((10 - total % 10) % 10)
    if kind == "GS1":
        total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
        return str((10 - total % 10) % 10)
    if kind == "Mod11":
        total = sum(d * (2 + i % 6) for i, d in enumerate(reversed(digits)))
        v = (11 - total % 11) % 11
        return "X" if v == 10 else str(v)
    raise TemplateError("{%s} is not a check digit" % kind)


# --- the template ---------------------------------------------------------------------

TOKEN = re.compile(r"\{\{|\}\}|\{([^{}]*)\}|[{}]")


def parse(template, counters, codes):
    """The template as lines of parts: ("text", s), ("counter", name), ("date", field), ("code", name),
    ("check", kind). TemplateError names the first field that is not known."""
    cnames = {c["name"].lower(): c["name"] for c in counters}
    knames = {c["name"].lower(): c["name"] for c in codes}
    dates = {f.lower(): f for f in DATE_FIELDS}
    checks = {k.lower(): k for k in CHECKS}
    lines = []
    for raw in template.split("\n"):
        parts = []
        pos = 0
        for m in TOKEN.finditer(raw):
            if m.start() > pos:
                parts.append(("text", raw[pos:m.start()]))
            tok = m.group(0)
            if tok == "{{":
                parts.append(("text", "{"))
            elif tok == "}}":
                parts.append(("text", "}"))
            elif tok in "{}":
                raise TemplateError("A brace with no field: write {{ or }} for a brace as text")
            else:
                key = m.group(1).strip().lower()
                if key in cnames:
                    parts.append(("counter", cnames[key]))
                elif key in knames:
                    parts.append(("code", knames[key]))
                elif key in dates:
                    parts.append(("date", dates[key]))
                elif key in checks:
                    parts.append(("check", checks[key]))
                else:
                    raise TemplateError("{%s} is not a counter, a date field, a code, or a check digit" % m.group(1))
            pos = m.end()
        if pos < len(raw):
            parts.append(("text", raw[pos:]))
        lines.append(parts)
    return lines


def render(parsed, counters, codes, t):
    """One item's lines of text, from the counters' current values and the time t."""
    kmap = {c["name"]: c for c in codes}
    out = []
    for parts in parsed:
        s = ""
        for kind, v in parts:
            if kind == "text":
                s += v
            elif kind == "counter":
                s += counters.text(v)
            elif kind == "date":
                s += date_value(v, t)
            elif kind == "code":
                s += code_value(kmap[v], t)
            else:
                s += check_digit(v, s)
        out.append(s)
    return out


class Clock:
    """Local time from the operator's browser, carried by a steady clock: the machine has no date of its own.

    epoch_ms is the browser's time when `mono` was read, and tz_min its offset from UTC in minutes."""

    def __init__(self, epoch_ms, tz_min, mono):
        self.epoch_ms = float(epoch_ms)
        self.tz_min = int(tz_min)
        self.mono = mono

    def at(self, mono_now):
        s = self.epoch_ms / 1000.0 + (mono_now - self.mono) + self.tz_min * 60
        return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=s)


def layout_cycle(profile, n_items, t):
    """The text of each of n_items and the counters as they stand after them.

    Returns (texts, before, after): texts a list of line lists, before and after {name: next value}."""
    counters_def = [dict(c) for c in profile.get("counters", [])]
    codes = profile.get("codes", [])
    for code in codes:
        check_code(code, codes)
    if len({c["name"].lower() for c in codes} | {c["name"].lower() for c in counters_def}) != \
            len(codes) + len(counters_def):
        raise TemplateError("A code and a counter share a name")
    parsed = parse(profile.get("text", ""), counters_def, codes)
    counters = Counters(counters_def)
    before = counters.snapshot()
    texts = []
    for _ in range(n_items):
        texts.append(render(parsed, counters, codes, t))
        counters.item_done()
    counters.cycle_done()
    return texts, before, counters.snapshot()
