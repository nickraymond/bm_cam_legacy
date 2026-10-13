#!/usr/bin/env python3
# filename: test_pool_cmd.py
# description: Pool test 2026-10-13 — tools/pool_cmd.py against a fake spotter-monitor (cmd.txt consume + console log answers).
"""
tools/pool_cmd.py gate, no hardware: a fake spotter-monitor thread per Spotter dir empties
cmd.txt (like tools/spotter_serial_monitor.py) and appends the unit's answer to the console
log in the real line format ("<UTC>Z <epoch> <node>, [bmcamNNN] OK id=N pool <verb> ...").

  1. both + OK: one persisted id for both units, JSON/console line exact, exit 0;
  2. ids increase across calls and survive in the id file; start at 90001;
  3. ERR -> exit 1; one unit silent -> exit 4; monitor down (cmd.txt never emptied) -> exit 3;
  4. preview PV lines are counted, not printed; an ack for another id is ignored;
  5. refusals (non-rig unit, bad note, bad --lines, oversize JSON) exit 2 and write nothing.

Run (repo root):  python3 -m pytest -q tests/test_pool_cmd.py
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import pool_cmd as pc  # noqa: E402

SPOTS = {"bmcam003": "SPOT-33507C", "bmcam004": "SPOT-31593C"}


class FakeMonitor:
    """Consumes <root>/<SPOT>/cmd.txt; answers per `behaviour[unit]`:
    "ok" | "err" | "silent" | "dead" (never consumes) | "preview" (PV lines then OK)."""

    def __init__(self, root, behaviour):
        self.root, self.behaviour = root, behaviour
        self.sent = {u: [] for u in SPOTS}
        self.stop = threading.Event()
        for s in SPOTS.values():
            os.makedirs(os.path.join(root, s), exist_ok=True)
        self.t = threading.Thread(target=self.run, daemon=True)

    def log(self, spot, text):
        path = pc.console_log(self.root, spot)
        with open(path, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} Message: "
                    f"{time.time():.3f} 53171fa3d81a8e6f, {text}\n")

    def run(self):
        while not self.stop.is_set():
            for unit, spot in SPOTS.items():
                b = self.behaviour.get(unit, "ok")
                path = os.path.join(self.root, spot, "cmd.txt")
                if b == "dead" or not os.path.exists(path):
                    continue
                with open(path, "r+") as f:
                    line = f.read().strip()
                    if not line:
                        continue
                    f.seek(0)
                    f.truncate()
                self.sent[unit].append(line)
                cid = json.loads(line.split(" ", 3)[3].rsplit(" ", 2)[0])["id"]
                verb = json.loads(line.split(" ", 3)[3].rsplit(" ", 2)[0])["v"]
                self.log(spot, line)                                     # console echo
                self.log(spot, f"[{unit}] OK id={cid + 1000} pool other")  # someone else's id
                if b == "ok":
                    self.log(spot, f"[{unit}] OK id={cid} pool {verb} state=idle")
                elif b == "err":
                    self.log(spot, f"[{unit}] ERR id={cid} pool {verb} busy")
                elif b == "slow_ok":       # sweep: answered only when done (#160), here after 2 s
                    threading.Timer(2.0, self.log, (spot, f"[{unit}] OK id={cid} pool {verb} dir=sweep/x n=5/5")).start()
                elif b == "busy_loop":     # sweep refused while the loop phase is active
                    self.log(spot, f"[{unit}] ERR id={cid} pool {verb} busy phase=loop")
                elif b == "preview":
                    self.log(spot, f"[{unit}] OK id={cid} pool preview img=181500")
                    self.log(spot, f"[{unit}] PVH {unit} 181500 n=2 bytes=10 sha256={'0' * 64} w=1 h=1 q=1")
                    self.log(spot, f"[{unit}] PV {unit} 181500 1/2 00000000 AAAA")
                    self.log(spot, f"[{unit}] PV {unit} 181500 2/2 00000000 AAAA")
                    self.log(spot, f"[{unit}] PVE {unit} 181500")
            time.sleep(0.05)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join(2)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *args, wait="3"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                rc = pc.main(["--log-root", self.root, "--wait", wait, "--linger", "0.3"] + list(args))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue()


class TestSend(Base):
    def test_both_ok_one_id(self):
        with FakeMonitor(self.root, {}) as mon:
            rc, out = self.run_cmd("both", "status")
        self.assertEqual(rc, 0, out)
        for u in SPOTS:
            self.assertEqual(mon.sent[u], ['bm pub bmcam/cmd {"id":90001,"c":"pool","v":"status"} 1 1'])
            self.assertIn(f"id=90001 {u}: OK", out)
        with open(os.path.join(self.root, "pool_cmd_id")) as f:
            self.assertEqual(f.read().strip(), "90001")
        self.assertTrue(os.path.exists(os.path.join(self.root, "pool_cmd.log")))

    def test_ids_increase_and_fields(self):
        with FakeMonitor(self.root, {}) as mon:
            self.assertEqual(self.run_cmd("bmcam003", "mark", "card 2m")[0], 0)
            self.assertEqual(self.run_cmd("bmcam003", "cal", "--duration-s", "600", "--interval-s", "10")[0], 0)
            self.assertEqual(self.run_cmd("SPOT-33507C", "loop", "--iterations", "5", "--period-s", "60")[0], 0)
        ids = [int(re.search(r'"id":(\d+)', l).group(1)) for l in mon.sent["bmcam003"]]
        self.assertEqual(ids, [90001, 90002, 90003])
        self.assertIn('"note":"card_2m"', mon.sent["bmcam003"][0])
        self.assertIn('"duration_s":600,"interval_s":10', mon.sent["bmcam003"][1])
        self.assertIn('"iterations":5,"period_s":60', mon.sent["bmcam003"][2])
        self.assertEqual(mon.sent["bmcam004"], [])

    def test_err_exit_1(self):
        with FakeMonitor(self.root, {"bmcam004": "err"}):
            rc, out = self.run_cmd("both", "stop")
        self.assertEqual(rc, 1, out)
        self.assertIn("bmcam004: ERR", out)
        self.assertIn("bmcam003: OK", out)

    def test_silent_exit_4(self):
        with FakeMonitor(self.root, {"bmcam003": "silent"}):
            rc, out = self.run_cmd("both", "status", wait="1")
        self.assertEqual(rc, 4, out)
        self.assertIn("bmcam003: NO ANSWER", out)

    def test_monitor_down_exit_3(self):
        with FakeMonitor(self.root, {"bmcam003": "dead", "bmcam004": "dead"}):
            pc_consume = pc.write_cmd.__defaults__
            pc.write_cmd.__defaults__ = (0.5,)
            try:
                rc, out = self.run_cmd("both", "status", wait="1")
            finally:
                pc.write_cmd.__defaults__ = pc_consume
        self.assertEqual(rc, 3, out)
        self.assertIn("NOT-CONSUMED", out)

    def test_preview_lines_counted(self):
        with FakeMonitor(self.root, {"bmcam003": "preview"}) as mon:
            rc, out = self.run_cmd("bmcam003", "preview", "--lines", "3-7,12")
        self.assertEqual(rc, 0, out)
        self.assertIn('"lines":"3-7,12"', mon.sent["bmcam003"][0])
        self.assertIn("4 preview line(s)", out)
        self.assertNotIn("AAAA", out)


def lines_of_len(n):
    """A valid 'a,b,c' spec of exactly n chars."""
    out, i = "", 1
    while len(out) < n:
        out += ("," if out else "") + str(i)
        i += 2
    out = out[:n].rstrip(",")
    while len(out) < n:
        out += "1"
    return out


class TestSweep(Base):
    def test_sweep_waits_for_late_ok_with_90s_default(self):
        with FakeMonitor(self.root, {"bmcam003": "slow_ok", "bmcam004": "slow_ok"}) as mon:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                rc = pc.main(["--log-root", self.root, "--linger", "0.3", "both", "sweep"])   # no --wait
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("wait=90s", out.getvalue())
        for u in SPOTS:
            self.assertEqual(mon.sent[u], ['bm pub bmcam/cmd {"id":90001,"c":"pool","v":"sweep"} 1 1'])
            self.assertIn(f"id=90001 {u}: OK", out.getvalue())

    def test_sweep_refused_during_loop(self):
        with FakeMonitor(self.root, {"bmcam003": "busy_loop"}):
            rc, out = self.run_cmd("bmcam003", "sweep")
        self.assertEqual(rc, 1, out)
        self.assertIn("busy phase=loop", out)

    def test_other_verbs_keep_20s_default(self):
        with FakeMonitor(self.root, {}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                pc.main(["--log-root", self.root, "--linger", "0.3", "bmcam003", "status"])
        self.assertIn("wait=20s", out.getvalue())


class TestLinesLimit(Base):
    def test_max_lines_is_186_and_exactly_fits(self):
        self.assertEqual(pc.MAX_LINES_CHARS, 186)
        spec = lines_of_len(186)
        self.assertEqual(len(spec), 186)
        with FakeMonitor(self.root, {}) as mon:
            rc, out = self.run_cmd("bmcam003", "preview", "--lines", spec)
        self.assertEqual(rc, 0, out)
        sent = mon.sent["bmcam003"][0]
        self.assertEqual(len(sent) + 1, 256)              # the console line limit is what binds
        rc, out = self.run_cmd("bmcam003", "preview", "--lines", lines_of_len(187))
        self.assertEqual(rc, 2)
        self.assertIn("max 186", out)


class TestRefusals(Base):
    def test_refusals_write_nothing(self):
        for args in (["bmcam001", "status"], ["SPOT-33361C", "status"],
                     ["bmcam003", "mark", "bad;note"], ["bmcam003", "preview", "--lines", "3-x"],
                     ["bmcam003", "status", "--arg", "x=" + "a" * 300], ["bmcam003", "launch"],
                     ["bmcam003", "preview", "--lines", lines_of_len(pc.MAX_LINES_CHARS + 1)]):
            rc, out = self.run_cmd(*args)
            self.assertEqual(rc, 2, (args, out))
        for s in SPOTS.values():
            self.assertFalse(os.path.exists(os.path.join(self.root, s, "cmd.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "pool_cmd_id")))

    def test_id_range_guard(self):
        path = os.path.join(self.root, "pool_cmd_id")
        with open(path, "w") as f:
            f.write("99999\n")
        with self.assertRaises(SystemExit):
            pc.next_id(path)


if __name__ == "__main__":
    unittest.main()
