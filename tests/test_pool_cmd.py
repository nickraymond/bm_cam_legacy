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

# Pairing since Tue 2026-10-13 (Nick swapped the cameras, intentional) = tools/rig_map.json
SPOTS = {"bmcam003": "SPOT-31593C", "bmcam004": "SPOT-33507C"}


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
                elif b == "dup_then_ok":   # replay answered while running (TE2 10/13), final OK 2 s later
                    self.log(spot, f"[{unit}] OK id={cid} pool {verb} in_progress dup")
                    threading.Timer(2.0, self.log, (spot, f"[{unit}] OK id={cid} pool {verb} n=11 dt=58.2s dir=sweep/113005 best_lp=0.8")).start()
                elif b == "dup_only":      # still running past the wait
                    self.log(spot, f"[{unit}] OK id={cid} pool {verb} in_progress dup")
                elif b == "wrong_unit":    # the OTHER camera sits on this Spotter (stale map after a swap)
                    other = [u for u in SPOTS if u != unit][0]
                    self.log(spot, f"[{other}] OK id={cid} pool {verb} state=idle")
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
            self.assertEqual(self.run_cmd("SPOT-31593C", "loop", "--iterations", "5", "--period-s", "60")[0], 0)
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
    def test_sweep_waits_for_late_ok_with_180s_default(self):
        with FakeMonitor(self.root, {"bmcam003": "slow_ok", "bmcam004": "slow_ok"}) as mon:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                rc = pc.main(["--log-root", self.root, "--linger", "0.3", "both", "sweep"])   # no --wait
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("wait=180s", out.getvalue())
        for u in SPOTS:
            self.assertEqual(mon.sent[u], ['bm pub bmcam/cmd {"id":90001,"c":"pool","v":"sweep"} 1 1'])
            self.assertIn(f"id=90001 {u}: OK", out.getvalue())

    def test_sweep_lens_fields_passthrough(self):
        """Extended sweep (Pi contract 10/13): --lp list or --lo/--hi/--step, exact short JSON."""
        with FakeMonitor(self.root, {"bmcam003": "slow_ok"}) as mon:
            rc, out = self.run_cmd("bmcam003", "sweep", "--lp", "0,0.2,0.4,0.6,0.8,1,1.2,1.4,1.6,1.8,1.82", wait="5")
            self.assertEqual(rc, 0, out)
            rc, out = self.run_cmd("bmcam003", "sweep", "--lo", "0", "--hi", "1.8", "--step", "0.2", wait="5")
            self.assertEqual(rc, 0, out)
        self.assertEqual(mon.sent["bmcam003"], [
            'bm pub bmcam/cmd {"id":90001,"c":"pool","v":"sweep","lp":[0,0.2,0.4,0.6,0.8,1,1.2,1.4,1.6,1.8,1.82]} 1 1',
            'bm pub bmcam/cmd {"id":90002,"c":"pool","v":"sweep","lo":0,"hi":1.8,"step":0.2} 1 1'])

    def test_sweep_lens_refusals_mirror_the_pi(self):
        for args, msg in ((["--lp", "16"], "outside 0.0..15.0"),
                          (["--lp", ",".join(["0.1"] * 13)], "1..12"),
                          (["--lp", "0.6", "--lo", "0"], "not both"),
                          (["--lo", "0", "--hi", "1"], "all required"),
                          (["--lo", "1", "--hi", "0", "--step", "0.2"], "--step > 0 and --hi >= --lo"),
                          (["--lo", "0", "--hi", "2.4", "--step", "0.2"], "13 positions > 12"),
                          (["--lp", "a,b"], "comma list")):
            rc, out = self.run_cmd("bmcam003", "sweep", *args)
            self.assertEqual(rc, 2, (args, out))
            self.assertIn(msg, out, args)
        rc, out = self.run_cmd("bmcam003", "status", "--lp", "1")
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, "pool_cmd_id")))   # nothing sent, no id used

    @unittest.skipUnless(os.path.exists(os.path.join(REPO_ROOT, "BM_Devel_Pi", "rc_focus_sweep.py")),
                         "Pi focus-sweep parser not in this checkout yet (feature/focus-sweep-20261013)")
    def test_sweep_json_parsed_by_the_pi(self):
        """Every sweep command pool_cmd builds is accepted by the Pi's own parse_command and gives the
        lens list we meant (runs once the Pi PR is merged; checked by hand against 032c7d5)."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
        import pool_sequence as ps
        import rc_focus_sweep as fs
        import argparse

        def args(**kw):
            a = argparse.Namespace(note=None, lines=None, duration_s=None, interval_s=None, iterations=None,
                                   period_s=None, arg=[], lp=None, lo=None, hi=None, step=None)
            a.__dict__.update(kw)
            return a
        for kw, want in ((dict(), [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 1.82]),
                         (dict(lp="0.6,0.7,0.8,0.9,1"), [0.6, 0.7, 0.8, 0.9, 1.0]),
                         (dict(lo=0.0, hi=1.8, step=0.2), [round(0.2 * i, 2) for i in range(10)]),
                         (dict(lp=",".join(f"{14.88 + i * 0.01:.2f}" for i in range(12))), None)):
            js, line = pc.build_json(99999, "sweep", args(**kw))
            self.assertLessEqual(len(line) + 1, pc.MAX_CONSOLE_LINE)
            cid, verb, fields = ps.parse_command(js.encode())
            got = fs.lens_list_from(fields.get("lp"), fields.get("lo"), fields.get("hi"), fields.get("step"))
            if want is not None:
                self.assertEqual([round(x, 2) for x in got], want, kw)

    def test_in_progress_dup_is_not_the_answer(self):
        """TE2 live run 10/13: the replay's 'in_progress dup' line must not end the wait."""
        with FakeMonitor(self.root, {"bmcam003": "dup_then_ok"}):
            rc, out = self.run_cmd("bmcam003", "sweep", wait="10")
        self.assertEqual(rc, 0, out)
        self.assertIn("still running (replay ack, not final)", out)
        self.assertIn("best_lp=0.8", out)                                   # waited for the FINAL OK
        self.assertIn("id=90001 bmcam003: OK", out)

    def test_in_progress_only_times_out(self):
        with FakeMonitor(self.root, {"bmcam003": "dup_only"}):
            rc, out = self.run_cmd("bmcam003", "sweep", wait="1.5")
        self.assertEqual(rc, 4, out)
        self.assertIn("NO ANSWER", out)

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


class TestRigMap(Base):
    """Tue 10/13 swap: the map comes from rig_map.json (or --map), defaults to the NEW pairing,
    and is printed at the top of every run."""

    def test_repo_map_file_is_the_new_pairing(self):
        with open(os.path.join(REPO_ROOT, "tools", "rig_map.json")) as f:
            raw = json.load(f)
        self.assertEqual({u: v["spot"] for u, v in raw.items() if not u.startswith("_")}, SPOTS)
        self.assertEqual(pc.parse_map(pc.DEFAULT_MAP), SPOTS)

    def test_header_and_routing_from_the_default_file(self):
        with FakeMonitor(self.root, {}) as mon:
            rc, out = self.run_cmd("bmcam003", "status")
        self.assertEqual(rc, 0, out)
        self.assertIn("rig map (", out)
        self.assertIn("bmcam003 -> SPOT-31593C (mote 53171fa3d81a8e6f bridge 0e582dd12c1e1480)", out)
        self.assertEqual(len(mon.sent["bmcam003"]), 1)                # went to SPOT-31593C's cmd.txt
        self.assertTrue(os.path.exists(os.path.join(self.root, "SPOT-31593C", "cmd.txt")))
        with open(os.path.join(self.root, "pool_cmd.log")) as f:
            self.assertIn("rig map", f.read())                          # header is in the log too

    def test_answer_from_the_wrong_camera_is_a_pairing_mismatch(self):
        """QE #169: a free pairing check on every command: the answering [bmcamNNN] must be the
        unit we addressed, else exit 5 loudly."""
        with FakeMonitor(self.root, {"bmcam003": "wrong_unit"}):
            rc, out = self.run_cmd("both", "status")
        self.assertEqual(rc, 5, out)
        self.assertIn("PAIRING MISMATCH: sent to bmcam003", out)
        self.assertIn("answered by [bmcam004]", out)
        self.assertIn("id=90001 bmcam004: OK", out)                     # the other unit is unaffected

    def test_builtin_default_when_no_file(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = pc.main(["--log-root", self.root, "--map-file", os.path.join(self.root, "none.json"),
                          "bmcam004", "status", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("rig map (built-in default", out.getvalue())
        self.assertIn("DRY-RUN bmcam004 (SPOT-33507C)", out.getvalue())

    def test_custom_file_and_map_override(self):
        mf = os.path.join(self.root, "map.json")
        with open(mf, "w") as f:
            json.dump({"bmcam003": "SPOT-AAAA01", "bmcam004": {"spot": "SPOT-BBBB02"}}, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pc.main(["--log-root", self.root, "--map-file", mf, "both", "status", "--dry-run"])
            pc.main(["--log-root", self.root, "--map-file", mf, "--map", "bmcam003=SPOT-CCCC03",
                     "bmcam003", "status", "--dry-run"])
        text = out.getvalue()
        self.assertIn(f"rig map ({mf})", text)
        self.assertIn("DRY-RUN bmcam003 (SPOT-AAAA01)", text)
        self.assertIn("DRY-RUN bmcam004 (SPOT-BBBB02)", text)
        self.assertIn("rig map (--map): bmcam003 -> SPOT-CCCC03", text)
        self.assertIn("DRY-RUN bmcam003 (SPOT-CCCC03)", text)
        self.assertFalse(os.path.exists(os.path.join(self.root, "pool_cmd.log")))   # dry runs write nothing

    def test_bad_maps_refused_nothing_sent(self):
        mf = os.path.join(self.root, "bad.json")
        for content in ('{"bmcam003": "SPOT-AAAA01", "bmcam004": "SPOT-AAAA01"}',      # same Spotter twice
                        '{"bmcam003": "not-a-spot"}', '{"bmcam003": ', '{}'):
            with open(mf, "w") as f:
                f.write(content)
            rc, out = self.run_cmd("--map-file", mf, "bmcam003", "status")
            self.assertEqual(rc, 2, (content, out))
            self.assertIn("REFUSED", out)
        self.assertFalse(os.path.exists(os.path.join(self.root, "pool_cmd_id")))


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
