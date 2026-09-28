#!/usr/bin/env python3
# filename: test_s4_hold.py
# description: Sprint26 S4 b.6c — hld and keep-alive: granted minutes (cap + budget clamp + bus_always_on), release, never persisted, stay awake, trg while held.
"""
Sprint26 S4 commit b.6c (DESIGN §4 keep-alive / explicit hold; REVIEW K10).

Pins:
  - hld v grants min(v, hold_max_min); per_boot also clamps to the budget
    minus the halt margin unless power.bus_always_on; v:0 releases; the ack
    carries "v":<granted>; nothing is written to the state file;
  - stay_on grants the capped minutes (a stay_on unit never halts);
  - keep-alive: after a command, awake_until = last command + keepalive_s, at
    most keepalive_max_s past the normal end, clamped to the budget;
  - stay_awake keeps servicing commands and returns "trigger" when a trg
    arrives that fits the budget (the next W10 action).

Run (repo root):
  python3 -m unittest tests.test_s4_hold -v
"""

import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_state_v9 as S  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_dispatch import Rig  # noqa: E402

LIMITS = {"commands.keepalive_s": 300, "commands.keepalive_max_s": 1800,
          "commands.hold_max_min": 120, "power.bus_always_on": False}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make_boot(r, budget_s=1800.0, run="per_boot", limits=None):
    clock = Clock()
    b = sup.Boot({"x": 1}, media="still", bm_commands_cfg={}, command_state=r.state,
                 transmit=True, bench_commands=False)
    b.run = run
    b.v9_limits = dict(LIMITS, **(limits or {}))
    b._guard_clock = clock
    b._sleep_fn = clock.sleep
    b.budget = CycleBudget(budget_s, 1.0, clock=clock)
    r.daemon.v9_dispatch.boot = b
    return b, clock


class Hold(unittest.TestCase):
    def test_granted_capped_and_clamped(self):
        r = Rig(self)
        b, clock = make_boot(r, budget_s=1800.0)
        r.send({"id": 1_000_001, "c": "hld", "v": 10})
        self.assertEqual(r.acks()[0], {"id": 1_000_001, "ok": 1, "h": r.hash(), "v": 10})
        self.assertEqual(b.hold_until, 600.0)
        r.send({"id": 1_000_002, "c": "hld", "v": 60})          # budget: (1800-20)/60 = 29
        ack = r.acks()[0]
        self.assertEqual(ack["v"], 29)
        self.assertIn("clamped", r.lines()[-1])
        r.send({"id": 1_000_003, "c": "hld", "v": 0})
        self.assertEqual((r.acks()[0]["v"], b.hold_until), (0, None))
        self.assertIn("hold released", r.lines()[-1])
        self.assertNotIn("hold", json.dumps(S.V9State(r.state_path).extra))

    def test_bus_always_on_lifts_the_clamp_but_not_the_cap(self):
        r = Rig(self)
        b, _clock = make_boot(r, budget_s=600.0, limits={"power.bus_always_on": True})
        r.send({"id": 1_000_001, "c": "hld", "v": 200})
        self.assertEqual(r.acks()[0]["v"], 120)

    def test_stay_on_grants_the_cap(self):
        r = Rig(self)
        make_boot(r, budget_s=60.0, run="stay_on")
        r.send({"id": 1_000_001, "c": "hld", "v": 30})
        self.assertEqual(r.acks()[0]["v"], 30)


class KeepAlive(unittest.TestCase):
    def test_awake_until(self):
        r = Rig(self)
        b, clock = make_boot(r, budget_s=3600.0)
        self.assertIsNone(b.awake_until())                     # no command, no hold
        clock.t = 100.0
        b.note_command()
        self.assertEqual(b.awake_until(), 400.0)
        clock.t = 1900.0                                        # past keepalive_max_s
        b.note_command()
        self.assertEqual(b.awake_until(), 1800.0)               # normal_end 0 + 1800

    def test_clamped_to_the_budget(self):
        r = Rig(self)
        b, clock = make_boot(r, budget_s=200.0)
        clock.t = 100.0
        b.note_command()
        self.assertEqual(b.awake_until(), 180.0)               # 200 - TAIL_SAFETY 20

    def test_stay_awake_services_commands_then_ends(self):
        r = Rig(self)
        b, clock = make_boot(r, budget_s=3600.0)
        b.note_command()
        r.daemon._inbound.put(json.dumps({"id": 7, "c": "ping"}).encode())
        out = quiet(b.stay_awake, r.daemon, clock.sleep)
        self.assertEqual(out, "done")
        self.assertGreaterEqual(clock.t, 300.0)
        self.assertLess(clock.t, 301.0)
        self.assertTrue(any("OK id=7 ping" in line for line in r.bm_console()))

    def test_a_trg_while_held_returns_trigger(self):
        r = Rig(self)
        b, clock = make_boot(r, budget_s=3600.0)
        b.request_hold(10)
        r.daemon._inbound.put(json.dumps({"id": 1_000_001, "c": "trg", "v": 2}).encode())
        self.assertEqual(quiet(b.stay_awake, r.daemon, clock.sleep), "trigger")
        self.assertLess(clock.t, 600.0)

    def test_a_trg_while_held_on_a_held_bus_fires_past_the_budget(self):
        # S5 F7 (bench 2026-09-28): bmcam003 on a held bus, hld 120 granted 30 min
        # after boot; a trg then stayed armed because the 480 s boot budget was spent.
        r = Rig(self)
        b, clock = make_boot(r, budget_s=480.0, limits={"power.bus_always_on": True})
        clock.t = 1800.0
        self.assertEqual(b.request_hold(60), 60)
        r.daemon._inbound.put(json.dumps({"id": 1_000_001, "c": "trg", "v": 2}).encode())
        self.assertEqual(quiet(b.stay_awake, r.daemon, clock.sleep), "trigger")

    def test_a_spent_budget_on_a_scheduled_bus_keeps_the_trg_armed(self):
        r = Rig(self)
        b, clock = make_boot(r, budget_s=480.0)
        clock.t = 1800.0
        r.send({"id": 1_000_001, "c": "trg", "v": 2})
        self.assertIsNotNone(b._pending_trigger())
        self.assertFalse(b.w10_trigger_fits())

    def test_a_w10_action_on_a_held_bus_gets_a_fresh_budget(self):
        r = Rig(self)
        b, clock = make_boot(r, budget_s=480.0, limits={"power.bus_always_on": True})
        b.settings = {"budget_seconds": 480, "pacing_delay_seconds": 1.0}
        b.owner = type("Owner", (), {"daemon": r.daemon})()
        clock.t = 1800.0
        old = b.budget
        self.assertEqual(old.remaining_s(), 0.0)
        b.reanchor_budget = True           # what _w10_actions sets on a held bus
        _daemon, budget = b.start({}, clock=clock, sleep_fn=clock.sleep, halt_fn=None,
                                  bm_close_fn=None, daemon_factory=None, log_fn=print,
                                  close_warn=print, end_line=None)
        self.assertIsNot(budget, old)
        self.assertEqual(budget.remaining_s(), 480.0)
        self.assertFalse(b.reanchor_budget)
        b.reanchor_budget = False          # scheduled bus: the one boot budget stays
        _daemon, again = b.start({}, clock=clock, sleep_fn=clock.sleep, halt_fn=None,
                                 bm_close_fn=None, daemon_factory=None, log_fn=print,
                                 close_warn=print, end_line=None)
        self.assertIs(again, budget)

    def test_off_the_v9_path_or_stay_on_does_nothing(self):
        r = Rig(self)
        b, clock = make_boot(r, run="stay_on")
        b.note_command()
        self.assertEqual(quiet(b.stay_awake, r.daemon, clock.sleep), "done")
        self.assertEqual(clock.t, 0.0)


if __name__ == "__main__":
    unittest.main()
