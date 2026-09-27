#!/usr/bin/env python3
# filename: test_s3b_wrapper.py
# description: Sprint26 S3b.4 — rc_run_capture_cycle.sh restart wrapper, driven by a stub runtime.
"""
Sprint26 S3b.4 (PLAN_S3b.md H6, H7).

Runs the REAL BM_Devel_Pi/rc_run_capture_cycle.sh with its test hooks pointing
at a stub "python" that exits with scripted codes, and a stub sleep that only
records the backoff. Pins:
  - per_boot exits (0, 1, 2) and SIGTERM (143) never loop;
  - 70 restarts with backoff 10, 20, 40, 80, 160 s; 71 restarts after 5 s;
  - a signal death (137) restarts only while the stay_on marker exists;
  - the 6th restart within 10 min becomes one --crashloop run, then the
    wrapper ends;
  - a SIGTERM to the wrapper reaches the runtime and ends the loop.

Run (repo root):
  python3 -m unittest tests.test_s3b_wrapper -v
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = os.path.join(REPO_ROOT, "BM_Devel_Pi", "rc_run_capture_cycle.sh")

STUB_PYTHON = r"""#!/bin/bash
# Stub runtime: record argv, then act out the next scripted step.
S="$STUB_DIR"
echo "$*" >> "$S/calls"
n=$(cat "$S/count" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$S/count"
step=$(sed -n "${n}p" "$S/steps")
case "$step" in
  marker137) echo $$ > "$BMCAM_STAY_ON_MARKER"; kill -9 $$ ;;
  kill137) kill -9 $$ ;;
  term_parent) kill -TERM $PPID; sleep 0.5; exit 0 ;;
  "") exit 0 ;;
  *) exit "$step" ;;
esac
"""

STUB_SLEEP = """#!/bin/bash
echo "$1" >> "$STUB_DIR/sleeps"
"""


class Wrapper(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="s3b_wrapper_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = os.path.join(self.dir, "app")
        os.makedirs(self.app)
        for name, text in (("python", STUB_PYTHON), ("sleep", STUB_SLEEP)):
            path = os.path.join(self.dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(path, 0o755)
        self.marker = os.path.join(self.dir, "stay_on.marker")
        self.env = dict(os.environ, STUB_DIR=self.dir, BMCAM_APP_DIR=self.app,
                        BMCAM_PYTHON=os.path.join(self.dir, "python"),
                        BMCAM_SLEEP=os.path.join(self.dir, "sleep"),
                        BMCAM_STAY_ON_MARKER=self.marker)

    def wrap(self, steps):
        with open(os.path.join(self.dir, "steps"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(str(s) for s in steps) + "\n")
        proc = subprocess.run(["bash", WRAPPER], env=self.env, timeout=60)
        return proc.returncode

    def read(self, name):
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return fh.read().split("\n")[:-1]

    def log(self):
        logs = os.listdir(os.path.join(self.app, "cron_logs"))
        self.assertEqual(len(logs), 1)
        with open(os.path.join(self.app, "cron_logs", logs[0]), encoding="utf-8") as fh:
            return fh.read()

    def test_per_boot_exits_never_loop(self):
        for code in (0, 1, 2):
            with self.subTest(code=code):
                for name in ("calls", "count", "sleeps"):
                    if os.path.exists(os.path.join(self.dir, name)):
                        os.remove(os.path.join(self.dir, name))
                self.assertEqual(self.wrap([code, 0]), code)
                self.assertEqual(self.read("calls"), ["-u rc_progressive_jpeg.py --transmit"])
                self.assertEqual(self.read("sleeps"), [])

    def test_crash_restarts_with_backoff(self):
        self.assertEqual(self.wrap([70, 70, 0]), 0)
        self.assertEqual(len(self.read("calls")), 3)
        self.assertEqual(self.read("sleeps"), ["10", "20"])
        self.assertIn("stay_on restart 2", self.log())

    def test_rss_restart_after_5s(self):
        self.wrap([71, 0])
        self.assertEqual(self.read("sleeps"), ["5"])

    def test_signal_death_restarts_only_with_the_marker(self):
        self.wrap(["marker137", 0])
        self.assertEqual(len(self.read("calls")), 2)
        self.assertFalse(os.path.exists(self.marker))     # the wrapper clears it
        os.remove(os.path.join(self.dir, "calls"))
        os.remove(os.path.join(self.dir, "count"))
        self.assertEqual(self.wrap(["kill137", 0]), 137)   # per_boot death: done
        self.assertEqual(len(self.read("calls")), 1)

    def test_crash_loop_falls_back_once(self):
        code = self.wrap([70] * 6 + [0, 0])
        calls = self.read("calls")
        self.assertEqual(len(calls), 7)
        self.assertEqual(calls[-1], "-u rc_progressive_jpeg.py --transmit --crashloop")
        self.assertEqual(self.read("sleeps"), ["10", "20", "40", "80", "160"])
        self.assertEqual(code, 0)
        self.assertIn("CRASH LOOP", self.log())

    def test_sigterm_to_the_wrapper_ends_the_loop(self):
        t0 = time.monotonic()
        self.wrap(["term_parent", 0])
        self.assertEqual(len(self.read("calls")), 1)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertIn("SIGTERM: passing it to the runtime", self.log())



class WrapperReview(Wrapper):  # review S3b #6
    def test_stale_marker_never_restarts_a_per_boot_death(self):
        with open(self.marker, "w") as fh:
            fh.write("1\n")                 # left by an earlier stay_on run this boot
        self.assertEqual(self.wrap(["kill137", 0]), 137)
        self.assertEqual(len(self.read("calls")), 1)


if __name__ == "__main__":
    unittest.main()
