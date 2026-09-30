# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The engine's tests: counters, dates, codes, check digits, fonts, fills, and programs."""
import copy
import datetime
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, HERE)

from serializer import fill, fonts, gcode, serials, store, text  # noqa: E402
import replay  # noqa: E402

CAT = fonts.Catalog(os.path.join(ROOT, "share"))
T = datetime.datetime(2026, 9, 30, 14, 5)


def counter(**kw):
    c = copy.deepcopy(store.COUNTER_DEFAULT)
    c.update(kw)
    return c


def profile(**kw):
    p = store.clean({})
    p.update(kw)
    return p


class Counters(unittest.TestCase):
    def texts(self, counters, template, n, t=T, codes=()):
        p = profile(text=template, counters=counters, codes=list(codes))
        return serials.layout_cycle(p, n, t)

    def test_sequential_decimal(self):
        texts, before, after = self.texts([counter(name="SN", width=6, next="100000")], "{SN}", 3)
        self.assertEqual([t[0] for t in texts], ["100000", "100001", "100002"])
        self.assertEqual(before, {"SN": "100000"})
        self.assertEqual(after, {"SN": "100003"})

    def test_step_and_padding(self):
        texts, _, after = self.texts([counter(name="N", width=4, next="0005", step=5)], "N{N}", 2)
        self.assertEqual([t[0] for t in texts], ["N0005", "N0010"])
        self.assertEqual(after["N"], "0015")

    def test_letters_and_lookalikes(self):
        c = counter(name="L", type="alpha", width=2, first="AA", next="AH", no_lookalikes=True)
        texts, _, after = self.texts([c], "{L}", 3)
        self.assertEqual([t[0] for t in texts], ["AH", "AJ", "AK"])       # no I
        self.assertEqual(after["L"], "AL")

    def test_custom_alphabet(self):
        c = counter(name="C", type="custom", alphabet="0123456789ACEFHJKLMNPRTUVWXY", width=3, first="000",
                    next="00Y")
        texts, _, _ = self.texts([c], "{C}", 2)
        self.assertEqual([t[0] for t in texts], ["00Y", "010"])

    def test_odometer_carry(self):
        digits = counter(name="D", width=2, first="00", next="98", at_end="wrap")
        letter = counter(name="L", type="alpha", width=1, first="A", next="A", advance="carry", carry_from="D")
        texts, _, after = self.texts([digits, letter], "1{L}-{D}", 4)
        self.assertEqual([t[0] for t in texts], ["1A-98", "1A-99", "1B-00", "1B-01"])
        self.assertEqual(after, {"D": "02", "L": "B"})

    def test_per_cycle_counter(self):
        sn = counter(name="SN", width=3, next="001")
        lot = counter(name="Lot", width=2, next="07", advance="cycle")
        texts, _, after = self.texts([sn, lot], "{Lot}-{SN}", 3)
        self.assertEqual([t[0] for t in texts], ["07-001", "07-002", "07-003"])
        self.assertEqual(after, {"SN": "004", "Lot": "08"})

    def test_stop_at_last(self):
        c = counter(name="SN", width=3, next="998", last="999")
        texts, _, after = self.texts([c], "{SN}", 2)
        self.assertEqual([t[0] for t in texts], ["998", "999"])
        self.assertEqual(after["SN"], "999+")
        with self.assertRaises(serials.TemplateError) as e:
            self.texts([counter(name="SN", width=3, next=after["SN"], last="999")], "{SN}", 1)
        self.assertIn("past its last value", str(e.exception))

    def test_running_out_mid_cycle(self):
        with self.assertRaises(serials.TemplateError):
            self.texts([counter(name="SN", width=3, next="999")], "{SN}", 2)

    def test_bad_definitions(self):
        for c, words in ((counter(name="Year"), "name of a date field"),
                         (counter(name="9x"), "letter, then"),
                         (counter(name="A", type="custom", alphabet="AA"), "twice"),
                         (counter(name="A", type="custom", alphabet="A+"), "other than +"),
                         (counter(name="A", advance="carry", carry_from="B"), "pick which"),
                         (counter(name="A", width=2, next="1x"), "not one of its characters")):
            with self.assertRaises(serials.TemplateError) as e:
                self.texts([c], "{%s}" % c["name"] if c["name"][0].isalpha() else "x", 1)
            self.assertIn(words, str(e.exception))

    def test_carry_circle(self):
        a = counter(name="A", advance="carry", carry_from="B", at_end="wrap")
        b = counter(name="B", advance="carry", carry_from="A", at_end="wrap")
        with self.assertRaises(serials.TemplateError) as e:
            self.texts([a, b], "{A}{B}", 1)
        self.assertIn("circle", str(e.exception))

    def test_unknown_field_and_braces(self):
        with self.assertRaises(serials.TemplateError) as e:
            self.texts([], "{Nope}", 1)
        self.assertIn("{Nope}", str(e.exception))
        texts, _, _ = self.texts([], "{{literal}}", 1)
        self.assertEqual(texts[0], ["{literal}"])
        with self.assertRaises(serials.TemplateError):
            self.texts([], "a { b", 1)


class Dates(unittest.TestCase):
    def one(self, template, t, codes=()):
        return serials.layout_cycle(profile(text=template, counters=[], codes=list(codes)), 1, t)[0][0][0]

    def test_fields(self):
        self.assertEqual(self.one("{Year}{Year2}{Year1}-{Month}{MonthName}-{Day}-{DayOfYear}", T),
                         "2026266-09Sep-30-273")
        self.assertEqual(self.one("{Week}{Weekday}{WeekdayName}{Hour}{Minute}", T), "403Wed1405")

    def test_iso_week_year_at_new_year(self):
        t = datetime.datetime(2026, 12, 31)                 # a Thursday: ISO week 53 of 2026
        self.assertEqual(self.one("{WeekYear2}{Week}", t), "2653")
        t = datetime.datetime(2027, 1, 1)                   # a Friday: still week 53 of 2026
        self.assertEqual(self.one("{Year2}|{WeekYear2}{Week}", t), "27|2653")

    def test_codes(self):
        month = {"name": "M", "of": "month", "values": list("ABCDEFGHJKLM"), "base_year": 2026}
        year = {"name": "Y", "of": "year", "values": ["S", "T", "U"], "base_year": 2025}
        wd = {"name": "W", "of": "weekday", "values": ["1", "2", "3", "4", "5", "6", "7"], "base_year": 2026}
        self.assertEqual(self.one("{Y}{M}{W}", T, [month, year, wd]), "TJ3")
        with self.assertRaises(serials.TemplateError) as e:
            self.one("{Y}", datetime.datetime(2030, 1, 1), [year])
        self.assertIn("no code for year 2030", str(e.exception))

    def test_clock_carries_the_browser_time(self):
        epoch = datetime.datetime(2026, 9, 30, 21, 0) - datetime.datetime(1970, 1, 1)
        c = serials.Clock(epoch.total_seconds() * 1000, -420, 500.0)   # UTC-7
        self.assertEqual(c.at(500.0), datetime.datetime(2026, 9, 30, 14, 0))
        self.assertEqual(c.at(500.0 + 3600), datetime.datetime(2026, 9, 30, 15, 0))


class Checks(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(serials.check_digit("Luhn", "7992739871"), "3")
        self.assertEqual(serials.check_digit("GS1", "400638133393"), "1")      # EAN-13 4006381333931
        # Weights 2 to 7 from the right: 5*2+1*3+6*4+0*5+4*6+6*7+0*2+3*3+0*4 = 112; 11 - 112 % 11 = 9.
        self.assertEqual(serials.check_digit("Mod11", "030640615"), "9")
        self.assertEqual(serials.check_digit("Mod11", "6"), "X")               # 6*2 = 12; 11 - 1 = 10, written X
        self.assertEqual(serials.check_digit("Mod36", "ABCD1234"), serials.check_digit("Mod36", "abcd-1234"))

    def test_mod36_detects_a_swap(self):
        a = serials.check_digit("Mod36", "SN12AB")
        b = serials.check_digit("Mod36", "SN21AB")
        self.assertNotEqual(a, b)

    def test_in_template(self):
        texts, _, _ = serials.layout_cycle(profile(text="SN {SN}-{Luhn}", counters=[counter(name="SN", next="000001")]),
                                           1, T)
        self.assertEqual(texts[0][0], "SN 000001-" + serials.check_digit("Luhn", "000001"))

    def test_nothing_to_check(self):
        with self.assertRaises(serials.TemplateError):
            serials.check_digit("Luhn", "ABC")


class Fonts(unittest.TestCase):
    def test_every_font_loads_and_draws(self):
        self.assertGreaterEqual(len(CAT.faces), 40)
        for face in CAT.faces.values():
            adv, paths = face.glyph("8")
            self.assertGreater(adv, 0, face.id)
            self.assertTrue(paths, face.id)
            self.assertGreater(face.cap_height, 0, face.id)
            if face.kind == "outline":
                for c in paths:
                    self.assertEqual(c[0], c[-1], "%s: a contour that is not closed" % face.id)

    def test_composite_glyph(self):
        face = CAT.get("roboto-400")
        self.assertTrue(face.has("Ä"))
        _, paths = face.glyph("Ä")
        self.assertGreaterEqual(len(paths), 3)            # the A and its two dots

    def test_kerning(self):
        face = CAT.get("roboto-400")
        self.assertLess(face.kern("A", "V"), 0)


class Fill(unittest.TestCase):
    def test_square_with_a_hole(self):
        outer = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        inner = [(3, 3), (3, 7), (7, 7), (7, 3), (3, 3)]        # the other way round: a hole
        rows = fill.rows([outer, inner], 1.0)
        self.assertEqual(len(rows), 10)
        by_y = dict(rows)
        self.assertEqual(by_y[0.5], [(0.0, 10.0)])
        self.assertEqual(by_y[5.5], [(0.0, 3.0), (7.0, 10.0)])

    def test_overlap_fills_once(self):
        a = [(0, 0), (6, 0), (6, 2), (0, 2), (0, 0)]
        b = [(4, 0), (10, 0), (10, 2), (4, 2), (4, 0)]
        self.assertEqual(dict(fill.rows([a, b], 1.0))[0.5], [(0.0, 10.0)])


class Layout(unittest.TestCase):
    def test_alignment_in_the_box(self):
        face = CAT.get("hershey-sans")
        p = profile(size_mm=5.0, box={"w": 60, "h": 20, "halign": "right", "valign": "bottom"})
        b = text.lay_out(face, ["ABC"], p)
        x0, y0, x1, y1 = b.ink()
        self.assertAlmostEqual(y1, 20.0, delta=0.05)               # the baseline sits on the box's bottom
        self.assertLess(x1, 60.0 + 0.01)
        self.assertGreater(x1, 58.0)
        p["box"]["halign"] = "center"
        p["box"]["valign"] = "middle"
        b = text.lay_out(face, ["ABC"], p)
        x0, y0, x1, y1 = b.ink()
        self.assertAlmostEqual((y0 + y1) / 2, 10.0, delta=0.1)

    def test_rotation_turns_the_box(self):
        face = CAT.get("hershey-sans")
        p = profile(size_mm=5.0, box={"w": 40, "h": 10, "halign": "left", "valign": "top"}, rotation=90)
        b = text.lay_out(face, ["AAAA"], p)
        self.assertEqual((b.box_w, b.box_h), (10, 40))
        x0, y0, x1, y1 = b.ink()
        self.assertGreater(y1 - y0, x1 - x0)                       # reads down the bed
        self.assertGreater(x0, 4.0)                                # the text's top is at the box's right

    def test_grid_slots_and_skips(self):
        p = profile(grid={"cols": 3, "rows": 2, "pitch_x": 30, "pitch_y": 20, "skip": [1, 5]},
                    offset={"x": 5, "y": 7})
        self.assertEqual([i for i, _, _ in text.slots(p)], [0, 2, 3, 4])
        self.assertEqual(text.slot_origin(p, (100, 50), 1, 2), (165.0, 77.0))


class Programs(unittest.TestCase):
    def build(self, **kw):
        p = profile(text="SN {SN}", counters=[counter(name="SN", width=4, next="0042")], **kw)
        face = CAT.get(p["font"])
        n = len(text.slots(p))
        texts, _, _ = serials.layout_cycle(p, n, T)
        return p, face, gcode.build(p, face, texts, (100.0, 60.0), "Test profile ?!~")

    def test_the_press_comes_before_any_move(self):
        p, face, (prog, info) = self.build(z=4.5, tray="out")
        moves, notes = replay.play(prog)
        self.assertIsNotNone(notes["arm_line"])
        self.assertLess(notes["arm_line"], notes["first_motion"])
        self.assertEqual(notes["z"], 4.5)
        self.assertEqual(notes["tray"], "out")
        self.assertTrue(prog.rstrip().endswith("M2"))
        last = moves[-1]
        self.assertEqual((last.x1, last.y1), (100.0, 60.0))            # back to the start point

    def test_the_machine_takes_it(self):
        import fake
        for font in ("hershey-sans", "roboto-400"):
            _, _, (prog, _) = self.build(font=font)
            fake.check_program(prog)                                    # the job runner's own rules

    def test_strokes_land_where_laid_out(self):
        p, face, (prog, info) = self.build(font="hershey-sans", grid={"cols": 2, "rows": 2, "pitch_x": 45,
                                                                       "pitch_y": 20, "skip": []})
        moves, _ = replay.play(prog)
        lit = [m for m in moves if m.lit]
        ends = {(round(m.x0, 2), round(m.y0, 2)) for m in lit} | {(round(m.x1, 2), round(m.y1, 2)) for m in lit}
        want, segs = [], 0
        for _, ox, oy, block in info["blocks"]:
            for path in block.moved(ox, oy):
                want.extend(path)
                segs += len(path) - 1
        self.assertLessEqual(len(lit), segs)                  # a segment shorter than 0.01 mm is dropped
        self.assertGreater(len(lit), segs * 0.95)
        for x, y in want:
            self.assertTrue(any(abs(x - a) < 0.006 and abs(y - b) < 0.006 for a, b in ends), (x, y))

    def test_fill_stays_inside_and_covers(self):
        p, face, (prog, info) = self.build(font="roboto-700", style="fill", size_mm=6.0,
                                           laser={"line": {"power": 20, "speed": 1200, "passes": 1},
                                                  "fill": {"power": 40, "speed": 3000, "interval": 0.2,
                                                           "passes": 1}})
        moves, _ = replay.play(prog)
        lit = [m for m in moves if m.lit]
        self.assertTrue(lit)
        self.assertTrue(all(m.y0 == m.y1 for m in lit))                  # fill lines run along X
        self.assertTrue(all(m.s == 400 and m.f == 3000 for m in lit))
        rows = fill.rows(info["blocks"][0][3].moved(info["blocks"][0][1], info["blocks"][0][2]), 0.2)
        want = sum(b - a for _, spans in rows for a, b in spans)
        got = sum(abs(m.x1 - m.x0) for m in lit)
        self.assertAlmostEqual(got, want, delta=0.01 * len(lit) + 0.1)

    def test_outside_the_work_area(self):
        p = profile(text="WIDE", box={"w": 40, "h": 10}, offset={"x": 480, "y": 0})
        face = CAT.get(p["font"])
        prog, info = gcode.build(p, face, [["WIDE"]], (10.0, 10.0))
        self.assertIn("past the right edge", gcode.problems(info)[0])

    def test_program_size_is_reported(self):
        p, face, (prog, info) = self.build()
        self.assertEqual(info["bytes"], len(prog))
        info = dict(info, bytes=gcode.PROGRAM_MAX + 1)
        self.assertIn("2 MB at most", gcode.problems(info)[0])

    def test_the_estimate_comes_close_to_the_build(self):
        for font, style in (("hershey-sans", "fill"), ("roboto-700", "fill"), ("roboto-700", "fill_outline")):
            p = profile(text="SN {SN}\n{Year}-{Month}-{Day}", font=font, style=style, size_mm=4.0,
                        counters=[counter(name="SN", width=6, next="000001")],
                        grid={"cols": 4, "rows": 3, "pitch_x": 50, "pitch_y": 20, "skip": [5]})
            face = CAT.get(font)
            texts, _, _ = serials.layout_cycle(p, 11, T)
            _, full = gcode.build(p, face, texts, (60.0, 40.0))
            est = gcode.estimate(p, face, texts, (60.0, 40.0))
            self.assertEqual(est["bounds"], full["bounds"])
            self.assertAlmostEqual(est["bytes"], full["bytes"], delta=full["bytes"] * 0.05)
            self.assertAlmostEqual(est["seconds"], full["seconds"], delta=full["seconds"] * 0.1)

    def test_numbers_are_exact(self):
        self.assertEqual(gcode._num(0), "0")
        self.assertEqual(gcode._num(5), "0.05")
        self.assertEqual(gcode._num(-5), "-0.05")
        self.assertEqual(gcode._num(12340), "123.4")
        self.assertEqual(gcode._num(-100), "-1")


class Profiles(unittest.TestCase):
    def test_clean_holds_everything_to_its_form(self):
        p = store.clean({"size_mm": "huge", "rotation": 45, "grid": {"cols": 999, "rows": -3, "skip": [0, 5000]},
                         "laser": {"fill": {"power": 250, "interval": 0}}, "notes": "a\x00b" + "x" * 5000,
                         "counters": [{"name": "SN", "type": "bogus", "width": 99}] * 20})
        self.assertEqual(p["size_mm"], store.DEFAULT["size_mm"])
        self.assertEqual(p["rotation"], 0)
        self.assertEqual((p["grid"]["cols"], p["grid"]["rows"]), (50, 1))
        self.assertEqual(p["grid"]["skip"], [0])
        self.assertEqual(p["laser"]["fill"]["power"], 100.0)
        self.assertEqual(p["laser"]["fill"]["interval"], 0.02)
        self.assertEqual(len(p["notes"]), store.NOTES_MAX)
        self.assertNotIn("\x00", p["notes"])
        self.assertEqual(len(p["counters"]), store.COUNTERS_MAX)
        self.assertEqual(p["counters"][0]["type"], "dec")
        self.assertEqual(p["counters"][0]["width"], serials.MAX_WIDTH)

    def test_example_profile_builds(self):
        p = store.example()
        face = CAT.get(p["font"])
        texts, _, _ = serials.layout_cycle(p, 1, T)
        prog, info = gcode.build(p, face, texts, (50.0, 50.0))
        self.assertEqual(gcode.problems(info), [])


if __name__ == "__main__":
    unittest.main()
