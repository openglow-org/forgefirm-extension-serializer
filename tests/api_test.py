# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The page's calls: profiles, the preview, the fonts, and the limits of what the host carries."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, HERE)

import fake  # noqa: E402
from serializer import api, fonts, runner, store  # noqa: E402

CAT = fonts.Catalog(os.path.join(ROOT, "share"))
CLOCK = {"epoch_ms": time.time() * 1000, "tz_min": 0}
ANSWER_MAX = 64 * 1024          # what the host takes back from a service
BODY_MAX = 4096                 # what the panel sends to one


class Calls(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="serializer-api-")
        self.clock = fake.Clock()
        self.m = fake.FakeMachine(self.clock)
        self.store = store.Store(self.dir)
        self.runner = runner.Runner(self.m, self.store, CAT, fake.ID, mono=self.clock)
        self.api = api.Api(self.store, CAT, self.runner, self.m)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, method, path, body=None):
        if body is not None:
            self.assertLessEqual(len(json.dumps(body)), BODY_MAX, "a call the panel would refuse: %s" % path)
        out = self.api.handle(method, path, body or {})
        self.assertLessEqual(len(json.dumps(out, separators=(",", ":"))), ANSWER_MAX, "an answer the host refuses")
        return out

    def pid(self):
        return self.call("GET", "/state")["selected"]

    def test_a_new_machine_has_the_example(self):
        st = self.call("GET", "/state")
        self.assertEqual([p["name"] for p in st["profiles"]], ["Example: serial plate"])
        pv = self.call("POST", "/preview", {"id": st["selected"], "clock": CLOCK})
        self.assertEqual(pv["errors"], [])
        self.assertTrue(pv["item"]["paths"])

    def test_profiles(self):
        pid = self.pid()
        cp = self.call("POST", "/profile/new", {"from": pid})["profile"]
        self.assertEqual(cp["name"], "Copy of Example: serial plate")
        self.assertEqual(self.call("GET", "/state")["selected"], cp["id"])
        p = self.call("POST", "/profile/patch", {"id": cp["id"], "set": {"box": {"w": 55}, "name": "Brass tags"}})
        self.assertEqual(p["profile"]["box"], dict(store.DEFAULT["box"], w=55.0, h=12.0, valign="middle"))
        self.assertIn("Brass tags", [x["name"] for x in p["profiles"]])
        self.call("POST", "/profile/delete", {"id": cp["id"]})
        with self.assertRaises(api.CallError) as e:
            self.call("POST", "/profile", {"id": cp["id"]})
        self.assertEqual(e.exception.status, 404)

    def test_the_running_profile_cannot_change(self):
        pid = self.pid()
        self.runner._cmd_start(pid=pid, clock=CLOCK)
        self.runner._publish()
        with self.assertRaises(api.CallError) as e:
            self.call("POST", "/profile/patch", {"id": pid, "set": {"text": "x"}})
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(api.CallError):
            self.call("POST", "/profile/delete", {"id": pid})

    def test_a_big_grid_fits_the_answer(self):
        pid = self.pid()
        self.call("POST", "/profile/patch", {"id": pid, "set": {
            "font": "roboto-400", "size_mm": 3, "grid": {"cols": 20, "rows": 20, "pitch_x": 20, "pitch_y": 12},
            "box": {"w": 18, "h": 10}, "text": "SN {Serial} {Year}-{Month}-{Day}\nLOT {Serial}\nLine three {Serial}"}})
        pv = self.call("POST", "/preview", {"id": pid, "clock": CLOCK})
        self.assertEqual(len(pv["slots"]), 400)
        self.assertTrue(pv["item"]["paths"])

    def test_long_text_in_one_item_fits_the_answer(self):
        pid = self.pid()
        self.call("POST", "/profile/patch", {"id": pid, "set": {
            "font": "greatvibes-400", "size_mm": 10, "box": {"w": 400, "h": 200},
            "text": "\n".join(["The quick brown fox jumps over the lazy dog {Serial}"] * 7)}})
        pv = self.call("POST", "/preview", {"id": pid, "clock": CLOCK})
        self.assertTrue(pv["item"]["paths"])
        self.assertGreaterEqual(pv["item"]["unit"], 0.01)

    def test_samples_of_every_font_fit(self):
        ids = [f["id"] for f in self.call("GET", "/fonts")["fonts"]]
        self.assertEqual(ids[0], "hershey-sans")
        for i in range(0, len(ids), 12):
            out = self.call("POST", "/samples", {"fonts": ids[i:i + 12], "text": "Serial 0123"})["samples"]
            self.assertEqual(sorted(out), sorted(ids[i:i + 12]))
            for s in out.values():
                self.assertTrue(s["paths"])

    def test_the_shipped_samples_are_what_the_code_draws(self):
        with open(os.path.join(ROOT, "share", "samples.json")) as f:
            shipped = json.load(f)
        self.assertEqual(sorted(shipped), sorted(CAT.faces), "run tools/samples_gen.py")
        for fid, s in shipped.items():
            drawn = json.loads(json.dumps(api.sample(CAT.get(fid), api.SAMPLE_TEXT)))
            self.assertEqual(s, drawn, "%s: run tools/samples_gen.py" % fid)

    def test_the_fullest_patch_fits_a_call(self):
        pid = self.pid()
        counters = [dict(store.COUNTER_DEFAULT, name="Counter%d" % i, type="custom",
                         alphabet="0123456789ABCDEFGHJKLMNPQRSTUVWXYZ", width=16, first="0" * 16, last="Z" * 16,
                         next="0" * 16, advance="carry" if i else "item", carry_from="Counter0" if i else "",
                         at_end="wrap") for i in range(store.COUNTERS_MAX)]
        codes = [{"name": "Code%d" % i, "of": "week", "base_year": 2026, "values": ["WWWWWWWW"] * 53}
                 for i in range(1)]
        self.call("POST", "/profile/patch", {"id": pid, "set": {"counters": counters}})
        self.call("POST", "/profile/patch", {"id": pid, "set": {"codes": codes}})
        self.call("POST", "/profile/patch", {"id": pid, "set": {"notes": "n" * store.NOTES_MAX}})
        self.call("POST", "/profile/patch", {"id": pid, "set": {"text": "t" * store.TEXT_MAX}})

    def test_preview_names_what_is_wrong(self):
        pid = self.pid()
        self.call("POST", "/profile/patch", {"id": pid, "set": {"text": "{Nope}"}})
        pv = self.call("POST", "/preview", {"id": pid, "clock": CLOCK})
        self.assertIn("{Nope}", pv["errors"][0])
        self.call("POST", "/profile/patch", {"id": pid, "set": {"text": "WIDE TEXT HERE", "size_mm": 10}})
        pv = self.call("POST", "/preview", {"id": pid, "clock": CLOCK, "units": "imperial"})
        self.assertIn("bigger than its box", pv["warnings"][0])
        self.assertIn(" in", pv["warnings"][0])
        self.m.homed_axes = 4
        self.api._status = (0.0, None)
        pv = self.call("POST", "/preview", {"id": pid, "clock": CLOCK})
        self.assertTrue(any("Home the machine" in w for w in pv["warnings"]), pv["warnings"])
        self.assertIsNone(pv["start"])

    def test_a_clock_is_required(self):
        with self.assertRaises(api.CallError) as e:
            self.call("POST", "/preview", {"id": self.pid()})
        self.assertEqual(e.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
