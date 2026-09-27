#!/usr/bin/env python3
# filename: test_s3b_idle_heal.py
# description: Sprint26 S3b.6 — O5: stay_on sends pending heals after 10 min idle, no trigger.
"""
Sprint26 S3b.6 (DESIGN §10 O5, ruled 2026-09-25; PLAN_S3b.md H11).

Pins, on the stay_on loop with a fake daemon and a recorded heal pass:
  - no pass while nothing is pending, however long the unit idles;
  - the first pass IDLE_HEAL_S after the last send (boot, action or pass);
    heartbeats do not count as a send;
  - at most one pass per IDLE_HEAL_S while heals stay pending;
  - an action resets the idle clock;
  - a pass is logged as an action of kind "heal" and resets the heartbeat;
  - without a heal tx (heal_tx_open_fn None) there is never a pass.
The heal pass itself (plan -> chunks -> <HL>) is pinned on the wire by the
golden scenario stay_on_idle_heal.

Run (repo root):
  python3 -m unittest tests.test_s3b_idle_heal -v
"""

import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_supervisor as sup  # noqa: E402
from tests.test_s3b_stay_on import Loop  # noqa: E402


class IdleHeals(Loop):
    def setUp(self):
        super().setUp()
        self.passes = []

        def fake_pass(boot, daemon, settings, tx_open_fn, clock, sleep_fn):
            self.passes.append(round(clock()))
            boot.summary = {"stage": "done"}
            return boot.summary
        p = mock.patch.object(sup, "heal_pass", fake_pass)
        p.start()
        self.addCleanup(p.stop)

    def run_heals(self, **kw):
        kw.setdefault("heal_tx_open_fn", lambda path: None)
        return self.loop(self.action(duration=100), **kw)

    def test_nothing_pending_no_pass(self):
        self.run_heals(stop_at=3000)
        self.assertEqual(self.passes, [])

    def test_first_pass_after_ten_minutes_then_one_per_ten(self):
        self.state.pending_heals = [{"key": "aaaaaa", "n": [1], "id": 1, "wakes_left": 3}]
        self.run_heals(heartbeat_s=300, stop_at=1900)
        self.assertEqual(self.passes, [600, 1200, 1800])
        self.assertEqual([round(b) for b in self.beats], [300, 900, 1500])
        kinds = [r["kind"] for r in self.log_lines()]
        self.assertEqual(kinds, ["heal", "heal", "heal"])

    def test_an_action_resets_the_idle_clock(self):
        self.state.pending_heals = [{"key": "aaaaaa", "n": [1], "id": 1, "wakes_left": 3}]
        self.run_heals(script=[self.trg_at(500)], stop_at=1300)
        # trg at 500 runs until 600 -> the idle clock restarts at 600
        self.assertEqual(self.passes, [1200])

    def test_no_heal_tx_no_pass(self):
        self.state.pending_heals = [{"key": "aaaaaa", "n": [1], "id": 1, "wakes_left": 3}]
        self.run_heals(heal_tx_open_fn=None, stop_at=2000)
        self.assertEqual(self.passes, [])


if __name__ == "__main__":
    unittest.main()
