#!/usr/bin/env python3
# filename: test_s3b_review_fixes.py
# description: Sprint26 S3b — pins for the independent review's findings (#1-#6).
"""
Sprint26 S3b independent review (2026-09-27), one test per fixed finding:
  (#1, #3 are in tests/test_s3b_w10.py W10Review; #6 in tests/test_s3b_wrapper.py)
  #2 a restarted stay_on process keeps the last scheduled slot (no action at once);
  #4 a failing settings re-resolve keeps the last good settings (no crash);
  #5 quiet window skips do not starve O5 idle heal passes.

Run (repo root):
  python3 -m unittest tests.test_s3b_review_fixes -v
"""

import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_supervisor as sup  # noqa: E402
from tests.test_s3b_stay_on import Loop  # noqa: E402


class LoopReview(Loop):
    def test_restart_keeps_the_last_slot(self):
        sup.guard.sched_save(-100.0)       # a slot started 100 s before this process
        self.loop(self.action(duration=10), interval_s=600, stop_at=700)
        self.assertEqual([round(a["t"]) for a in self.actions], [500])

    def test_first_process_of_the_boot_runs_at_once(self):
        self.loop(self.action(duration=10), interval_s=600, stop_at=100)
        self.assertEqual([round(a["t"]) for a in self.actions], [0])
        self.assertEqual(sup.guard.sched_load(), 0.0)

    def test_failing_reresolve_keeps_last_good(self):
        good = self.settings_fn()
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyError("overlay")
            return good
        self.settings_fn = flaky
        code, _b, _d = self.loop(self.action(), interval_s=600, stop_at=1300)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.actions), 3)
        self.assertTrue(all(a["made"] == good["made"] for a in self.actions))

    def test_quiet_skips_do_not_starve_idle_heals(self):
        passes = []

        def fake_pass(boot, daemon, settings, tx_open_fn, clock, sleep_fn):
            passes.append(round(clock()))
            boot.summary = {"stage": "done"}
            return boot.summary
        self.state.pending_heals = [{"key": "aaaaaa", "n": [1], "id": 1, "wakes_left": 9}]
        with mock.patch.object(sup, "heal_pass", fake_pass):
            self.loop(self.action(duration=0, skip=True), interval_s=120, stop_at=700,
                      heal_tx_open_fn=lambda p: None)
        self.assertEqual(passes, [600])


if __name__ == "__main__":
    unittest.main()
