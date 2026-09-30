# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""A machine for the tests: the extension API as the Serializer uses it, on a clock the test moves.

It behaves the way the machine does where the Serializer depends on it,
as the bench reference showed it:
the job runner checks a program whole before a line goes out and refuses
one while a Grbl sender is connected; a program's first laser line waits
for the button, and an open lid there cancels the job with no alarm; the
button wait times out into alarm 3; an abort answers the job's record,
and during a cut leaves alarm 3; the lease names the job while it plays.

The press shows in the controller's report only: `arming` is true through
the wait and goes false at the press, and `armed` comes true when the
cooling's re-check has passed, about two seconds later. forgectrl's
job.armed event is no sign of a press: it comes with job.arming, when the
wait starts (the cooling goes to its cut profile then).
"""
import re

ID = "org.openglow.serializer"
BUTTON_TIMEOUT_S = 300.0
RECHECK_S = 2.0                     # from the press to the window open, as the bench reference showed
RETURN_S = 3.0                      # the head's drive back after a cut the lid canceled


class ApiError(Exception):
    def __init__(self, status, words):
        super().__init__("%d %s" % (status, words))
        self.status = status
        self.words = words


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def check_program(text):
    """The job runner's check (jobrun_program_check), as it refuses."""
    for n, line in enumerate(text.split("\n"), 1):
        if any(ord(c) < 32 or ord(c) > 126 for c in line):
            raise ApiError(400, "line %d: a byte that is not printable ASCII" % n)
        bare = re.sub(r"\([^)]*\)", "", line).split(";")[0].strip()
        if bare.startswith("$"):
            raise ApiError(400, "line %d: a $ line" % n)
        if any(c in bare for c in "?!~"):
            raise ApiError(400, "line %d: a realtime character" % n)
        if len(bare) > 250:
            raise ApiError(400, "line %d: over 250 characters" % n)


class FakeMachine:
    def __init__(self, clock):
        self.clock = clock
        self.lid = True
        self.homed_axes = 7
        self.pos = (100.0, 50.0)
        self.alarm = 0
        self.fire_ok = True
        self.verdict = "OK"
        self.sender_connected = False
        self.kept_out = False
        self.released = False
        self.job_state = None           # None, "arming" (the wait), "checking" (pressed), "running" (the window)
        self.check_until = None
        self.moving_until = 0.0
        self.job_lit = False
        self.job_ends_at = None
        self.arm_since = None
        self.run_s = 20.0
        self.events_list = []
        self.seq = 0
        self.dropped = 0
        self.programs = {}
        self.jobs = []
        self.lit_jobs = []              # the jobs whose window opened: what the machine marked
        self.aborts = 0
        self.paused = False

    # --- the test's hands ---------------------------------------------------------------------------

    def emit(self, name, data=None):
        self.seq += 1
        self.events_list.append({"seq": self.seq, "event": name, "data": data or {}})

    def press(self):
        self._advance()
        if self.job_state == "arming" and self.lid:
            self.job_state = "checking"             # the cooling's re-check, before the window opens
            self.check_until = self.clock.now + RECHECK_S

    def set_lid(self, closed):
        self._advance()
        if closed == self.lid:
            return
        self.lid = closed
        self.emit("lid", {"closed": closed})
        if not closed and self.job_state in ("arming", "checking", "running"):
            # The controller cancels the job (lid_policy cancel): a controlled stop, a reset from a standstill,
            # and after a cut the head's drive back to where the job began. The job runner does not see it: the
            # job stays open, and the lease with it, until it is aborted (as the bench reference showed).
            was_running = self.job_state == "running"
            self.job_state = "stuck"
            self.moving_until = self.clock.now + (RETURN_S if was_running else 0.0)
            self.emit("job.ended", {"result": "ended"})

    def _stick(self, alarm):
        """The controller ended the job with an alarm; the job runner holds it until it is aborted."""
        self.job_state = "stuck"
        self.moving_until = self.clock.now
        self.alarm = alarm
        self.emit("alarm", {"code": alarm})
        self.emit("job.ended", {"result": "alarm"})

    def _end(self, alarm):
        """The job is over: the window closes (job.ended, as forgectrl reports every job that began to arm), and
        the lease goes back."""
        self.job_state = None
        self.job_ends_at = None
        if alarm:
            self.alarm = alarm
            self.emit("alarm", {"code": alarm})
        self.emit("job.ended", {"result": "alarm" if alarm else "ended"})
        self.emit("lease.changed", {"owner": ("ext:" + ID) if self.kept_out else None})

    def _advance(self):
        now = self.clock.now
        if self.job_state == "checking" and now >= self.check_until:
            self.job_state = "running"
            self.job_lit = True
            self.lit_jobs.append(len(self.jobs) - 1)
            self.job_ends_at = now + self.run_s
        if self.job_state == "running" and now >= self.job_ends_at:
            self._end(None)
        elif self.job_state == "arming" and now - self.arm_since > BUTTON_TIMEOUT_S:
            self._stick(3)

    def moving(self):
        return self.job_state == "running" or (self.job_state == "stuck" and self.clock.now < self.moving_until)

    # --- the extension API -------------------------------------------------------------------------------

    def status(self):
        self._advance()
        holder = None
        if self.job_state:
            holder = {"owner": "job:" + ID, "kind": "sender", "under": "ext:" + ID}
        elif self.kept_out:
            holder = {"owner": "ext:" + ID, "kind": "extension"}
        return {
            "lens": {"edge_z": 4.5, "stops_found": True, "reach_min": -0.3, "reach_max": 11.3, "tray": "in",
                     "tray_offset_mm": 34.29},
            "state": "running" if self.moving() else "idle",
            "homed": self.homed_axes == 7, "homed_axes": self.homed_axes,
            "switches": {"lid": self.lid, "button": False, "interlock_ok": True},
            "lease": {"holder": holder},
            "pos": {"x": self.pos[0], "y": self.pos[1], "z": 3.0},
            "grbl": {"age_s": 0.5, "report": {
                "state": "Alarm" if self.alarm else ("Hold" if self.paused else
                                                     ("Run" if self.moving() else "Idle")),
                "alarm": self.alarm,
                "laser": {"armed": self.job_state == "running", "arming": self.job_state == "arming"}}},
        }

    def cool(self):
        return {"fire_ok": self.fire_ok, "verdict": self.verdict, "reason": "" if self.fire_ok else "warming up"}

    def events(self, since, wait):
        if since is None:
            return {"next": self.seq, "dropped": 0, "connected": True, "events": []}
        evs = [e for e in self.events_list if e["seq"] > since]
        return {"next": self.seq, "dropped": self.dropped, "connected": True, "events": evs}

    def sender_out(self, out):
        if out:
            if self.job_state or self.alarm:
                raise ApiError(409, "the controller is busy")
            self.kept_out = True
            self.sender_connected = False
        else:
            self.kept_out = False
            self.released = False
        return {"out": self.kept_out, "released": self.released}

    def sender_state(self):
        return {"out": self.kept_out, "released": self.released}

    def write_program(self, name, text):
        self.programs[name] = text
        return name

    def job(self, name):
        self._advance()
        if self.sender_connected:
            raise ApiError(409, "a Grbl client is connected")
        if self.job_state:
            raise ApiError(409, "a job is running")
        text = self.programs[name]
        check_program(text)
        if len(text) > 2 * 1024 * 1024:
            raise ApiError(400, "the program is more than 2 MiB")
        if self.alarm:
            raise ApiError(409, "error 9: the controller is in an alarm")
        self.jobs.append(text)
        self.job_state = "arming"
        self.job_lit = False
        self.arm_since = self.clock.now
        self.emit("job.arming")
        self.emit("job.armed")                      # with the wait's start, as forgectrl reports it
        self.emit("lease.changed", {"owner": "job:" + ID})
        return {"state": "running", "owner": "job:" + ID, "lit": False}

    def job_abort(self):
        self._advance()
        self.aborts += 1
        if not self.job_state:
            raise ApiError(409, "no program is running")
        lit = self.job_lit
        self._end(3 if self.moving() else (self.alarm or None))    # a reset in motion is alarm 3
        return {"state": "failed", "reason": "aborted", "lit": lit}
