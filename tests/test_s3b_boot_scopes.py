#!/usr/bin/env python3
# filename: test_s3b_boot_scopes.py
# description: Sprint26 S3b.2 — Boot process scope vs action scope; sent heal events cleared.
"""
Sprint26 S3b.2 (PLAN_S3b.md §1, H11).

Pins:
  - Boot.start on the process's FIRST action: budget, then port session +
    daemon (S3a G1 order, per_boot unchanged);
  - a second Boot.start (stay_on's next action) reuses the SAME owner, daemon
    and port session and builds a NEW budget (one budget per action);
  - start_process() brings the process scope up with no action (stay_on's
    trigger-only boot), and a later start() only builds the budget;
  - WakeHeals.send_status_after_end clears only the daemon heal_events merged
    into an <HL> that went out; a budget-cut or failed send keeps the rest.

Run (repo root):
  python3 -m unittest tests.test_s3b_boot_scopes -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_heal  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from tests.test_s3a_supervisor import Base, quiet  # noqa: E402


class CountingDaemon:
    def __init__(self, calls):
        self.calls = calls
        self.fresh_time_reads = False

    def start(self):
        self.calls.append("daemon_start")


class BootScopes(Base):
    def fakes(self):
        f = super().fakes()
        f["daemon_factory"] = lambda s, c, st: CountingDaemon(self.rec.calls)
        return f

    def start(self, boot, summary):
        return boot.start(summary, log_fn=lambda *a: None, close_warn=print,
                          end_line=lambda: "end", **self.fakes())

    def test_second_action_reuses_owner_daemon_and_session(self):
        boot = self.boot()
        boot.run = "stay_on"                  # one budget per action (per_boot: one per boot)
        d1, b1 = quiet(self.start, boot, {})
        owner = boot.owner
        self.clock.t = 500.0
        d2, b2 = quiet(self.start, boot, {"n": 2})
        self.assertIs(d1, d2)
        self.assertIs(boot.owner, owner)
        self.assertIsNot(b1, b2)
        self.assertEqual(self.rec.calls.count("daemon_start"), 1)
        self.assertAlmostEqual(b2.elapsed_s(), 0.0)          # anchored at action 2's start
        self.assertAlmostEqual(b1.elapsed_s(), 500.0)
        self.assertEqual(boot.summary, {"n": 2})
        self.assertTrue(d1.fresh_time_reads)

    def test_per_boot_keeps_one_budget_for_a_w10_extra_action(self):
        boot = self.boot()
        _d1, b1 = quiet(self.start, boot, {})
        self.clock.t = 300.0
        _d2, b2 = quiet(self.start, boot, {"n": 2})
        self.assertIs(b1, b2)                 # G1: never re-anchored within a boot
        self.assertAlmostEqual(b2.elapsed_s(), 300.0)

    def test_start_process_then_actions(self):
        boot = self.boot()
        f = self.fakes()
        daemon = quiet(boot.start_process, clock=f["clock"], sleep_fn=f["sleep_fn"],
                       halt_fn=f["halt_fn"], bm_close_fn=f["bm_close_fn"],
                       daemon_factory=f["daemon_factory"])
        self.assertIsNone(boot.budget)
        d, b = quiet(self.start, boot, {})
        self.assertIs(d, daemon)
        self.assertIsNotNone(b)
        self.assertEqual(self.rec.calls, ["daemon_start"])

    def test_per_boot_order_is_budget_then_daemon(self):
        boot = self.boot()
        seen = []
        orig = boot.start_process

        def spy(**kw):
            seen.append(boot.budget is not None)
            return orig(**kw)
        boot.start_process = spy
        quiet(self.start, boot, {})
        self.assertEqual(seen, [True])      # G1: the budget exists before the daemon starts


class ActionLogMediaKey(BootScopes):
    def test_stills_key_from_the_boot(self):
        import rc_supervisor
        boot = self.boot()
        quiet(self.start, boot, {})
        boot.media_key = "0dumcd"                 # what still_action sets under the supervisor
        self.assertEqual(rc_supervisor.action_record(boot)["media_key"], "0dumcd")
        quiet(self.start, boot, {"media_key": "0vid00"})   # next action: reset, summary wins
        self.assertEqual(rc_supervisor.action_record(boot)["media_key"], "0vid00")


class FakeState:
    pending_heals = []

    def set_pending_heals(self, items):
        pass


class FakeDaemon:
    def __init__(self, events):
        self.state = FakeState()
        self.heal_events = events


def ev(key, a="requested", n=3, r="ok", cid=1):
    return {"key": key, "a": a, "n": n, "r": r, "id": cid}


class SentHealEventsCleared(unittest.TestCase):
    def status(self, events, budget_msgs=100, tx=None):
        daemon = FakeDaemon(events)
        heals = quiet(rc_heal.WakeHeals, daemon, "/nonexistent_sent", {})
        clk = [0.0]
        budget = CycleBudget(budget_msgs, 1.0, clock=lambda: clk[0])
        wire = []
        sent = quiet(heals.send_status_after_end, tx or wire.append, budget, wake_key=None,
                     delay_seconds=1.0, sleep_fn=lambda s: clk.__setitem__(0, clk[0] + s))
        return daemon, sent

    def test_sent_events_are_cleared(self):
        daemon, sent = self.status([ev("aaaaaa", cid=1), ev("bbbbbb", a="refused", r="range",
                                                          cid=2)])
        self.assertEqual(len(sent), 2)
        self.assertEqual(daemon.heal_events, [])

    def test_a_second_wake_does_not_resend(self):
        daemon, _ = self.status([ev("aaaaaa")])
        heals = quiet(rc_heal.WakeHeals, daemon, "/nonexistent_sent", {})
        self.assertEqual(heals.status_lines(), [])

    def test_budget_cut_keeps_the_unsent(self):
        # 1.5 s of budget at 1 s/msg: the first <HL> fits, then its paced sleep
        # leaves 0.5 s, so the second does not.
        daemon, sent = self.status([ev("aaaaaa", cid=1), ev("bbbbbb", cid=2)], budget_msgs=1.5)
        self.assertEqual(len(sent), 1)
        self.assertEqual([e["key"] for e in daemon.heal_events], ["bbbbbb"])

    def test_tx_failure_keeps_everything_not_sent(self):
        def boom(line):
            raise OSError("uart")
        daemon, sent = self.status([ev("aaaaaa")], tx=boom)
        self.assertEqual(sent, [])
        self.assertEqual(len(daemon.heal_events), 1)


if __name__ == "__main__":
    unittest.main()
