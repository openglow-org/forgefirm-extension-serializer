# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The run: one cycle after another, until the operator stops it.

Start keeps the Grbl sender out for the whole run and sends the first
cycle when the lid is closed. A cycle's program waits at its first line
for the press on the machine's button; the machine arms there, before
anything moves. When the cycle is done, the operator opens the lid,
changes the items, and closes it, and the next cycle goes out with the
next numbers.

The numbers of a cycle count as used from the moment the machine arms for
it. They are given back only when the service knows no laser line ran:
the operator stopped the cycle before the press, nobody pressed in time,
or the lid opened before the press. When the service cannot tell (it lost
the machine's events, or it restarted during a cycle), the numbers count
as used, so a number is never marked twice.

Every machine call is made on the runner's own thread, one at a time.
The page's calls hand it a command and wait for its answer.
"""
import queue
import threading
import time

from . import gcode, serials, text

STATUS_EVERY_S = 1.0
STATUS_JOB_EVERY_S = 0.5
SENDER_EVERY_S = 2.0
COOLING_RETRY_S = 2.0
CALL_WAIT_S = 8.0
SENDER_TRIES = 8                # half a second apart
PROGRAM = "cycle.gcode"


class Refused(Exception):
    """A command the runner will not carry out, in words for the operator."""


def fmt_len(mm, units):
    return ("%.2f in" % (mm / 25.4)) if units == "imperial" else ("%.1f mm" % mm)


def z_problem(profile, status, units="metric"):
    """Why the lens cannot reach the profile's Z, or None."""
    lens = (status or {}).get("lens") or {}
    if not lens.get("stops_found"):
        return None
    off = float(lens.get("tray_offset_mm", 0.0))
    now = off if lens.get("tray") == "out" else 0.0
    want = off if profile.get("tray") == "out" else 0.0
    lo = float(lens["reach_min"]) - now + want
    hi = float(lens["reach_max"]) - now + want
    z = float(profile.get("z", 0.0))
    if lo - 0.01 <= z <= hi + 0.01:
        return None
    return "The lens reaches material heights from %s to %s with the crumb tray %s, and this profile asks for %s." % (
        fmt_len(max(lo, 0.0), units), fmt_len(hi, units), profile.get("tray", "in"), fmt_len(z, units))


def homed(status):
    return (int((status or {}).get("homed_axes", 0)) & 3) == 3


def report(status):
    return ((status or {}).get("grbl") or {}).get("report") or {}


class Run:
    def __init__(self, pid, name, start, clock, units):
        self.pid = pid
        self.name = name
        self.start = start              # the grid's start point on the bed
        self.clock = clock              # serials.Clock
        self.units = units
        self.cycle = 0                  # the cycle in hand (sent or marking), 1 on
        self.done = 0                   # cycles marked
        self.used = 0                   # cycles whose numbers were used, marked or stopped: a cycle's number
        self.stop_after = False
        self.retry_at = 0.0
        self.sent_at = 0.0
        self.press_deadline = 0.0
        self.saw_armed = False          # the window opened for the cycle in hand: its numbers are used
        self.saw_arming = False         # the machine waited for the press
        self.pressed = False            # ... and the wait ended with the job still there: the press
        self.lid_in_wait = False        # the lid opened during the wait: the controller canceled the job
        self.events_ok = True
        self.lid_in_run = False
        self.paused = None
        self.texts = []
        self.ahead_for = None           # the done count the next program was last made ahead for


class Runner:
    def __init__(self, machine, store, catalog, pkg_id, mono=time.monotonic):
        self.m = machine
        self.store = store
        self.catalog = catalog
        self.id = pkg_id
        self.mono = mono
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.run = None
        self.state = "idle"
        self.note = ("", "")
        self.status = None
        self.status_at = -1e9
        self.sender_at = -1e9
        self.lid_closed = None
        self.lid_open_at = None
        self.feed_ok = True
        self.pre = None                 # the next cycle's program, made ahead (_build_ahead)
        self._pub = {}
        self._publish()

    # --- the page's side -----------------------------------------------------------------

    def command(self, name, **kw):
        """Hands a command to the runner's thread and waits for it; Refused carries its words."""
        done, box = threading.Event(), {}
        self.q.put(("cmd", name, kw, done, box))
        if not done.wait(CALL_WAIT_S):
            return self.view()
        if "error" in box:
            raise Refused(box["error"])
        return self.view()

    def view(self):
        with self.lock:
            v = dict(self._pub)
        if v.get("press_until") is not None:
            v["press_left_s"] = max(0, int(v.pop("press_until") - self.mono()))
        else:
            v.pop("press_until", None)
        return v

    def _publish(self):
        r = self.run
        pub = {"state": self.state, "note": {"kind": self.note[0], "text": self.note[1]}}
        if r is not None:
            pub["run"] = {"profile": r.pid, "name": r.name, "cycle": r.cycle, "done": r.done,
                          "stop_after": r.stop_after, "paused": r.paused, "start": list(r.start),
                          "pressed": r.pressed and self.state == "wait_press",
                          "first": (r.texts[0] if r.texts else []), "last": (r.texts[-1] if r.texts else []),
                          "items": len(r.texts)}
            pub["press_until"] = r.press_deadline if self.state == "wait_press" else None
        with self.lock:
            self._pub = pub

    def _say(self, kind, words):
        self.note = (kind, words)

    # --- the thread --------------------------------------------------------------------------

    def events_loop(self):
        """Follows the machine's events into the runner's queue, for good."""
        since = None
        while True:
            try:
                doc = self.m.events(since, 25 if since is not None else None)
            except Exception:
                self.q.put(("feed", False))
                time.sleep(2)
                continue
            nxt = doc.get("next", since)
            if since is not None and nxt is not None and nxt < since:
                self.q.put(("feed", False))              # the feed started over under us: events may be lost
            elif doc.get("dropped") or not doc.get("connected", True):
                self.q.put(("feed", False))
            for ev in doc.get("events", []):
                self.q.put(("ev", ev.get("event"), ev.get("data") or {}))
            since = nxt

    def loop(self):
        self.recover()
        while True:
            try:
                item = self.q.get(timeout=0.25)
            except queue.Empty:
                item = None
            try:
                if item is None:
                    pass
                elif item[0] == "cmd":
                    _, name, kw, done, box = item
                    try:
                        getattr(self, "_cmd_" + name)(**kw)
                    except Refused as e:
                        box["error"] = str(e)
                    except Exception as e:                       # never leave the page waiting
                        box["error"] = "%s: %s" % (type(e).__name__, e)
                    finally:
                        self._publish()
                        done.set()
                elif item[0] == "ev":
                    self._event(item[1], item[2])
                elif item[0] == "feed":
                    self.feed_ok = False
                    if self.run is not None:
                        self.run.events_ok = False
                self._tick()
            except Exception as e:
                print("runner: %s: %s" % (type(e).__name__, e), flush=True)
            self._publish()

    # --- what the machine says -----------------------------------------------------------------

    def _poll_status(self):
        try:
            st = self.m.status()
        except Exception:
            return None
        self.status = st
        self.status_at = self.mono()
        closed = bool((st.get("switches") or {}).get("lid"))
        if self.lid_closed is None:
            self.lid_closed = closed
            if not closed:
                self.lid_open_at = -1e9                  # open since before anyone looked: not a bump
        elif closed != self.lid_closed:
            self._lid(closed)                            # an edge the events did not bring (yet)
        return st

    def _job_running(self):
        holder = ((self.status or {}).get("lease") or {}).get("holder") or {}
        return holder.get("owner") == "job:" + self.id

    def _alarm(self):
        return int(report(self.status).get("alarm") or 0)

    def _event(self, name, data):
        r = self.run
        if name == "lid":
            closed = bool(data.get("closed"))
            if closed != self.lid_closed:
                self._lid(closed)
        elif name in ("lease.changed", "job.ended", "alarm"):
            self.status_at = -1e9                         # look now
        if r is None:
            return
        # job.armed is no sign of a press: forgectrl reports it with job.arming, as the wait starts. The press
        # is read from the controller's report (_tick).
        if name == "job.paused" and self.state == "running":
            r.paused = data.get("reason") or "hold"
        elif name == "job.resumed" and self.state == "running":
            r.paused = None
            r.lid_in_run = False

    def _lid(self, closed):
        self.lid_closed = closed
        now = self.mono()
        r = self.run
        if not closed:
            self.lid_open_at = now
            if r is None:
                return
            if self.state == "running":
                r.lid_in_run = True
            elif self.state == "wait_press":
                r.lid_in_wait = True
            elif self.state in ("wait_open", "ready", "cooling"):
                self.state = "wait_close"
                self._say("info", "Change the items, then close the lid.")
            return
        if r is None or self.state != "wait_close":
            return
        held = now - (self.lid_open_at if self.lid_open_at is not None else now)
        prof = self.store.get(r.pid)
        min_open = prof["run"]["min_open_s"] if prof else 2.0
        if held + 1e-6 < min_open:
            self.state = "wait_open"
            self._say("warn", "The lid was open for only a moment, so nothing was sent. Open it, change the items, "
                              "and close it.")
            return
        self._send()

    # --- the ticks ----------------------------------------------------------------------------------

    def _tick(self):
        r = self.run
        if r is None:
            return
        now = self.mono()
        every = STATUS_JOB_EVERY_S if self.state in ("wait_press", "running") else STATUS_EVERY_S
        if now - self.status_at >= every:
            self._poll_status()
            r = self.run
            if r is None:
                return
        if now - self.sender_at >= SENDER_EVERY_S:
            self.sender_at = now
            try:
                if self.m.sender_state().get("released"):
                    self._released()
                    return
            except Exception:
                pass
        s = self.state
        if s in ("wait_open", "wait_close", "ready") and r.ahead_for != r.done:
            r.ahead_for = r.done
            self._build_ahead()
        elif s == "cooling" and now >= r.retry_at and self.lid_closed:
            self._send()
        elif s == "wait_press":
            # The controller's report: `arming` through the wait, false from the press, and `armed` once the
            # cooling's re-check has passed and the window is open (about two seconds after the press).
            las = report(self.status).get("laser") or {}
            running = self._job_running() and self.status_at > r.sent_at
            if not self.lid_closed:
                r.lid_in_wait = True
            if running and las.get("arming"):
                r.saw_arming = True
            if running and las.get("armed"):
                self._armed()
            elif running and self._ended_by_controller(r.lid_in_wait and not las.get("arming")):
                self._abort_held()
                self._ended_before_arm()
            elif running and r.saw_arming and not las.get("arming") and not r.lid_in_wait and not self._alarm():
                r.pressed = True
            elif not running and now - r.sent_at > 1.0 and self.status_at > r.sent_at:
                self._ended_before_arm()
            elif now >= r.press_deadline and not r.pressed:
                self._press_timeout()
        elif s == "running":
            las = report(self.status).get("laser") or {}
            if not self._job_running() and self.status_at > r.sent_at:
                self._cycle_over()
            elif self._job_running() and self._ended_by_controller(r.lid_in_run and not las.get("armed")):
                self._abort_held()
                self._cycle_over()

    def _ended_by_controller(self, lid_cancel):
        """The controller ended the job - the lid canceled it (lid_policy cancel), or an alarm did - and the job
        runner still holds it: it holds a job until the job's lines are all answered, and a controller reset
        answers none. True once the head is still, since ending the job is a reset of its own, and a reset in
        motion (the head's drive back after a canceled cut) is an alarm."""
        if not (lid_cancel or self._alarm()):
            return False
        rep = report(self.status)
        return rep.get("state") in ("Idle", "Alarm") and (self.status or {}).get("state") == "idle"

    def _abort_held(self):
        """Ends a job the controller already ended, so the job runner lets the machine go."""
        r = self.run
        if self._abort():
            r.saw_armed = True
        self._poll_status()

    # --- a cycle -------------------------------------------------------------------------------------

    def _profile_face(self, pid):
        p = self.store.get(pid)
        if p is None:
            raise Refused("That profile is gone")
        face = self.catalog.get(p["font"])
        if face is None:
            raise Refused("The font of this profile is not on the machine; pick another")
        return p, face

    def _texts(self, p, t):
        try:
            return serials.layout_cycle(p, len(text.slots(p)), t)
        except serials.TemplateError as e:
            raise Refused(str(e))

    def _refuse_problems(self, p, info, units):
        probs = gcode.problems(info)
        z = z_problem(p, self.status, units)
        if z:
            probs.append(z)
        if probs:
            raise Refused(probs[0])

    def _check(self, p, face, start, t, units):
        """Refused with the first thing that would stop a cycle, from its estimate: a fraction of a build."""
        texts, _, _ = self._texts(p, t)
        try:
            info = gcode.estimate(p, face, texts, start)
        except gcode.JobError as e:
            raise Refused(str(e))
        self._refuse_problems(p, info, units)

    def _build(self, p, face, start, t, units):
        """(program, info, texts, before, after); Refused with the first problem. The program made ahead
        during the reload is used when the texts, the profile, and the start point it was made for still
        stand; otherwise the program is made now, which can take seconds."""
        texts, before, after = self._texts(p, t)
        pre, self.pre = self.pre, None
        if pre and pre["texts"] == texts and pre["profile"] == p and pre["start"] == start:
            program, info = pre["program"], pre["info"]
        else:
            self.state = "sending"
            self._say("info", "Making the cycle's program.")
            self._publish()
            try:
                program, info = gcode.build(p, face, texts, start, p["name"])
            except gcode.JobError as e:
                raise Refused(str(e))
        self._refuse_problems(p, info, units)
        return program, info, texts, before, after

    def _build_ahead(self):
        """Makes the next cycle's program while the operator changes the items, so closing the lid sends it at
        once. Out of a cut only: the service has a quarter of the processor then, and a sliver during one."""
        r = self.run
        try:
            p, face = self._profile_face(r.pid)
            now = self.mono()
            texts, _, _ = self._texts(p, r.clock.at(now))
            program, info = gcode.build(p, face, texts, r.start, p["name"])
        except (Refused, gcode.JobError):
            return
        self.pre = {"texts": texts, "profile": p, "start": r.start, "program": program, "info": info}

    def _send(self):
        r = self.run
        now = self.mono()
        try:
            p, face = self._profile_face(r.pid)
        except Refused as e:
            return self._end("bad", str(e))
        try:
            cool = self.m.cool()
        except Exception:
            cool = {"fire_ok": True}
        if not cool.get("fire_ok", True):
            self.state = "cooling"
            r.retry_at = now + COOLING_RETRY_S
            why = cool.get("reason") or cool.get("verdict") or "not ready"
            return self._say("info", "Waiting for the cooling (%s). The cycle goes out when it is ready." % why)
        self._poll_status()
        if self._alarm():
            return self._end("bad", self._alarm_words())
        if not self.lid_closed:
            self.state = "wait_close"
            return self._say("info", "Close the lid to send the cycle.")
        try:
            program, info, texts, before, after = self._build(p, face, r.start, r.clock.at(now), r.units)
        except Refused as e:
            return self._end("bad", str(e))
        if self.run is None:
            return
        r.cycle = r.used + 1            # a cycle canceled before its press goes out again under its number
        r.texts = texts
        try:
            self.m.write_program(PROGRAM, program)
        except Exception as e:
            return self._end("bad", "The program could not be written: %s" % e)
        res = {"pid": r.pid, "cycle": r.cycle, "before": before, "after": after, "texts": texts,
               "at": r.clock.at(now).strftime("%Y-%m-%d %H:%M"), "status": "sent"}
        self.store.save_state({"run": {"pid": r.pid}, "reservation": res})
        r.saw_armed = r.saw_arming = r.pressed = r.lid_in_wait = False
        r.events_ok = self.feed_ok
        r.lid_in_run = False
        r.paused = None
        try:
            self.m.job(PROGRAM)
        except Exception as e:
            self.store.save_state({"run": {"pid": r.pid}})
            return self._end("bad", "The machine did not take the cycle: %s" % getattr(e, "words", e))
        r.sent_at = self.mono()
        r.press_deadline = r.sent_at + p["run"]["press_wait_s"]
        self.status_at = -1e9
        self.state = "wait_press"
        self._say("info", "Press the button on the machine to mark cycle %d." % r.cycle)

    def _reservation(self):
        return self.store.state().get("reservation")

    def _consume(self):
        res = self._reservation()
        if res:
            self.store.set_counters(res["pid"], res["after"])
            self.store.save_state({"run": {"pid": res["pid"]}} if self.run else {})
            if self.run is not None:
                self.run.used += 1
        return res

    def _give_back(self):
        res = self._reservation()
        self.store.save_state({"run": {"pid": res["pid"]}} if (res and self.run) else {})
        return res

    def _log(self, res, result):
        if not res:
            return
        texts = res.get("texts") or []
        self.store.log({"at": res.get("at"), "profile": self.run.name if self.run else "", "cycle": res["cycle"],
                        "items": len(texts), "first": texts[0] if texts else [], "last": texts[-1] if texts else [],
                        "result": result})

    def _armed(self):
        r = self.run
        r.saw_armed = True
        self._consume()
        r.paused = None
        self.state = "running"
        self._say("info", "Marking cycle %d." % r.cycle)

    def _ended_before_arm(self):
        """The job ended while the service had not seen its window open. Its numbers are given back only when
        nothing says the laser could have run: the press came, or events were lost, and they count as used."""
        r = self.run
        fired = r.saw_armed or r.pressed or not r.events_ok
        res = self._consume() if fired else self._give_back()
        if fired:
            self._log(res, "unsure")
            lead = "Cycle %d stopped as it started, so its numbers count as used." % r.cycle
        else:
            lead = "The cycle was canceled before the button was pressed, so its numbers will be used next."
        if self._alarm():
            return self._end("bad", self._alarm_words(lead))
        if not self.lid_closed:
            self.state = "wait_close"
            if not fired:
                lead = "The lid opened before the button was pressed, so the cycle was canceled. Its numbers will " \
                       "be used next."
            return self._say("warn", lead + " Close the lid to send it again.")
        self.state = "ready"
        self._say("warn", lead)

    def _abort(self):
        """Ends the job in hand; True when the laser may have run."""
        try:
            rec = self.m.job_abort()
        except Exception:
            return True
        return bool((rec or {}).get("lit"))

    def _press_timeout(self):
        r = self.run
        fired = self._abort() or r.saw_armed
        res = self._consume() if fired else self._give_back()
        self._poll_status()
        if fired:
            self._log(res, "unsure")
        if self._alarm():
            return self._end("bad", self._alarm_words("Nobody pressed the button in time."))
        self.state = "ready"
        self._say("warn", "Nobody pressed the button in time, so the cycle was canceled. Its numbers will be used "
                          "next. Press Send again, or open and close the lid.")

    def _cycle_over(self):
        r = self.run
        res = self._reservation() or {"cycle": r.cycle, "texts": r.texts,
                                       "at": r.clock.at(self.mono()).strftime("%Y-%m-%d %H:%M")}
        alarm = self._alarm()
        if alarm:
            self._log(res, "alarm %d" % alarm)
            return self._end("bad", self._alarm_words("Cycle %d stopped." % r.cycle))
        if r.lid_in_run:
            self._log(res, "stopped: the lid opened")
            words = "The lid opened during cycle %d and stopped it. Its numbers count as used." % r.cycle
            kind = "warn"
        else:
            self._log(res, "done")
            r.done += 1
            words = "Cycle %d is done." % r.cycle
            kind = "ok"
        if r.stop_after:
            return self._end(kind, words + " The run is over.")
        if self.lid_closed:
            self.state = "wait_open"
            self._say(kind, words + " Open the lid, change the items, and close it.")
        else:
            self.state = "wait_close"
            self._say(kind, words + " Change the items, then close the lid.")

    def _alarm_words(self, lead=""):
        a = self._alarm()
        words = "The machine stopped with alarm %d. Clear it from your Grbl sender ($X), or home the machine, " \
                "then press Start." % a
        return (lead + " " + words).strip()

    def _released(self):
        r = self.run
        if self.state == "wait_press":
            fired = self._abort() or r.saw_armed
            res = self._consume() if fired else self._give_back()
            if fired:
                self._log(res, "unsure")
            return self._end("warn", "The Grbl sender was let back in from the panel, so the run stopped before "
                                     "cycle %d. Its numbers will be used next." % r.cycle if not fired else
                             "The Grbl sender was let back in from the panel, so the run stopped.")
        if self.state == "running":
            if not r.stop_after:
                r.stop_after = True
                self._say("warn", "The Grbl sender was let back in from the panel: the run stops after this cycle.")
            return
        self._end("warn", "The Grbl sender was let back in from the panel, so the run stopped.")

    def _end(self, kind, words):
        try:
            self.m.sender_out(False)
        except Exception:
            pass
        self.run = None
        self.pre = None
        self.state = "idle"
        self.store.save_state({})
        self._say(kind, words)

    # --- commands ------------------------------------------------------------------------------------

    def _cmd_start(self, pid, clock, units="metric"):
        if self.run is not None:
            raise Refused("A run is already going")
        p, face = self._profile_face(pid)
        st = self._poll_status()
        if st is None:
            raise Refused("The machine does not answer")
        if not report(st):
            raise Refused("The GRBL controller is not running")
        if not homed(st):
            raise Refused("Home the machine first, so the start point is known.")
        if self._alarm():
            raise Refused(self._alarm_words().replace(" then press Start.", " then try again."))
        if p["start"]["mode"] == "fixed":
            start = (p["start"]["x"], p["start"]["y"])
        else:
            pos = st.get("pos") or {}
            start = (float(pos.get("x", 0.0)), float(pos.get("y", 0.0)))
        now = self.mono()
        clk = serials.Clock(clock["epoch_ms"], clock["tz_min"], now)
        self._check(p, face, start, clk.at(now), units)             # refuses what could not run
        # The machine takes the sender only when no line has gone to it for 2 s, and the last job's own lines
        # count: a Start right after a Stop waits that out.
        for attempt in range(SENDER_TRIES):
            try:
                self.m.sender_out(True)
                break
            except Exception as e:
                words = str(getattr(e, "words", e))
                if "using the machine" not in words or attempt == SENDER_TRIES - 1:
                    raise Refused("The machine would not keep the Grbl sender out: %s" % words)
                time.sleep(0.5)
        self.run = Run(pid, p["name"], start, clk, units)
        self.sender_at = now
        self.store.save_state({"run": {"pid": pid}})
        if self.lid_closed:
            self._send()
        else:
            self.lid_open_at = -1e9                      # the operator is loading: closing it sends the cycle
            self.state = "wait_close"
            self._say("info", "Load the items and close the lid.")

    def _cmd_stop(self, now=True):
        r = self.run
        if r is None:
            return
        if self.state == "running" and not now:
            r.stop_after = True
            return self._say("info", "The run stops after this cycle.")
        if self.state == "wait_press":
            fired = self._abort() or r.saw_armed
            res = self._consume() if fired else self._give_back()
            if fired:
                self._log(res, "unsure")
                return self._end("warn", "Stopped.")
            return self._end("ok", "Stopped. Cycle %d was not marked; its numbers will be used next." % r.cycle)
        if self.state == "running":
            self._abort()
            res = self._reservation() or {"cycle": r.cycle, "texts": r.texts, "at": ""}
            self._log(res, "stopped")
            self._poll_status()
            if self._alarm():
                return self._end("warn", "Stopped during cycle %d; its numbers count as used. %s" % (
                    r.cycle, self._alarm_words().replace(" then press Start.", " before the next run.")))
            return self._end("warn", "Stopped during cycle %d; its numbers count as used." % r.cycle)
        self._end("ok", "Stopped after %d cycle%s." % (r.done, "" if r.done == 1 else "s"))

    def _cmd_send(self):
        if self.run is None:
            raise Refused("There is no run to send a cycle for")
        if self.state != "ready":
            raise Refused("There is no canceled cycle to send again: open the lid, change the items, and close it")
        if not self.lid_closed:
            raise Refused("Close the lid first")
        self._send()

    # --- after a restart ----------------------------------------------------------------------------

    def recover(self):
        """A service that restarted during a run: the run is over, and a cycle in hand is settled."""
        st = self.store.state()
        res = st.get("reservation")
        if not res and not st.get("run"):
            return
        status = self._poll_status() or {}
        holder = ((status.get("lease") or {}).get("holder") or {}).get("owner")
        if res:
            fired = True
            if holder == "job:" + self.id and not report(status).get("laser", {}).get("armed"):
                fired = self._abort()
            if fired:
                self.store.set_counters(res["pid"], res["after"])
                self.store.log({"at": res.get("at"), "profile": "", "cycle": res["cycle"],
                                "items": len(res.get("texts") or []),
                                "first": (res.get("texts") or [[]])[0], "last": (res.get("texts") or [[]])[-1],
                                "result": "unsure"})
        try:
            self.m.sender_out(False)
        except Exception:
            pass
        self.store.save_state({})
        self._say("warn", "The Serializer restarted, which ended the run. Press Start to go on.")
