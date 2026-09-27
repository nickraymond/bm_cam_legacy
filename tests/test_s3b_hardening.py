#!/usr/bin/env python3
# filename: test_s3b_hardening.py
# description: Sprint26 S3b.4 — watchdog, RSS ceiling, log rotation/pruning, stay_on marker.
"""
Sprint26 S3b.4 (PLAN_S3b.md H7-H10).

Pins:
  - CommandDaemon.reader_health: healthy while reading, unhealthy after a run
    of uart read errors (healthy again after a good read) and once the reader
    thread has stopped;
  - the stay_on loop exits EXIT_CRASH on a watchdog trip and EXIT_RSS when the
    RSS is over the ceiling after an action: shutdown -> close, never a halt;
  - the action log carries rss_now_kb;
  - rotate_stdout_if_big moves fds 1/2 to <log>.N and prunes only rotated
    pieces (rc_cycle_*.log.N), never the per_boot rc_cycle_*.log files
    (in a child process: it re-points the process's stdout);
  - the stay_on marker is written while run_stay_on runs and removed after.

Run (repo root):
  python3 -m unittest tests.test_s3b_hardening -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_stay_on_guard as guard  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_command_daemon import DaemonTestCase  # noqa: E402
from tests.test_s3b_stay_on import Loop  # noqa: E402


class ReaderHealth(DaemonTestCase):
    def test_read_error_run_then_recovery(self):
        self.daemon.WATCHDOG_READ_ERRORS = 3
        self.daemon.start()
        self.assertEqual(self.daemon.reader_health(), (True, ""))
        self.uart.fail_next_reads = 3
        self._await(lambda: not self.daemon.reader_health()[0], timeout=5,
                    message="watchdog trip")
        self.assertIn("read errors in a row", self.daemon.reader_health()[1])
        self._await(lambda: self.daemon.reader_health()[0], timeout=5, message="recovery")

    def test_stopped_reader_is_unhealthy(self):
        self.assertFalse(self.daemon.reader_health()[0])          # never started
        self.daemon.start()
        self.daemon.stop(join_timeout=1.0)
        self.assertEqual(self.daemon.reader_health(), (False, "reader thread not running"))


class LoopGuards(Loop):
    def test_watchdog_trip_exits_crash_without_halt(self):
        health = iter([(True, "")] * 5 + [(False, "reader thread not running")] * 99)
        with mock.patch.object(sup, "_reader_health", lambda d: next(health)):
            code, _boot, d = self.loop(self.action(), stop_at=1e9)
        self.assertEqual(code, sup.EXIT_CRASH)
        self.assertEqual(self.rec.calls, ["close"])
        self.assertTrue(d.stopped)

    def test_rss_ceiling_exits_after_the_action(self):
        with mock.patch.object(guard, "current_rss_kb", return_value=999_999):
            code, boot, _d = self.loop(self.action(), interval_s=600, stop_at=1e9)
        self.assertEqual(code, sup.EXIT_RSS)
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(self.rec.calls, ["close"])
        self.assertEqual(self.log_lines()[0]["rss_now_kb"], 999_999)

    def test_marker_while_running(self):
        marker = os.path.join(self.tmp.name, "marker")
        seen = []

        def act(boot, settings):
            seen.append(os.path.exists(marker))
            return self.action()(boot, settings)
        with mock.patch.object(guard, "MARKER_PATH", marker):     # over Loop's own patch
            self.loop(act, interval_s=600, stop_at=100)
        self.assertEqual(seen, [True])
        self.assertFalse(os.path.exists(marker))


ROTATE_CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import rc_stay_on_guard as g
log = sys.argv[2]
g._stdout_path = lambda: log            # macOS has no /proc/self/fd
print("x" * 200, flush=True)
g.rotate_stdout_if_big(limit=100, keep=3)
print("after rotation", flush=True)
"""


class Rotation(unittest.TestCase):
    def test_rotates_stdout_and_prunes_only_rotated_pieces(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(4):                       # per_boot logs: never pruned
                p = os.path.join(d, f"rc_cycle_2026092{i}T000000Z.log")
                with open(p, "w") as fh:
                    fh.write("old\n")
                os.utime(p, (1000 + i, 1000 + i))
            for i in range(1, 4):                    # older rotated pieces, oldest first
                p = os.path.join(d, f"rc_cycle_20260926T000000Z.log.{i}")
                with open(p, "w") as fh:
                    fh.write("piece\n")
                os.utime(p, (2000 + i, 2000 + i))
            log = os.path.join(d, "rc_cycle_20260927T000000Z.log")
            with open(log, "w") as out:
                subprocess.run([sys.executable, "-c", ROTATE_CHILD,
                                os.path.join(REPO_ROOT, "BM_Devel_Pi"), log],
                               stdout=out, stderr=subprocess.STDOUT, check=True, timeout=30)
            with open(log) as fh:
                first = fh.read()
            with open(log + ".1") as fh:
                second = fh.read()
            self.assertIn("continues in rc_cycle_20260927T000000Z.log.1", first)
            self.assertNotIn("after rotation", first)
            self.assertIn("after rotation", second)
            left = sorted(os.listdir(d))
            self.assertEqual(len([f for f in left if f.endswith(".log")]), 5)   # all kept
            pieces = [f for f in left if not f.endswith(".log")]
            self.assertEqual(len(pieces), 3)          # keep=3 newest pieces
            self.assertIn("rc_cycle_20260927T000000Z.log.1", pieces)
            self.assertNotIn("rc_cycle_20260926T000000Z.log.1", pieces)

    def test_small_or_non_file_stdout_is_left_alone(self):
        self.assertIsNone(guard.rotate_stdout_if_big(limit=10**12))

    def test_current_rss_is_positive(self):
        self.assertGreater(guard.current_rss_kb(), 0)
        self.assertTrue(guard.rss_over_ceiling(10, ceiling_kb=5))
        self.assertFalse(guard.rss_over_ceiling(None))


if __name__ == "__main__":
    unittest.main()
