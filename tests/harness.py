#!/usr/bin/env python3
# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
"""The page in a stand-in panel, against the real service code and the fake machine.

The harness serves a page that frames ui/index.html the way the control
panel does (a srcdoc frame sandboxed with scripts alone, the panel's
policy first, the panel's color scheme) and answers its bridge calls:
self, the machine's status, the job's record, the frame's height, and
the page's calls to its service, which go to the service's own code
(lib/serializer) running here on the fake machine (tests/fake.py) in real
time. Buttons beside the frame are the machine's lid and button.

  python3 tests/harness.py [--port 8097] [--units imperial]
      then browse http://127.0.0.1:8097/

  python3 tests/harness.py --call-port 8765
      also answers the page's calls for forgectrl's dev server, which runs
      the panel's own code around the page:
      tools/devserver.py --mock --package build/pkg --call-port 8765

  python3 tests/harness.py --scenario [--browser chrome|firefox]
      runs the page through a whole run in a headless browser and exits 0
      when every step passed. A driver added to the scenario's copy of the
      page, and to nothing else, clicks and reads the page as an operator
      would; the steps are in SCENARIO below.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, HERE)

import fake  # noqa: E402
from serializer import api, fonts, runner, store  # noqa: E402

POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
          "img-src blob: data:; connect-src 'none'; form-action 'none'; base-uri 'none'; webrtc 'block'")
BROWSERS = {
    "chrome": [r"C:\Program Files\Google\Chrome\Application\chrome.exe", "/usr/bin/google-chrome",
               "/usr/bin/chromium"],
    "firefox": [r"C:\Program Files\Mozilla Firefox\firefox.exe", "/usr/bin/firefox"],
}


class RealClock:
    @property
    def now(self):
        return time.monotonic()

    def __call__(self):
        return time.monotonic()


class Machine(fake.FakeMachine):
    """The fake machine in real time, safe to call from every thread, with a job record like the runner's."""

    def __init__(self):
        super().__init__(RealClock())
        self.lock = threading.RLock()
        self.run_s = 6.0
        self.lines = 0

    def events(self, since, wait):
        end = time.monotonic() + (wait or 0)
        while True:
            with self.lock:
                doc = super().events(since, None if since is None else 0)
            if since is None or doc["events"] or time.monotonic() >= end:
                return doc
            time.sleep(0.1)

    def job_record(self):
        with self.lock:
            self._advance()
            if not self.jobs:
                return {"state": "idle"}
            if self.job_state == "running":
                done = 1.0 - max(0.0, self.job_ends_at - self.clock.now) / self.run_s
                return {"state": "running", "lines": self.lines, "acked": int(self.lines * done), "sent": self.lines}
            return {"state": "running" if self.job_state else "done", "lines": self.lines, "acked": 0}

    def job(self, name):
        with self.lock:
            self.lines = self.programs[name].count("\n")
            return super().job(name)


def _locked(name):
    base = getattr(fake.FakeMachine, name)

    def call(self, *a, **kw):
        with self.lock:
            return base(self, *a, **kw)
    return call


for _n in ("status", "cool", "sender_out", "sender_state", "write_program", "job_abort", "press", "set_lid"):
    setattr(Machine, _n, _locked(_n))


HARNESS = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Serializer harness</title>
<style>
body { margin: 0; font: 13.5px system-ui, sans-serif; background: #f0f1f4; color: #222; }
body.dark { background: #15161c; color: #d9dce3; }
.top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 12px; background: #2b2b5e; color: #fff; }
.top button { font: inherit; }
.top .st { margin-left: auto; font-variant-numeric: tabular-nums; }
.cardx { margin: 12px; background: #fff; border: 1px solid #dde0e6; border-radius: 6px; padding: 10px; max-width: 1100px; }
body.dark .cardx { background: #1e2028; border-color: #32353f; }
iframe { display: block; width: 100%; height: 360px; border: 0; background: transparent; color-scheme: light; }
body.dark iframe { color-scheme: dark; }
#verdict { padding: 8px 12px; font-weight: 600; white-space: pre-wrap; }
</style></head><body>
<div class="top">
  <b>Stand-in panel</b>
  <button id="press">Press the button</button>
  <button id="lid">Open the lid</button>
  <button id="home">Unhome</button>
  <button id="cool">Cooling: OK</button>
  <button id="alarm">Clear the alarm</button>
  <button id="theme">Dark</button>
  <span class="st" id="st"></span>
</div>
<div id="verdict"></div>
<div class="cardx"><div style="font-weight:600;margin-bottom:6px">Serializer <span style="color:#767a82;font-weight:400">Official</span></div><div id="slot"></div></div>
<script>
var PAGE = @@PAGE@@, POLICY = @@POLICY@@, UNITS = @@UNITS@@, SCENARIO = @@SCENARIO@@;
var frame = document.createElement('iframe');
frame.setAttribute('sandbox', 'allow-scripts');
frame.srcdoc = '<meta http-equiv="Content-Security-Policy" content="' + POLICY + '"><meta name="color-scheme" content="light dark">' + PAGE;
document.getElementById('slot').appendChild(frame);
function post(path, body) {
  return fetch(path, { method: 'POST', body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); });
}
window.addEventListener('message', function (ev) {
  var m = ev.data;
  if (ev.source !== frame.contentWindow || !m) return;
  if (m.harness === 1) { var w = waiting[m.id]; if (w) { delete waiting[m.id]; w(m.r); } return; }
  if (m.forgefirm !== 1) return;
  var reply = function (ok, v) { frame.contentWindow.postMessage({ forgefirm: 1, id: m.id, ok: ok, value: ok ? v : undefined, error: ok ? undefined : v }, '*'); };
  if (m.call === 'frame.height') { var px = Math.max(200, Math.min(1400, Math.round(m.args.px))); frame.style.height = px + 'px'; return reply(true, { px: px }); }
  post('/bridge', { call: m.call, args: m.args, units: UNITS }).then(function (r) { reply(r.ok, r.ok ? r.value : r.error); });
});
function btn(id, path, body) { document.getElementById(id).onclick = function () { post(path, body && body()).then(show); }; }
function show(s) {
  document.getElementById('st').textContent = 'lid ' + (s.lid ? 'closed' : 'open') + ' · ' + (s.job || 'no job') + (s.alarm ? ' · alarm ' + s.alarm : '') + (s.homed ? '' : ' · not homed');
  document.getElementById('lid').textContent = s.lid ? 'Open the lid' : 'Close the lid';
  document.getElementById('home').textContent = s.homed ? 'Unhome' : 'Home';
  document.getElementById('cool').textContent = 'Cooling: ' + (s.cool ? 'OK' : 'warming up');
}
btn('press', '/fake/press');
btn('lid', '/fake/lid');
btn('home', '/fake/home');
btn('cool', '/fake/cool');
btn('alarm', '/fake/alarm');
document.getElementById('theme').onclick = function () { document.body.classList.toggle('dark'); this.textContent = document.body.classList.contains('dark') ? 'Light' : 'Dark'; };
setInterval(function () { post('/fake/state').then(show); }, 700);

/* The scenario: the page, driven as an operator would. */
var waiting = {}, nid = 0, log = [];
function ask(op) {
  op.harness = 1; op.id = ++nid;
  return new Promise(function (ok) { waiting[op.id] = ok; frame.contentWindow.postMessage(op, '*'); });
}
function sleep(ms) { return new Promise(function (ok) { setTimeout(ok, ms); }); }
function until(what, test, ms) {
  var end = Date.now() + (ms || 15000);
  return (function again() {
    return ask({ op: 'read' }).then(function (r) {
      if (test(r)) return r;
      if (Date.now() > end) throw new Error('timed out waiting for ' + what + '; the page shows ' + JSON.stringify(r).slice(0, 900));
      return sleep(250).then(again);
    });
  })();
}
function step(name, fn) { return function (prev) { return Promise.resolve(fn(prev)).then(function (v) { log.push('PASS ' + name); return v; }); }; }
function run() {
  var s = Promise.resolve();
  SCENARIO_STEPS.forEach(function (st) { s = s.then(step(st[0], st[1])); });
  return s.then(function () { return { ok: true, log: log }; }, function (e) { log.push('FAIL ' + String(e)); return { ok: false, log: log }; })
    .then(function (v) { document.getElementById('verdict').textContent = (v.ok ? 'PASS\n' : 'FAIL\n') + v.log.join('\n'); document.title = v.ok ? 'PASS' : 'FAIL'; return post('/result', v); });
}
@@STEPS@@
if (SCENARIO) setTimeout(run, 500);
</script></body></html>
"""

DRIVER = r"""
<script>
/* The scenario's driver: added to this copy of the page only. */
window.addEventListener('message', function (ev) {
  var m = ev.data;
  if (ev.source !== window.parent || !m || m.harness !== 1) return;
  var r = {}, q = function (s) { return document.querySelector(s); };
  try {
    if (m.op === 'click') q(m.sel).click();
    if (m.op === 'set') { var el = q(m.sel); el.focus(); el.value = m.value; el.dispatchEvent(new Event(m.ev || 'change', { bubbles: true })); el.blur(); }
    if (m.op === 'read' || m.op) {
      r = { title: q('#runTitle').textContent, sub: q('#runSub').textContent, note: q('#runNote').textContent,
            sample: q('#sample').textContent, saved: q('#saved').textContent, locked: q('#editor').disabled,
            itemPaths: q('#pvItem').querySelectorAll('path').length, bedSlots: q('#pvBed').querySelectorAll('.slot').length,
            bedTexts: [].map.call(q('#pvBed').querySelectorAll('.slot text'), function (e) { return e.textContent; }),
            errors: [].map.call(document.querySelectorAll('#pvMsgs .msg.bad'), function (e) { return e.textContent; }),
            warnings: [].map.call(document.querySelectorAll('#pvMsgs .msg.warn'), function (e) { return e.textContent; }),
            start: !q('#bStart').hidden && !q('#bStart').disabled, stopShown: !q('#bStop').hidden, stopNowShown: !q('#bStopNow').hidden,
            progress: !q('#progress').hidden, size: q('[data-k="size_mm"]').value, sizeUnit: q('[data-ul="len"]').textContent,
            history: q('#histBody').textContent, info: q('#pvInfo').textContent, counters: q('#counters').textContent,
            font: q('#fontName').textContent, pickerFonts: q('#fontpick').querySelectorAll('button[data-font] svg path').length,
            height: document.documentElement.scrollHeight };
    }
  } catch (e) { r.error = String(e); }
  parent.postMessage({ harness: 1, id: m.id, r: r }, '*');
});
</script>
"""

SCENARIO_STEPS = r"""
var SCENARIO_STEPS = [
  ['the page loads and is ready', function () { return until('ready', function (r) { return r.title === 'Ready to start' && r.itemPaths > 0; }); }],
  ['the example profile shows its first serial', function () { return until('sample', function (r) { return r.sample.indexOf('SN 000001') >= 0; }); }],
  ['a check digit added to the text shows in the sample', function () {
    return ask({ op: 'set', sel: '#text', value: 'SN {Serial}-{Luhn}', ev: 'input' })
      .then(function () { return until('the check digit', function (r) { return r.sample.indexOf('SN 000001-8') >= 0 && r.saved === 'Saved'; }); }); }],
  ['three items across: the sample names the last', function () {
    return ask({ op: 'set', sel: '[data-k="grid.cols"]', value: '3' })
      .then(function () { return until('three items', function (r) { return r.sample.indexOf('of 3') >= 0 && r.sample.indexOf('SN 000003') >= 0 && r.bedSlots === 3; }); }); }],
  ['the bed shows every line of every item, and nothing else', function () {
    return ask({ op: 'set', sel: '#text', value: 'SN {Serial}-{Luhn}\nLOT 7', ev: 'input' })
      .then(function () { return ask({ op: 'click', sel: '#pvMode button[data-v="bed"]' }); })
      .then(function () {
        return until('the bed', function (r) {
          return r.bedTexts.length === 6 && r.bedTexts.indexOf('SN 000003-4') >= 0 &&
            r.bedTexts.filter(function (t) { return t === 'LOT 7'; }).length === 3 &&
            r.bedTexts.every(function (t) { return !/^\d+$/.test(t); });
        });
      }).then(function () { return ask({ op: 'click', sel: '#pvMode button[data-v="item"]' }); }); }],
  ['a text size in the panel units', function () {
    return ask({ op: 'read' }).then(function (r) {
      if (UNITS === 'imperial' && (r.sizeUnit !== 'in' || Math.abs(parseFloat(r.size) - 3 / 25.4) > 0.001)) throw new Error('size ' + r.size + ' ' + r.sizeUnit);
      if (UNITS !== 'imperial' && (r.sizeUnit !== 'mm' || parseFloat(r.size) !== 3)) throw new Error('size ' + r.size + ' ' + r.sizeUnit);
    }); }],
  ['the font picker shows every font drawn in itself', function () {
    return ask({ op: 'click', sel: '#fontBtn' }).then(function () { return until('samples', function (r) { return r.pickerFonts >= 40; }, 30000); })
      .then(function () { return ask({ op: 'click', sel: '#fontpick button[data-font="roboto-700"]' }); })
      .then(function () { return until('the new font', function (r) { return r.font === 'Roboto Bold' && r.saved === 'Saved'; }); }); }],
  ['start sends the first cycle and asks for the press', function () {
    return ask({ op: 'click', sel: '#bStart' }).then(function () {
      return until('the press', function (r) { return r.title === 'Press the button on the machine' && r.locked && r.stopShown; }); }); }],
  ['the press starts the marking, with its progress', function () {
    return post('/fake/press').then(function () { return until('marking', function (r) { return r.title === 'Marking cycle 1' && r.stopNowShown; }); })
      .then(function () { return until('progress', function (r) { return r.progress; }); }); }],
  ['the cycle ends and asks for the reload', function () {
    return until('the reload', function (r) { return r.title === 'Open the lid and change the items' && r.note.indexOf('Cycle 1 is done') >= 0; }, 20000); }],
  ['the lid opened and closed sends cycle 2 with the next numbers', function () {
    return post('/fake/lid').then(function () { return sleep(2600); }).then(function () { return post('/fake/lid'); })
      .then(function () { return until('cycle 2', function (r) { return r.title === 'Press the button on the machine' && r.sub.indexOf('SN 000004') >= 0 && r.sub.indexOf('SN 000006') >= 0; }); }); }],
  ['stop before the press gives cycle 2 back', function () {
    return ask({ op: 'click', sel: '#bStop' }).then(function () {
      return until('stopped', function (r) { return r.title === 'Ready to start' && !r.locked && r.note.indexOf('Cycle 2 was not marked') >= 0 && r.sample.indexOf('SN 000004') >= 0; }); }); }],
  ['the history shows cycle 1', function () {
    return ask({ op: 'click', sel: '#history summary' }).then(function () {
      return until('the history', function (r) { return r.history.indexOf('SN 000001') >= 0 && r.history.indexOf('Marked') >= 0; }); }); }],
  ['an unknown field is named in the preview and Start waits', function () {
    return ask({ op: 'set', sel: '#text', value: 'SN {Serail}', ev: 'input' }).then(function () {
      return until('the error', function (r) { return r.errors.join(' ').indexOf('{Serail}') >= 0 && !r.start && r.title === 'Not ready'; }); }); }],
  ['a machine that is not homed cannot start', function () {
    return ask({ op: 'set', sel: '#text', value: 'SN {Serial}', ev: 'input' }).then(function () { return post('/fake/home'); })
      .then(function () { return until('not homed', function (r) { return r.sub.indexOf('Home the machine') >= 0 && !r.start; }); })
      .then(function () { return post('/fake/home'); }); }]
];
"""


class State:
    def __init__(self, units):
        self.m = Machine()
        self.dir = tempfile.mkdtemp(prefix="serializer-harness-")
        self.store = store.Store(self.dir)
        self.cat = fonts.Catalog(os.path.join(ROOT, "share"))
        self.runner = runner.Runner(self.m, self.store, self.cat, fake.ID)
        self.api = api.Api(self.store, self.cat, self.runner, self.m)
        self.units = units
        self.result = None
        threading.Thread(target=self.runner.loop, daemon=True).start()
        threading.Thread(target=self.runner.events_loop, daemon=True).start()

    def fake_state(self):
        m = self.m
        with m.lock:
            m._advance()
            return {"lid": m.lid, "job": m.job_state, "alarm": m.alarm, "homed": m.homed_axes == 7, "cool": m.fire_ok}

    def bridge(self, call, args):
        if call == "self":
            return {"id": fake.ID, "version": "0.1.0", "tier": "official", "units": self.units,
                    "capabilities": ["ui", "machine.read", "events", "motion.job", "sender.keep_out", "job_time.run"]}
        if call == "machine.status":
            return self.m.status()
        if call == "machine.cool":
            return self.m.cool()
        if call == "motion.job.state":
            return self.m.job_record()
        if call == "service.call":
            try:
                return {"status": 200, "body": self.api.handle(args["method"], args["path"], args.get("body") or {})}
            except api.CallError as e:
                return {"status": e.status, "body": {"error": e.words}}
        raise ValueError("the stand-in panel has no %s" % call)


def page_html(driver):
    with open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8") as f:
        page = f.read()
    if driver:
        page = page.replace("</body>", DRIVER + "</body>")
    return page


def make_handler(st, scenario):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, data):
            data = data.encode() if isinstance(data, str) else data
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] != "/":
                return self._send(404, "text/plain", "no")
            # ?driver=1 adds the scenario's driver without running the scenario, so the page can be driven by hand
            # from the harness page (ask({op: ...}) in its console).
            driver = scenario or "driver=1" in self.path
            page = json.dumps(page_html(driver)).replace("</", "<\\/")      # not the end of this script
            html = (HARNESS.replace("@@PAGE@@", page)
                    .replace("@@POLICY@@", json.dumps(POLICY)).replace("@@UNITS@@", json.dumps(st.units))
                    .replace("@@SCENARIO@@", "true" if scenario else "false")
                    .replace("@@STEPS@@", SCENARIO_STEPS))
            self._send(200, "text/html; charset=utf-8", html)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            m = st.m
            if self.path == "/bridge":
                try:
                    out = {"ok": True, "value": st.bridge(body["call"], body.get("args") or {})}
                except Exception as e:
                    out = {"ok": False, "error": str(e)}
                return self._send(200, "application/json", json.dumps(out))
            if self.path == "/fake/press":
                m.press()
            elif self.path == "/fake/lid":
                m.set_lid(not m.lid)
            elif self.path == "/fake/home":
                with m.lock:
                    m.homed_axes = 4 if m.homed_axes == 7 else 7
            elif self.path == "/fake/cool":
                with m.lock:
                    m.fire_ok = not m.fire_ok
            elif self.path == "/fake/alarm":
                with m.lock:
                    m.alarm = 0
            elif self.path == "/result":
                st.result = body
            elif self.path != "/fake/state":
                return self._send(404, "text/plain", "no")
            return self._send(200, "application/json", json.dumps(st.fake_state()))
    return H


def end_browser(profile):
    """Ends every process of the browser run on this profile: a launcher's PID is not always the browser's."""
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%s*' } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                  % os.path.basename(profile))
        subprocess.run(["powershell", "-NoProfile", "-Command", script], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["pkill", "-f", profile], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def serve_calls(st, port):
    """The service's side of forgectrl's dev server (tools/devserver.py --call-port): the page's calls, in the
    host's form, answered by the service's code on the fake machine."""
    class C(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            try:
                code, out = 200, st.api.handle(self.command, self.path, body)
            except api.CallError as e:
                code, out = e.status, {"error": e.words}
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _do
    srv = ThreadingHTTPServer(("127.0.0.1", port), C)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def find_browser(name):
    for p in BROWSERS[name]:
        if os.path.exists(p):
            return p
    found = shutil.which(name)
    if found:
        return found
    raise SystemExit("no %s found" % name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--units", default="metric", choices=("metric", "imperial"))
    ap.add_argument("--scenario", action="store_true")
    ap.add_argument("--browser", default="chrome", choices=sorted(BROWSERS))
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--call-port", type=int, default=0,
                    help="also answer forgectrl's dev server (tools/devserver.py --call-port) on this port")
    a = ap.parse_args()
    st = State(a.units)
    if a.call_port:
        serve_calls(st, a.call_port)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(st, a.scenario))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % a.port
    if not a.scenario:
        print("the stand-in panel is at %s (data in %s)" % (url, st.dir))
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            shutil.rmtree(st.dir, ignore_errors=True)
            return
    prof = tempfile.mkdtemp(prefix="serializer-browser-")
    exe = find_browser(a.browser)
    if a.browser == "chrome":
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--user-data-dir=" + prof, url]
    else:
        cmd = [exe, "-headless", "-no-remote", "-new-instance", "-profile", prof, url]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.monotonic() + a.timeout
    try:
        while st.result is None and time.monotonic() < end:
            time.sleep(0.5)
    finally:
        proc.kill()
        proc.wait()
        end_browser(prof)
        srv.shutdown()
        time.sleep(0.5)
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(st.dir, ignore_errors=True)
    if st.result is None:
        print("NORESULT: the page did not finish in %.0f s" % a.timeout)
        sys.exit(2)
    print("\n".join(st.result["log"]))
    print("PASS" if st.result["ok"] else "FAIL")
    sys.exit(0 if st.result["ok"] else 1)


if __name__ == "__main__":
    main()
