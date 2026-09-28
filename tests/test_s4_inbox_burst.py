#!/usr/bin/env python3
# filename: test_s4_inbox_burst.py
# description: Sprint26 S4 b.7 — the durable inbox on the burst path: stash only, drained at decision points, survives a crash, rsd-only drains for <HL>.
"""
Sprint26 S4 commit b.7 (DESIGN §6.2 "Mid-burst commands"; REVIEW X2/R5;
consensus R22, R26; review B8).

Pins:
  - on the v9 path the burst pump is daemon.stash_pending: raw append only
    (no answer, no state change); the legacy/v8 pump is unchanged;
  - process_pending (a decision point) handles the inbox first, oldest first,
    removes each entry after its answer, and deletes the file when empty;
  - a crash mid-burst loses nothing: a NEW process handles the stash;
  - 70 distinct commands mid-burst: 64 kept, the eviction logged;
  - drain_rsd handles only heal requests (their events ride this wake's
    <HL>); other verbs wait for the decision point.

Run (repo root):
  python3 -m unittest tests.test_s4_inbox_burst -v
"""

import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_inbox as I  # noqa: E402
import command_state_v9 as S  # noqa: E402
import rc_command_hooks as hooks  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_dispatch import Rig  # noqa: E402


def with_inbox(r):
    r.daemon.v9_inbox = I.Inbox(I.path_beside(r.state_path), log=r.log.append)
    return r


class Burst(unittest.TestCase):
    def rig(self):
        r = Rig(self)
        r.log = []
        return with_inbox(r)

    def put(self, r, *cmds):
        for c in cmds:
            r.daemon._inbound.put(json.dumps(c).encode())

    def test_pump_only_stashes(self):
        r = self.rig()
        pump = hooks.make_pending_pump_fn(r.daemon, {"command_events": []})
        self.assertEqual(pump, r.daemon.stash_pending)
        before = os.path.exists(r.state_path) and open(r.state_path).read()
        self.put(r, {"id": 1_000_001, "c": "set", "kv": {"m": 150}}, {"id": 7, "c": "ping"})
        self.assertEqual(pump(), 2)
        self.assertEqual(r.daemon._acks, [])
        self.assertEqual(r.daemon._console, [])
        self.assertEqual(len(r.daemon.v9_inbox.entries()), 2)
        self.assertEqual(os.path.exists(r.state_path) and open(r.state_path).read(), before)
        events = quiet(r.daemon.process_pending)
        self.assertEqual([e["action"] for e in events], ["applied", "applied"])
        self.assertEqual(S.V9State(r.state_path).overlay["still.message_cap"], 150)
        self.assertFalse(os.path.exists(r.daemon.v9_inbox.path))

    def test_legacy_pump_unchanged(self):
        r = Rig(self)                                 # no inbox: the v8/legacy pump
        pump = hooks.make_pending_pump_fn(r.daemon, {"command_events": []})
        self.assertNotEqual(pump, r.daemon.stash_pending)

    def test_crash_mid_burst_loses_nothing(self):
        r = self.rig()
        self.put(r, {"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        r.daemon.stash_pending()
        r.new_process()                               # the process died mid-burst
        with_inbox(r)
        quiet(r.daemon.process_pending)
        self.assertEqual(S.V9State(r.state_path).overlay["still.message_cap"], 150)
        self.assertEqual(r.acks()[0]["ok"], 1)

    def test_handled_but_not_removed_is_a_duplicate(self):
        r = self.rig()
        self.put(r, {"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        r.daemon.stash_pending()
        entries = r.daemon.v9_inbox.entries()
        quiet(r.daemon.v9_dispatch.handle, entries[0])  # handled, then the crash
        r.new_process()
        with_inbox(r)
        events = quiet(r.daemon.process_pending)
        self.assertEqual([e["action"] for e in events], ["duplicate"])

    def test_bound_and_eviction(self):
        r = self.rig()
        self.put(r, *[{"id": 1_000_000 + i, "c": "ping"} for i in range(70)])
        r.daemon.stash_pending()
        self.assertEqual(len(r.daemon.v9_inbox.entries()), I.MAX_ENTRIES)
        self.assertEqual(sum("inbox full" in m for m in r.log), 6)

    def test_drain_rsd_only(self):
        r = self.rig()
        self.put(r, {"id": 1_000_001, "c": "set", "kv": {"m": 150}},
                 {"id": 100_002, "c": "rsd", "h": [["k3a9zq", "1"]]})
        r.daemon.stash_pending()
        events = quiet(r.daemon.drain_rsd)
        self.assertEqual([e["id"] for e in events], [100_002])
        self.assertEqual([e["key"] for e in r.daemon.heal_events], ["k3a9zq"])
        left = r.daemon.v9_inbox.entries()
        self.assertEqual(len(left), 1)
        self.assertIn(b'"set"', left[0])
        self.assertNotIn("still.message_cap", S.V9State(r.state_path).overlay)


if __name__ == "__main__":
    unittest.main()
