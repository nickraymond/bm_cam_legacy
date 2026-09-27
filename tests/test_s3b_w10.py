#!/usr/bin/env python3
# filename: test_s3b_w10.py
# description: Sprint26 S3b.7 — W10 (O3): a trg heard in the per_boot listen tail fires this boot if it fits.
"""
Sprint26 S3b.7 (DESIGN §10 O3, ruled 2026-09-25; PLAN_S3b.md H12; W10).

Pins:
  - Boot.w10_trigger_fits: only per_boot, only with a trg armed, only when
    remaining - TAIL_SAFETY_S >= min_action_s (stills 60 s; video clip +
    lead-in + 60 s, set by main);
  - run_per_boot fires a fitting trg as another action on the SAME budget,
    then halts once; each action gets an action-log line;
  - a trg that does not fit stays armed, one action, one halt;
  - CommandDaemon.listen_window(until=...) ends the window early.
The wire is pinned by the golden scenario still_trigger_in_tail
(vectors/ = legacy, trg stays armed; vectors_supervisor/ = W10).

Run (repo root):
  python3 -m unittest tests.test_s3b_w10 -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_command_hooks as cmd_hooks  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_command_daemon import DaemonTestCase  # noqa: E402
from tests.test_s3a_supervisor import SETTINGS, Base, quiet  # noqa: E402
from tests.test_s3b_boot_scopes import CountingDaemon  # noqa: E402


class State:
    def __init__(self):
        self.pending_trigger = None


class W10(Base):
    def setUp(self):
        super().setUp()
        self.state = State()

    def w10_boot(self):
        return sup.Boot(dict(SETTINGS), media="still", bm_commands_cfg={"enabled": True},
                        command_state=self.state, transmit=True, bench_commands=False,
                        action_log=self.log)

    def action(self, durations, arm_after=()):
        """Each call: start (budget + daemon), spend durations[i] s, then arm a
        trg if i in arm_after (heard in this action's tail)."""
        seen = []

        def fn(boot):
            i = len(seen)
            _d, budget = boot.start({"command_events": []}, clock=self.clock,
                                    sleep_fn=self.clock.sleep, halt_fn=self.rec.halt,
                                    bm_close_fn=self.rec.close,
                                    daemon_factory=lambda s, c, st: CountingDaemon(
                                        self.rec.calls),
                                    log_fn=lambda *a: None, close_warn=print, end_line=None)
            seen.append(budget)
            self.state.pending_trigger = None          # the boot drain consumed it
            self.clock.t += durations[i]
            if i in arm_after:
                self.state.pending_trigger = {"id": 900 + i, "value": 2}
            return boot.summary
        return fn, seen

    def test_fits_rule(self):
        boot = self.w10_boot()
        quiet(self.action([0])[0], boot)                # budget 600 s anchored at t=0
        self.assertFalse(boot.w10_trigger_fits())      # nothing armed
        self.state.pending_trigger = {"id": 1, "value": 2}
        self.clock.t = 600 - cmd_hooks.TAIL_SAFETY_S - sup.W10_MIN_STILL_ACTION_S
        self.assertTrue(boot.w10_trigger_fits())
        self.clock.t += 0.5
        self.assertFalse(boot.w10_trigger_fits())
        boot.min_action_s = 5.0 + 2.0 + sup.W10_VIDEO_MARGIN_S    # a 5 s clip, 2 s lead-in
        self.clock.t = 600 - cmd_hooks.TAIL_SAFETY_S - boot.min_action_s
        self.assertTrue(boot.w10_trigger_fits())
        self.clock.t += 0.5
        self.assertFalse(boot.w10_trigger_fits())
        self.clock.t = 0.0
        boot.run = "stay_on"
        self.assertFalse(boot.w10_trigger_fits())

    def test_fitting_trg_fires_on_the_same_budget(self):
        fn, seen = self.action([200, 150], arm_after=(0,))
        boot = self.w10_boot()
        quiet(sup.run_per_boot, boot, fn)
        self.assertEqual(len(seen), 2)
        self.assertIs(seen[0], seen[1])
        self.assertEqual(self.rec.calls, ["daemon_start", "close", "halt"])
        self.assertEqual(len(self.log_lines()), 2)
        self.assertIsNone(self.state.pending_trigger)

    def test_trg_that_does_not_fit_stays_armed(self):
        fn, seen = self.action([560], arm_after=(0,))
        boot = self.w10_boot()
        quiet(sup.run_per_boot, boot, fn)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.state.pending_trigger["id"], 900)
        self.assertEqual(self.rec.calls, ["daemon_start", "close", "halt"])


class ListenUntil(DaemonTestCase):
    def test_until_ends_the_window(self):
        t = [0.0]
        calls = []

        def until():
            calls.append(t[0])
            return t[0] >= 1.0
        self.daemon.start()
        quiet(self.daemon.listen_window, 150, clock=lambda: t[0],
              sleep_fn=lambda s: t.__setitem__(0, t[0] + s), until=until)
        self.assertAlmostEqual(t[0], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
