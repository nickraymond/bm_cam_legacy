#!/usr/bin/env python3
# filename: test_s4_config_restart.py
# description: Sprint26 S4 b.4 (G10g) — a next-boot setting on stay_on: the loop exits 72, the wrapper restarts in 5 s outside the crash-loop cap.
"""
Sprint26 S4 commit b.4 (PLAN_S4.md G10g; consensus NEW-1 / B major 2).

Pins:
  - the dispatcher notes next-boot keys a set/reset changed (restart_requested);
    next-action keys do not request a restart;
  - the stay_on loop returns EXIT_CONFIG (72) at its next idle tick when the
    dispatcher asks, before any action;
  - rc_run_capture_cycle.sh restarts on 72 after 5 s and never counts it toward
    the 5-in-10-min crash-loop cap (6 config restarts in a row: no --crashloop).

Run (repo root):
  python3 -m unittest tests.test_s4_config_restart -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_supervisor as sup  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
import tests.test_s3b_wrapper as WR  # noqa: E402  (module: its TestCase is not re-collected)
from tests.test_s4_dispatch import Rig  # noqa: E402


class Dispatcher(unittest.TestCase):
    def test_next_boot_keys_request_a_restart(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        self.assertEqual(r.daemon.v9_dispatch.restart_requested, [])
        r.send({"id": 1_000_002, "c": "set", "kv": {"mode.interval_s": 600,
                                                      "network.default": "ap"}})
        self.assertEqual(sorted(r.daemon.v9_dispatch.restart_requested),
                         ["mode.interval_s", "network.default"])


class FakeDaemon:
    def __init__(self, flag_after):
        self.ticks = 0
        self.flag_after = flag_after
        self.v9_dispatch = type("D", (), {"restart_requested": []})()

    def process_pending(self):
        self.ticks += 1
        if self.ticks >= self.flag_after:
            self.v9_dispatch.restart_requested = ["mode.run"]
        return []

    def drain_acks(self, clock=None):
        return 0

    def drain_console(self, sleep_fn=None):
        return 0

    def reader_health(self):
        return True, ""


class Loop(unittest.TestCase):
    def setUp(self):
        sup.STOP.update(requested=False, signal=None)   # a stay_on test may leave it set

    def test_loop_exits_72_before_any_action(self):
        daemon = FakeDaemon(flag_after=3)
        boot = type("B", (), {"command_state": None, "save_local": False})()
        t = [0.0]
        actions = []
        code = quiet(sup._loop, boot, daemon, lambda b, s: actions.append(1), lambda: {},
                     0, 0, lambda s: None, lambda: t[0],
                     lambda s: t.__setitem__(0, t[0] + s))
        self.assertEqual(code, sup.EXIT_CONFIG)
        self.assertEqual(code, 72)
        self.assertEqual(daemon.ticks, 3)
        self.assertEqual(actions, [])


class WrapperConfigRestart(unittest.TestCase):
    # The S3b harness methods only (subclassing would re-run the S3b tests).
    setUp, wrap, read, log = WR.Wrapper.setUp, WR.Wrapper.wrap, WR.Wrapper.read, WR.Wrapper.log

    def test_72_restarts_in_5s_outside_the_cap(self):
        code = self.wrap([72] * 6 + [0])
        self.assertEqual(code, 0)
        calls = self.read("calls")
        self.assertEqual(len(calls), 7)
        self.assertFalse(any("--crashloop" in c for c in calls))
        self.assertEqual(self.read("sleeps"), ["5"] * 6)
        self.assertIn("not counted toward the crash-loop cap", self.log())

    def test_crashes_still_count_around_config_restarts(self):
        code = self.wrap([70, 72, 70, 70, 70, 70, 70, 0])
        calls = self.read("calls")
        self.assertIn("--crashloop", calls[-1])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
