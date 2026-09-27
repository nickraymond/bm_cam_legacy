#!/usr/bin/env python3
# filename: test_s3b_capture_stop.py
# description: Sprint26 S3b.5 — a stay_on stop request ends the capture-retry wait (bounded wait).
"""
Sprint26 S3b.5 (PLAN_S3b.md H6; DESIGN §4 "A capture retry gets a bounded wait").

Pins:
  - without a stop check (per_boot, legacy) the retry wait is ONE plain sleep
    of the full delay, exactly as before;
  - with a stop check, the wait polls it at least once a second and returns
    True as soon as a stop is requested;
  - in the capture retry loop, a stop during the wait raises before the next
    attempt starts (the camera is not run again; no <WS a=fail>);
  - install_stop_flag() points rc_capture at the supervisor's SIGTERM flag.

Run (repo root):
  python3 -m unittest tests.test_s3b_capture_stop -v
"""

import os
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_capture  # noqa: E402
import rc_supervisor  # noqa: E402


class FakeTime:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class RetryWait(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, rc_capture, "stop_check", None)
        self.ft = FakeTime()
        p = mock.patch.object(rc_capture, "time", types.SimpleNamespace(
            monotonic=self.ft.monotonic, sleep=self.ft.sleep))
        p.start()
        self.addCleanup(p.stop)

    def test_legacy_is_one_plain_sleep(self):
        rc_capture.stop_check = None
        self.assertFalse(rc_capture._retry_wait(60))
        self.assertEqual(self.ft.sleeps, [60])

    def test_full_wait_in_one_second_steps(self):
        rc_capture.stop_check = lambda: False
        self.assertFalse(rc_capture._retry_wait(3.5))
        self.assertEqual(self.ft.sleeps, [1.0, 1.0, 1.0, 0.5])

    def test_stop_ends_the_wait_within_a_second(self):
        rc_capture.stop_check = lambda: self.ft.t >= 2.0
        self.assertTrue(rc_capture._retry_wait(60))
        self.assertLessEqual(self.ft.t, 3.0)

    def test_retry_loop_stops_before_the_next_attempt(self):
        calls = []

        def camera(cmd, out, err, label):
            calls.append(label)
            raise subprocess.TimeoutExpired(cmd, 30)
        rc_capture.stop_check = lambda: len(self.ft.sleeps) >= 1
        statuses = []
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(rc_capture, "_run_camera_command_with_timeout", camera), \
                mock.patch.object(rc_capture, "_send_capture_status",
                                  lambda **kw: statuses.append(kw["action"])), \
                mock.patch.object(rc_capture, "debug_print", lambda *a, **k: None):
            with self.assertRaisesRegex(RuntimeError, r"stopped \(SIGTERM\) before attempt 2/4"):
                rc_capture._run_native_full_capture(
                    "/usr/bin/rpicam-still", os.path.join(d, "n.jpg"), 4608, 2592, 95,
                    os.path.join(d, "n"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("fail", statuses)


class InstallStopFlag(unittest.TestCase):
    def test_points_rc_capture_at_the_flag(self):
        self.addCleanup(setattr, rc_capture, "stop_check", None)
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))
        with mock.patch("builtins.print"):
            rc_supervisor.install_stop_flag()
        self.assertIs(rc_capture.stop_check, rc_supervisor.stop_requested)


if __name__ == "__main__":
    unittest.main()
