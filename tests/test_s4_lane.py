#!/usr/bin/env python3
# filename: test_s4_lane.py
# description: Sprint26 S4 b.8 — the lane guard for small cellular sends (acks, <CF>, heartbeats, <HL>) on the v9 path.
"""
Sprint26 S4 commit b.8 (DESIGN §6.1: "All cellular sends (acks, <CF>, <WS>,
<HL>) share one pacer with the lane guard, so nothing lands in the 30 s after a
5-minute boundary"; Nick 2026-09-28: in S4).

Pins:
  - lane_wait_s: 0 outside the guard; the rest of the post-boundary guard
    inside it; boundary + guard within the 2 s pre-margin; 0 with no lane
    config (legacy), the lane disabled, or no trusted Spotter read yet;
  - drain_acks holds a queued ack/<CF> inside the guard and sends it after;
  - flush_acks extends its deadline ONCE by the guard wait;
  - the stay_on loop postpones a due heartbeat until the guard has passed;
  - the <HL> lines wait out the guard when the budget allows.

Run (repo root):
  python3 -m unittest tests.test_s4_lane -v
"""

import datetime as dt
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_daemon  # noqa: E402
import rc_command_hooks as hooks  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_replies import FakeBm  # noqa: E402

LANE = {"enabled": True, "grid_seconds": 300.0, "post_boundary_guard_s": 30.0}
T0 = dt.datetime(2026, 9, 24, 15, 0, 0, tzinfo=dt.timezone.utc)     # a boundary


class Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def daemon_at(phase_s, lane=LANE):
    """A daemon whose last Spotter read was at the boundary, `phase_s` ago."""
    mono = Mono()
    d = command_daemon.CommandDaemon.__new__(command_daemon.CommandDaemon)
    d.lane_cfg = lane
    d._last_utc = T0
    d._last_utc_mono = mono.t
    mono.t += phase_s
    return d, mono


class LaneWait(unittest.TestCase):
    def wait(self, phase, lane=LANE):
        d, mono = daemon_at(phase, lane)
        with mock.patch.object(command_daemon.time, "monotonic", mono):
            return d.lane_wait_s()

    def test_phases(self):
        self.assertEqual(self.wait(0.0), 30.0)
        self.assertEqual(self.wait(12.0), 18.0)
        self.assertEqual(self.wait(30.0), 0.0)
        self.assertEqual(self.wait(200.0), 0.0)
        self.assertEqual(self.wait(297.0), 0.0)
        self.assertEqual(self.wait(299.0), 31.0)             # inside the 2 s pre-margin
        self.assertEqual(self.wait(301.0), 29.0)             # the next grid

    def test_off(self):
        self.assertEqual(self.wait(5.0, lane=None), 0.0)
        self.assertEqual(self.wait(5.0, lane=dict(LANE, enabled=False)), 0.0)
        d, mono = daemon_at(5.0)
        d._last_utc = None
        with mock.patch.object(command_daemon.time, "monotonic", mono):
            self.assertEqual(d.lane_wait_s(), 0.0)


class Sends(unittest.TestCase):
    def daemon(self, phase):
        d = command_daemon.CommandDaemon(FakeBm(), None)
        mono = Mono()
        d.lane_cfg = LANE
        d._last_utc = T0
        d._last_utc_mono = mono.t
        mono.t += phase
        return d, mono

    def test_drain_acks_holds_inside_the_guard(self):
        d, mono = self.daemon(10.0)
        d._acks.append('{"id":1000001,"ok":1}')
        with mock.patch.object(command_daemon.time, "monotonic", mono):
            self.assertEqual(quiet(d.drain_acks, clock=lambda: 0.0), 0)
            mono.t += 25.0
            self.assertEqual(quiet(d.drain_acks, clock=lambda: 0.0), 1)
        self.assertEqual(d.bm.tx, ['{"id":1000001,"ok":1}'])

    def test_flush_extends_once(self):
        d, mono = self.daemon(0.0)                          # 30 s of guard ahead
        d._acks.append('{"id":1000001,"ok":1}')
        clock = [0.0]

        def sleep(s):
            clock[0] += s
            mono.t += s
        with mock.patch.object(command_daemon.time, "monotonic", mono):
            quiet(hooks.flush_acks, d, {"command_events": []}, clock=lambda: clock[0],
                  sleep_fn=sleep, budget_s=15.0)
        self.assertEqual(len(d.bm.tx), 1)                   # sent after the guard
        self.assertGreaterEqual(clock[0], 29.9)
        self.assertLess(clock[0], 46.0)

    def test_hl_waits_out_the_guard(self):
        import rc_heal
        from rc_time_budget import CycleBudget
        d, mono = self.daemon(20.0)                         # 10 s of guard left
        d.state = type("St", (), {"pending_heals": []})()
        d.heal_events = []
        heals = quiet(rc_heal.WakeHeals, d, "/nonexistent", {})
        heals.outcomes = {"k3a9zq": {"a": "sent", "n": 1, "r": "ok", "id": 100_002}}
        slept = []

        def sleep(s):
            slept.append(s)
            mono.t += s
        sent = []
        with mock.patch.object(command_daemon.time, "monotonic", mono):
            quiet(heals.send_status_after_end, lambda b: sent.append(b),
                  CycleBudget(600.0, 1.0, clock=lambda: 0.0), delay_seconds=1.0,
                  sleep_fn=sleep)
        self.assertEqual(len(sent), 1)
        self.assertAlmostEqual(slept[0], 10.0)              # the guard, then the pacing
        self.assertEqual(slept[1], 1.0)


class Heartbeat(unittest.TestCase):
    def setUp(self):
        sup.STOP.update(requested=False, signal=None)

    def test_postponed_until_the_guard_passes(self):
        clock = [0.0]
        beats = []

        class D:
            v9_dispatch = None

            def process_pending(self):
                if clock[0] > 400:
                    sup.STOP["requested"] = True
                return []

            def drain_acks(self, clock=None):
                return 0

            def drain_console(self, sleep_fn=None):
                return 0

            def reader_health(self):
                return True, ""

            def lane_wait_s(self):
                return 5.0 if 300 <= clock[0] < 330 else 0.0

        boot = type("B", (), {"command_state": None, "save_local": False,
                              "note_guards": lambda self, s: []})()
        quiet(sup._loop, boot, D(), lambda b, s: {}, lambda: {}, 0, 300,
              lambda s: beats.append(clock[0]), lambda: clock[0],
              lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertEqual(len(beats), 1)
        self.assertGreaterEqual(beats[0], 330.0)
        sup.STOP.update(requested=False, signal=None)


if __name__ == "__main__":
    unittest.main()
