#!/usr/bin/env python3
# filename: test_ack_resend.py
# description: R1 command resilience — the last 2 boots' cellular answers re-sent as d:1 acks at wake start.
"""
R1 (Nick 2026-10-03, EM chat): a unit acks each command during the Spotter's
mailbox sync, when the cell-only queue is often full ("Unable to submit message
to cell-only queue"), and the ack is silently dropped; G4 lost 9 of 32 acks
(runs/g4_outdoor12h_20261002/RESULTS.md G4.12). Now each wake re-sends the
cached answers of the last 2 boots, before capture, in the EXISTING duplicate
ack form (d:1) the backend already reads.

Pins:
  lost ack re-sent        an answer from the previous boot goes out again as d:1
  2 boots only            boot N's answer is re-sent in N+1 and N+2, never in N+3
  normal acks unchanged   a command answered this boot keeps its normal ack (no
                          d:1) on the normal queue; the re-send never touches it
  bounded                 at most RESEND_MAX (newest), cellular ranges only
  G9 once per process     a mote replay later in the wake sends no 2nd cellular copy
  paced + budgeted        the supervisor paces the re-sends at the ack floor and
                          trims them to what the wake budget can pace

Run (repo root):
  python3 -m pytest tests/test_ack_resend.py -q
"""

import contextlib
import io
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_v9 as V  # noqa: E402
import rc_supervisor  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_dispatch import Rig  # noqa: E402


def boot(rig):
    """A new process on the same state file, counted like a --transmit boot."""
    rig.new_process()
    quiet(rig.state.count_boot)


def resend(rig):
    """Dispatcher picks + daemon send; -> the JSON acks that went on the wire."""
    picks = rig.daemon.v9_dispatch.recent_answers()
    rig.daemon.bm.tx.clear()
    quiet(rig.daemon.resend_acks, [ack for _cid, ack in picks], clock=rig.clock,
          sleep_fn=lambda s: setattr(rig.clock, "t", rig.clock.t + s))
    return [json.loads(a) for a in rig.daemon.bm.tx]


class TestAckResend(unittest.TestCase):
    def test_lost_ack_is_resent_next_boot_as_d1(self):
        r = Rig(self)
        boot(r)
        r.send({"id": 1_000_001, "c": "ping"})
        self.assertNotIn("d", r.acks()[0])           # the original: queued, then "lost"
        boot(r)
        wire = resend(r)
        self.assertEqual(wire, [{"id": 1_000_001, "ok": 1, "h": r.hash(), "d": 1}])

    def test_resent_in_the_next_two_boots_only(self):
        r = Rig(self)
        boot(r)
        r.send({"id": 1_000_001, "c": "ping"})
        seen = []
        for _ in range(3):
            boot(r)
            seen.append([a["id"] for a in resend(r)])
        self.assertEqual(seen, [[1_000_001], [1_000_001], []])

    def test_normal_acks_are_unchanged(self):
        r = Rig(self)
        boot(r)
        r.send({"id": 1_000_001, "c": "ping"})
        r.acks()
        boot(r)
        r.send({"id": 1_000_002, "c": "ping"})      # answered THIS boot
        normal = list(r.daemon._acks)
        wire = resend(r)
        self.assertEqual([a["id"] for a in wire], [1_000_001])   # not this boot's
        self.assertEqual(r.daemon._acks, normal)                  # queue untouched
        self.assertEqual(json.loads(normal[0]), {"id": 1_000_002, "ok": 1, "h": r.hash()})

    def test_rejection_is_resent_with_its_error(self):
        r = Rig(self)
        boot(r)
        r.send({"id": 1_000_001, "c": "set", "kv": {"schedule.timezone": "Mars/Olympus"}})
        original = r.acks()[0]
        boot(r)
        wire = resend(r)
        self.assertEqual(wire, [dict(original, d=1)])

    def test_bounded_newest_first_and_cellular_ranges_only(self):
        r = Rig(self)
        boot(r)
        r.send({"id": 7, "c": "ping"})              # console range: never a cellular ack
        for i in range(1, 10):
            r.send({"id": 1_000_000 + i, "c": "ping"})
        boot(r)
        ids = [a["id"] for a in resend(r)]
        self.assertEqual(ids, [1_000_000 + i for i in range(10 - V.RESEND_MAX, 10)])

    def test_replay_after_the_resend_sends_no_second_cellular_copy(self):
        r = Rig(self)
        boot(r)
        r.send({"id": 1_000_001, "c": "ping"})
        r.acks()
        boot(r)
        resend(r)
        r.send({"id": 1_000_001, "c": "ping"})      # the mote's ~60 s replay
        self.assertEqual(r.acks(), [])               # console answer only (G9)
        self.assertTrue(any("duplicate" in line for line in r.lines()))

    def test_a_replay_drained_first_is_not_resent_twice(self):
        r = Rig(self)
        boot(r)
        r.send({"id": 1_000_001, "c": "ping"})
        r.acks()
        boot(r)
        r.send({"id": 1_000_001, "c": "ping"})      # boot drain: replay -> d:1 queued
        self.assertEqual(r.acks()[0].get("d"), 1)
        self.assertEqual(resend(r), [])


class TestSupervisorResend(unittest.TestCase):
    def make_boot(self, rig, budget_s, transmit=True):
        b = rc_supervisor.Boot({}, media="still", bm_commands_cfg={}, command_state=rig.state,
                               transmit=transmit, bench_commands=False)
        b.budget = CycleBudget(budget_s, 1.0, clock=rig.clock)
        b._guard_clock = rig.clock
        return b

    def run_resend(self, rig, b):
        summary, sleeps = {}, []

        def sleep(s):
            sleeps.append(s)
            rig.clock.t += s
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            b._resend_recent_acks(rig.daemon, summary, sleep)
        return summary, sleeps, out.getvalue()

    def rig_with_answers(self, n):
        r = Rig(self)
        boot(r)
        for i in range(1, n + 1):
            r.send({"id": 1_000_000 + i, "c": "ping"})
        r.acks()
        boot(r)
        r.daemon.bm.tx.clear()
        return r

    def test_paced_once_per_process_and_logged(self):
        r = self.rig_with_answers(3)
        b = self.make_boot(r, 480)
        summary, sleeps, out = self.run_resend(r, b)
        self.assertEqual(summary["ack_resend"], {"ids": [1_000_001, 1_000_002, 1_000_003],
                                                 "sent": 3})
        self.assertEqual(len(r.daemon.bm.tx), 3)
        self.assertEqual(sleeps, [r.daemon.ack_interval_s] * 2)   # the ack floor between
        self.assertIn("[CMD] ack re-send: 3 answer(s)", out)
        self.assertIn("[CMD] ack re-sent: ", out)
        self.assertEqual(r.daemon.stats["acks_resent"], 3)
        self.run_resend(r, b)                                     # a W10 action's drain
        self.assertEqual(len(r.daemon.bm.tx), 3)

    def test_trimmed_to_the_wake_budget(self):
        r = self.rig_with_answers(3)
        b = self.make_boot(r, 2.0)                                # paces 2 messages
        summary, _, out = self.run_resend(r, b)
        self.assertEqual(summary["ack_resend"]["ids"], [1_000_002, 1_000_003])
        self.assertIn("[CMD][WARN] ack re-send: 3 answer(s), the budget paces 2", out)

    def test_no_resend_without_transmit(self):
        r = self.rig_with_answers(2)
        summary, _, _ = self.run_resend(r, self.make_boot(r, 480, transmit=False))
        self.assertEqual((summary, r.daemon.bm.tx), ({}, []))


if __name__ == "__main__":
    unittest.main()
