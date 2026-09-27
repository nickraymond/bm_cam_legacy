#!/usr/bin/env python3
# filename: test_s3a_time_read.py
# description: Sprint26 W6 — fresh Spotter time reads, monotonic check, drift-only clock step.
"""
Sprint26 W6 (DESIGN_supervisor.md §4 "Time", REVIEW_20260925.md K5).

Pins what the golden world cannot show (its Pi clock and Spotter always agree):
  - a fresh daemon read clears the raw buffer BEFORE its subscribe write, so a
    stamp buffered earlier is never returned; the legacy read still returns it;
  - a fresh read never returns a time older than the last one (ignored, keeps
    waiting);
  - the gate steps the system clock only on drift when min_clock_step_s is set,
    always when it is None (legacy);
  - the supervisor's first gate read of a process always steps; later reads
    are drift-gated.

Run (repo root):
  python3 -m unittest tests.test_s3a_time_read -v
"""

import contextlib
import datetime as dt
import io
import os
import struct
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

from tests.test_command_daemon import DaemonTestCase  # noqa: E402
import rc_supervisor  # noqa: E402
import spotter_time_sync  # noqa: E402

PROFILE = os.path.join(REPO_ROOT, "device_profiles", "bmcam003", "camera_schedule.yaml")
OLD = dt.datetime(2026, 9, 24, 15, 0, 0, tzinfo=dt.timezone.utc)
NEW = dt.datetime(2026, 9, 24, 16, 0, 0, tzinfo=dt.timezone.utc)


def stamp(utc):
    return b"spotter/utc-time" + struct.pack("<Q", int(utc.timestamp() * 1e6))


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class FreshDaemonRead(DaemonTestCase):
    def buffered_old_stamp(self):
        self.daemon.start()
        self.uart.inject(stamp(OLD))
        self._await(lambda: len(self.daemon._raw) >= 24, message="old stamp buffered")

    def inject_on_first_sleep(self, utc):
        sent = []

        def sleep_fn(_s):
            if not sent:
                sent.append(True)
                self.uart.inject(stamp(utc))
            import time
            time.sleep(0.02)
        return sleep_fn

    def test_legacy_read_returns_the_buffered_stamp(self):
        self.buffered_old_stamp()
        self.assertEqual(quiet(self.daemon.wait_for_spotter_utc, 3), OLD)

    def test_fresh_read_ignores_what_was_buffered_before_it(self):
        self.buffered_old_stamp()
        self.daemon.fresh_time_reads = True
        got = quiet(self.daemon.wait_for_spotter_utc, 3, sleep_fn=self.inject_on_first_sleep(NEW))
        self.assertEqual(got, NEW)

    def test_fresh_read_never_goes_back_in_time(self):
        self.daemon.start()
        self.daemon.fresh_time_reads = True
        self.daemon._last_utc = NEW
        with self.assertRaises(TimeoutError):
            quiet(self.daemon.wait_for_spotter_utc, 0.5, sleep_fn=self.inject_on_first_sleep(OLD))
        self.assertEqual(self.daemon._last_utc, NEW)


class DriftOnlyClockStep(unittest.TestCase):
    def gate(self, offset_s, min_step):
        steps = []
        now = dt.datetime.now(dt.timezone.utc)
        with mock.patch.object(spotter_time_sync, "set_system_clock_utc", steps.append):
            _allowed, info = quiet(
                spotter_time_sync.should_transmit_now_from_schedule, PROFILE,
                read_spotter_utc_fn=lambda **kw: now + dt.timedelta(seconds=offset_s),
                min_clock_step_s=min_step)
        return steps, info["set_system_clock"]

    def test_legacy_always_steps(self):
        steps, how = self.gate(0.0, None)
        self.assertEqual((len(steps), how), (1, "ok"))

    def test_small_drift_is_not_stepped(self):
        steps, how = self.gate(0.5, 2.0)
        self.assertEqual(steps, [])
        self.assertTrue(how.startswith("skipped: drift +0."), how)

    def test_real_drift_is_stepped(self):
        steps, how = self.gate(-30.0, 2.0)
        self.assertEqual((len(steps), how), (1, "ok"))


class SupervisorGateKwargs(unittest.TestCase):
    def test_first_read_steps_later_reads_are_drift_gated(self):
        boot = rc_supervisor.Boot({}, media="still", bm_commands_cfg={}, command_state=None,
                                  transmit=True, bench_commands=False)
        first = boot.gate_kwargs(None, {})
        second = boot.gate_kwargs(None, {})
        self.assertNotIn("min_clock_step_s", first)
        self.assertEqual(second["min_clock_step_s"], rc_supervisor.CLOCK_STEP_MIN_DRIFT_S)


if __name__ == "__main__":
    unittest.main()
