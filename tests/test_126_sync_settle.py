#!/usr/bin/env python3
# filename: test_126_sync_settle.py
# description: #126 — heal passes, stay_on trg actions and per_boot W10 trg actions wait SYNC_SETTLE_S after the last command (the Spotter's post-hub.sync cellular queue).
"""
nickraymond/bm_cam_legacy#126 (bmcam004 / SPOT-31593C, 2026-10-05): an rsd delivered at the
Spotter's hub.sync was healed at once; the heal burst landed in the sync and the 2-slot
cellular queue dropped chunks 83, 84 and the <HL>. G4 2026-10-03: a W10 trg clip lost 24/25.

Pins (fake clock, the stay_on / W10 harnesses of Sprint26):
  - Boot.settle_left_s: 0 before any command; SYNC_SETTLE_S right after one, counting down;
  - stay_on: a trg action and an O5 heal pass start SYNC_SETTLE_S after the last command,
    and a command meanwhile restarts the settle; nothing changes without commands;
  - per_boot W10: the trg needs settle + its minimum action time in the budget, and the
    action starts after the settle (commands keep being serviced while it waits).
The wire is pinned by the golden vectors (stay_on trg scenarios, v9_trigger_in_tail).

Run (repo root):
  python3 -m unittest tests.test_126_sync_settle -v
"""

import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_command_hooks as cmd_hooks  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_s3a_supervisor import quiet  # noqa: E402
from tests.test_s3b_stay_on import Loop  # noqa: E402
from tests.test_s3b_w10 import W10  # noqa: E402

S = sup.SYNC_SETTLE_S


class StayOn(Loop):
    def setUp(self):
        super().setUp()
        self.holder = {}
        self.passes = []

        def fake_pass(boot, daemon, settings, tx_open_fn, clock, sleep_fn):
            self.passes.append(round(clock()))
            boot.summary = {"stage": "done"}
            return boot.summary
        p = mock.patch.object(sup, "heal_pass", fake_pass)
        p.start()
        self.addCleanup(p.stop)

    def stay_boot(self, transmit=True):
        boot = super().stay_boot(transmit)
        self.holder["boot"] = boot
        return boot

    def command_at(self, t, then=None):
        """A command arriving at t (the v9 dispatcher stamps note_command), then `then`."""
        def fn():
            self.holder["boot"].note_command()
            if then:
                then()
        return (t, fn)

    def arm_trg(self, tid=1):
        return lambda: setattr(self.state, "pending_trigger", {"id": tid, "value": 2})

    def test_a_trg_waits_out_the_settle(self):
        self.loop(self.action(), script=[self.command_at(300, self.arm_trg())], stop_at=1000)
        self.assertEqual(len(self.actions), 1)
        self.assertAlmostEqual(self.actions[0]["t"], 300 + S, delta=0.5)

    def test_a_command_meanwhile_restarts_the_settle(self):
        self.loop(self.action(), script=[self.command_at(300, self.arm_trg()),
                                         self.command_at(330)], stop_at=1000)
        self.assertAlmostEqual(self.actions[0]["t"], 330 + S, delta=0.5)

    def test_no_command_noted_no_wait(self):
        # e.g. a trg armed from the boot drain: nothing to settle against
        self.loop(self.action(), script=[(300, self.arm_trg())], stop_at=1000)
        self.assertAlmostEqual(self.actions[0]["t"], 300, delta=0.5)

    def test_an_rsd_at_the_sync_is_healed_after_the_settle(self):
        # long idle (the O5 timer is due), then the rsd arrives: the pass waits S s
        def rsd():
            self.state.pending_heals = [{"key": "0e9fp2", "n": [81, 82, 83, 84], "id": 100131,
                                         "wakes_left": 3}]
        self.loop(self.action(), script=[self.command_at(3236, rsd)], stop_at=3500,
                  heal_tx_open_fn=lambda path: None)
        self.assertEqual(len(self.passes), 1)
        self.assertAlmostEqual(self.passes[0], 3236 + S, delta=1)

    def test_settle_left_counts_down(self):
        self.loop(self.action(), script=[self.command_at(100)], stop_at=200)
        boot = self.holder["boot"]
        self.assertEqual(boot.settle_left_s(), 0.0)          # 100 s after it, at the stop
        boot.last_command_at = boot._now() - 10
        self.assertAlmostEqual(boot.settle_left_s(), S - 10, delta=0.01)
        boot.last_command_at = None
        self.assertEqual(boot.settle_left_s(), 0.0)


class PerBootW10(W10):
    def setUp(self):
        super().setUp()
        sup.STOP.update(requested=False, halting=False, signal=None)   # not a stopping run

    def started_boot(self):
        boot = self.w10_boot()
        fn, _seen = self.action([0])
        quiet(fn, boot)                                      # budget 600 s anchored at t=0
        return boot

    def test_the_trg_needs_settle_plus_its_action_in_the_budget(self):
        boot = self.started_boot()
        self.state.pending_trigger = {"id": 1, "value": 2}
        edge = 600 - cmd_hooks.TAIL_SAFETY_S - sup.W10_MIN_STILL_ACTION_S
        self.clock.t = edge
        self.assertTrue(boot.w10_trigger_fits())             # no command: as before
        boot.note_command()                                  # the trg itself just arrived
        self.assertFalse(boot.w10_trigger_fits())            # no room for the settle
        self.clock.t = edge - S
        boot.note_command()
        self.assertTrue(boot.w10_trigger_fits())

    def test_sync_settle_waits_and_services_commands(self):
        boot = self.started_boot()
        ticks = []
        fake = mock.Mock()
        fake.process_pending.side_effect = lambda: ticks.append(self.clock.t) or []
        boot.note_command()
        t0 = self.clock.t
        waited = quiet(boot.sync_settle, fake, self.clock.sleep, "W10 trg id=1")
        self.assertAlmostEqual(waited, S, delta=sup.IDLE_TICK_S + 0.01)
        self.assertAlmostEqual(self.clock.t - t0, S, delta=sup.IDLE_TICK_S + 0.01)
        self.assertGreater(len(ticks), 100)                  # commands kept flowing
        self.assertEqual(quiet(boot.sync_settle, fake, self.clock.sleep, "again"), 0.0)


if __name__ == "__main__":
    unittest.main()
