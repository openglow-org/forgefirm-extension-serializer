# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The run's tests: cycles, the lid, the button, stops, and the numbers a cycle uses or gives back.

The runner is stepped by hand on the fake machine's clock (tests/fake.py):
events go to it as its own thread would hand them over, and its ticks run
every quarter second of fake time.
"""
import os
import random
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, HERE)

import fake  # noqa: E402
from serializer import fonts, runner, store  # noqa: E402

CAT = fonts.Catalog(os.path.join(ROOT, "share"))
CLOCK = {"epoch_ms": 1790000000000.0, "tz_min": 0}


class Bench:
    def __init__(self, **profile):
        self.clock = fake.Clock()
        self.m = fake.FakeMachine(self.clock)
        self.dir = tempfile.mkdtemp(prefix="serializer-test-")
        self.store = store.Store(self.dir)
        p = store.clean({})
        p.update({"name": "Test", "text": "SN {SN}", "font": "hershey-sans",
                  "counters": [dict(store.COUNTER_DEFAULT, name="SN", width=4, first="0001", next="0001")],
                  "grid": {"cols": 2, "rows": 1, "pitch_x": 50, "pitch_y": 20, "skip": []}})
        p.update(profile)
        self.pid = self.store.create(p)["id"]
        self.r = self.new_runner()
        self.posted = []                # the texts of every job the machine was given, in order
        post = self.m.job

        def job(name):
            out = post(name)
            self.posted.append(list(self.r.run.texts))
            return out
        self.m.job = job

    @property
    def marked(self):
        """The texts of every cycle the machine marked: the jobs whose window opened."""
        return [self.posted[i] for i in self.m.lit_jobs]

    def new_runner(self):
        r = runner.Runner(self.m, self.store, CAT, fake.ID, mono=self.clock)
        self.since = self.m.seq
        return r

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def pump(self, seconds=0.5):
        end = self.clock.now + seconds
        while True:
            for ev in self.m.events(self.since, 0)["events"]:
                self.r._event(ev["event"], ev["data"])
            self.since = self.m.seq
            self.r._tick()
            self.r._publish()
            if self.clock.now >= end:
                break
            self.clock.now += 0.25

    def cmd(self, name, **kw):
        try:
            getattr(self.r, "_cmd_" + name)(**kw)
        finally:
            self.r._publish()

    def start(self):
        self.cmd("start", pid=self.pid, clock=CLOCK)
        self.pump()

    def press(self, settle=2.5):
        """The button, and the cooling's re-check that follows it before the window opens."""
        self.m.press()
        self.pump(settle)

    def lid(self, closed, after=0.0):
        self.pump(after)
        self.m.set_lid(closed)
        self.pump()

    def reload(self):
        self.lid(False)
        self.lid(True, after=3.0)

    def next_value(self):
        return self.store.get(self.pid)["counters"][0]["next"]

    def lose_events(self):
        self.r.feed_ok = False
        if self.r.run is not None:
            self.r.run.events_ok = False

    @property
    def state(self):
        return self.r.view()["state"]

    @property
    def note(self):
        return self.r.view()["note"]["text"]


class Cycles(unittest.TestCase):
    def setUp(self):
        self.b = Bench()

    def tearDown(self):
        self.b.close()

    def test_a_cycle_then_the_next(self):
        b = self.b
        b.start()
        self.assertTrue(b.m.kept_out)
        self.assertEqual(b.state, "wait_press")
        self.assertEqual(len(b.m.jobs), 1)
        self.assertEqual(b.r.run.texts, [["SN 0001"], ["SN 0002"]])
        self.assertEqual(b.next_value(), "0001")          # not used until the press
        b.press()
        self.assertEqual(b.state, "running")
        self.assertEqual(b.next_value(), "0003")
        b.pump(25)
        self.assertEqual(b.state, "wait_open")
        self.assertIn("Cycle 1 is done", b.note)
        b.reload()
        self.assertEqual(b.state, "wait_press")
        self.assertEqual(len(b.m.jobs), 2)
        self.assertEqual(b.r.run.texts, [["SN 0003"], ["SN 0004"]])
        log = b.store.recent(5)
        self.assertEqual(log[0]["result"], "done")
        self.assertEqual(log[0]["first"], ["SN 0001"])

    def test_first_cycle_waits_for_the_lid(self):
        b = self.b
        b.m.lid = False
        b.start()
        self.assertEqual(b.state, "wait_close")
        self.assertEqual(b.m.jobs, [])
        b.lid(True, after=1.0)
        self.assertEqual(b.state, "wait_press")

    def test_stop_before_the_press_gives_the_numbers_back(self):
        b = self.b
        b.start()
        b.cmd("stop", now=True)
        self.assertEqual(b.m.aborts, 1)
        self.assertEqual(b.state, "idle")
        self.assertFalse(b.m.kept_out)
        self.assertEqual(b.m.alarm, 0)
        self.assertEqual(b.next_value(), "0001")
        self.assertIn("will be used next", b.note)
        b.start()
        self.assertEqual(b.r.run.texts, [["SN 0001"], ["SN 0002"]])

    def test_the_armed_event_at_the_wait_is_not_a_press(self):
        """forgectrl reports job.armed as the wait starts: the bench reference showed it."""
        b = self.b
        b.start()
        b.pump(3)
        self.assertEqual(b.state, "wait_press")
        self.assertEqual(b.next_value(), "0001")

    def test_stop_between_the_press_and_the_window_gives_the_numbers_back(self):
        b = self.b
        b.start()
        b.press(settle=0.5)                               # pressed; the cooling's re-check still running
        self.assertEqual(b.state, "wait_press")
        self.assertTrue(b.r.run.pressed)
        b.cmd("stop", now=True)
        self.assertEqual(b.m.lit_jobs, [])
        self.assertEqual(b.next_value(), "0001")
        self.assertEqual(b.m.alarm, 0)

    def test_nobody_presses(self):
        b = self.b
        b.start()
        b.pump(241)
        self.assertEqual(b.m.aborts, 1)
        self.assertEqual(b.m.alarm, 0)                    # canceled before the machine's own timeout
        self.assertEqual(b.state, "ready")
        self.assertEqual(b.next_value(), "0001")
        b.cmd("send")
        b.pump()
        self.assertEqual(b.state, "wait_press")
        self.assertEqual(b.r.run.texts, [["SN 0001"], ["SN 0002"]])

    def test_lid_opened_before_the_press(self):
        b = self.b
        b.start()
        b.lid(False, after=5)
        self.assertEqual(b.state, "wait_close")
        self.assertEqual(b.next_value(), "0001")
        b.lid(True, after=3)
        self.assertEqual(b.state, "wait_press")
        self.assertEqual(b.r.run.texts, [["SN 0001"], ["SN 0002"]])

    def test_the_machines_own_button_timeout_ends_the_run(self):
        b = self.b
        b.store.patch(b.pid, {"run": {"press_wait_s": 600}})     # longer than the machine's 300 s
        b.start()
        b.pump(305)
        self.assertEqual(b.state, "idle")
        self.assertIn("alarm 3", b.note)
        self.assertEqual(b.m.job_state, None)                   # the held job was ended
        self.assertEqual(b.next_value(), "0001")
        self.assertFalse(b.m.kept_out)

    def test_a_bump_is_not_a_reload(self):
        b = self.b
        b.start()
        b.press()
        b.pump(25)
        b.lid(False)
        b.lid(True, after=0.5)
        self.assertEqual(b.state, "wait_open")
        self.assertEqual(len(b.m.jobs), 1)
        self.assertIn("only a moment", b.note)
        b.reload()
        self.assertEqual(len(b.m.jobs), 2)

    def test_stop_after_this_cycle(self):
        b = self.b
        b.start()
        b.press()
        b.cmd("stop", now=False)
        self.assertEqual(b.state, "running")
        b.pump(25)
        self.assertEqual(b.state, "idle")
        self.assertFalse(b.m.kept_out)
        self.assertEqual(b.store.recent(1)[0]["result"], "done")
        self.assertEqual(b.next_value(), "0003")

    def test_stop_now_during_a_cut(self):
        b = self.b
        b.start()
        b.press()
        b.pump(5)
        b.cmd("stop", now=True)
        self.assertEqual(b.state, "idle")
        self.assertEqual(b.next_value(), "0003")          # the laser ran: the numbers are used
        self.assertIn("alarm 3", b.note)
        self.assertEqual(b.store.recent(1)[0]["result"], "stopped")

    def test_lid_opens_during_the_cut(self):
        b = self.b
        b.start()
        b.press()
        b.lid(False, after=5)
        self.assertEqual(b.state, "running")              # the head drives back: nothing is reset while it moves
        self.assertEqual(b.m.aborts, 0)
        b.pump(4)
        self.assertEqual(b.m.aborts, 1)                   # the job the job runner held is ended, from a standstill
        self.assertEqual(b.m.alarm, 0)
        self.assertEqual(b.state, "wait_close")
        self.assertEqual(b.next_value(), "0003")
        self.assertEqual(b.store.recent(1)[0]["result"], "stopped: the lid opened")
        b.lid(True, after=3)
        self.assertEqual(b.state, "wait_press")
        self.assertEqual(b.r.run.texts, [["SN 0003"], ["SN 0004"]])
        self.assertEqual(b.r.run.cycle, 2)                # its numbers were used: the next is cycle 2

    def test_a_canceled_cycle_keeps_its_number(self):
        b = self.b
        b.start()
        b.lid(False, after=2)
        b.lid(True, after=3)
        self.assertEqual(b.r.run.cycle, 1)

    def test_lost_events_count_the_numbers_as_used(self):
        b = self.b
        b.start()
        b.lose_events()
        b.lid(False, after=2)
        self.assertEqual(b.next_value(), "0003")
        self.assertEqual(b.store.recent(1)[0]["result"], "unsure")

    def test_cooling_is_waited_for(self):
        b = self.b
        b.m.fire_ok = False
        b.start()
        self.assertEqual(b.state, "cooling")
        self.assertIn("warming up", b.note)
        self.assertEqual(b.m.jobs, [])
        b.m.fire_ok = True
        b.pump(3)
        self.assertEqual(b.state, "wait_press")

    def test_released_from_the_panel(self):
        b = self.b
        b.start()
        b.m.released = True
        b.pump(3)
        self.assertEqual(b.state, "idle")
        self.assertEqual(b.m.aborts, 1)
        self.assertEqual(b.next_value(), "0001")
        self.assertFalse(b.m.kept_out)

    def test_released_during_the_cut_finishes_it(self):
        b = self.b
        b.start()
        b.press()
        b.m.released = True
        b.pump(3)
        self.assertEqual(b.state, "running")
        b.pump(25)
        self.assertEqual(b.state, "idle")
        self.assertEqual(b.store.recent(1)[0]["result"], "done")

    def test_an_alarm_ends_the_run(self):
        b = self.b
        b.start()
        b.press()
        b.m._end(3)
        b.pump()
        self.assertEqual(b.state, "idle")
        self.assertIn("alarm 3", b.note)
        self.assertEqual(b.next_value(), "0003")

    def test_the_next_program_is_made_during_the_reload(self):
        b = self.b
        b.start()
        b.press()
        b.pump(25)
        self.assertIsNotNone(b.r.pre)
        self.assertEqual(b.r.pre["texts"], [["SN 0003"], ["SN 0004"]])
        made = b.r.pre["program"]
        b.reload()
        self.assertEqual(b.state, "wait_press")
        self.assertEqual(b.m.jobs[-1], made)
        self.assertIsNone(b.r.pre)

    def test_a_program_made_ahead_is_remade_when_its_text_moved_on(self):
        b = Bench(text="SN {SN} {Hour}:{Minute}")
        try:
            b.start()
            b.press()
            b.pump(25)
            ahead = b.r.pre["texts"]
            b.lid(False)
            b.lid(True, after=90)                           # a minute and a half: the minute has changed
            self.assertEqual(b.state, "wait_press")
            self.assertNotEqual(b.r.run.texts, ahead)
            self.assertEqual([t[0][:7] for t in b.r.run.texts], ["SN 0003", "SN 0004"])
        finally:
            b.close()

    def test_the_machine_refuses_the_cycle(self):
        b = self.b
        b.start()
        b.press()
        b.pump(25)
        b.m.sender_connected = True                          # as if the keep-out had ended under us
        b.reload()
        self.assertEqual(b.state, "idle")
        self.assertIn("did not take the cycle", b.note)
        self.assertEqual(b.next_value(), "0003")


class Starting(unittest.TestCase):
    def setUp(self):
        self.b = Bench()

    def tearDown(self):
        self.b.close()

    def refused(self, words):
        with self.assertRaises(runner.Refused) as e:
            self.b.cmd("start", pid=self.b.pid, clock=CLOCK)
        self.assertIn(words, str(e.exception))
        self.assertFalse(self.b.m.kept_out)

    def test_not_homed(self):
        self.b.m.homed_axes = 4
        self.refused("Home the machine")

    def test_in_an_alarm(self):
        self.b.m.alarm = 2
        self.refused("alarm 2")

    def test_outside_the_work_area(self):
        self.b.m.pos = (480.0, 10.0)
        self.refused("past the right edge")

    def test_lens_cannot_reach(self):
        self.b.store.patch(self.b.pid, {"z": 30.0})
        self.refused("The lens reaches")

    def test_bad_template(self):
        self.b.store.patch(self.b.pid, {"text": "{Nope}"})
        self.refused("{Nope}")

    def test_uses_the_current_position(self):
        b = self.b
        b.m.pos = (120.0, 80.0)
        b.start()
        self.assertEqual(b.r.run.start, (120.0, 80.0))

    def test_uses_a_fixed_start(self):
        b = self.b
        b.store.patch(b.pid, {"start": {"mode": "fixed", "x": 30.0, "y": 40.0}})
        b.start()
        self.assertEqual(b.r.run.start, (30.0, 40.0))


class Restart(unittest.TestCase):
    def setUp(self):
        self.b = Bench()

    def tearDown(self):
        self.b.close()

    def test_restart_before_the_press_gives_the_numbers_back(self):
        b = self.b
        b.start()
        r2 = b.new_runner()
        r2.recover()
        self.assertEqual(b.m.aborts, 1)
        self.assertEqual(b.next_value(), "0001")
        self.assertFalse(b.m.kept_out)
        self.assertIn("restarted", r2.view()["note"]["text"] or r2.note[1])

    def test_restart_after_the_press_counts_them(self):
        b = self.b
        b.start()
        b.press()
        r2 = b.new_runner()
        r2.recover()
        self.assertEqual(b.next_value(), "0003")

    def test_restart_between_the_press_and_the_window_gives_them_back(self):
        b = self.b
        b.start()
        b.press(settle=0.5)
        r2 = b.new_runner()
        r2.recover()
        self.assertEqual(b.m.aborts, 1)
        self.assertEqual(b.m.lit_jobs, [])
        self.assertEqual(b.next_value(), "0001")

    def test_restart_with_no_job_counts_them(self):
        b = self.b
        b.start()
        b.m.job_abort()                                        # the job is gone, and nobody saw how
        r2 = b.new_runner()
        r2.recover()
        self.assertEqual(b.next_value(), "0003")


class NeverTwice(unittest.TestCase):
    def test_random_operator(self):
        """Whatever the operator does, and in whatever order, no number is marked twice."""
        total = 0
        for seed in range(12):
            rnd = random.Random(seed)
            b = Bench()
            try:
                for _ in range(300):
                    a = rnd.choice(["press", "press", "lid", "lid", "wait", "wait", "stop", "stop_after",
                                    "start", "send", "timeout", "lose"])
                    try:
                        if a == "press":
                            b.press(settle=rnd.choice([0.25, 0.5, 2.5]))
                        elif a == "lid":
                            b.lid(not b.m.lid, after=rnd.choice([0.5, 3.0]))
                        elif a == "wait":
                            b.pump(rnd.choice([1, 10, 30]))
                        elif a == "stop":
                            b.cmd("stop", now=True)
                        elif a == "stop_after":
                            b.cmd("stop", now=False)
                        elif a == "start":
                            b.m.alarm = 0
                            b.cmd("start", pid=b.pid, clock=CLOCK)
                        elif a == "send":
                            b.cmd("send")
                        elif a == "timeout":
                            b.pump(250)
                        elif a == "lose":
                            b.lose_events()
                    except runner.Refused:
                        pass
                    b.pump()
                seen = [t for cycle in b.marked for item in cycle for t in item]
                self.assertEqual(len(seen), len(set(seen)), "seed %d: %s" % (seed, seen))
                total += len(b.marked)
            finally:
                b.close()
        self.assertGreater(total, 40, "the operator marked too little for the test to mean anything")


if __name__ == "__main__":
    unittest.main()
