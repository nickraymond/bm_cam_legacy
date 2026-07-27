#!/usr/bin/env python3
# filename: test_gui_lifecycle.py
# description: Sprint10 §7 — unit tests for the GUI command lifecycle store.
"""
Sprint10 — tests for tools/bm_command_gui/lifecycle.py.

Pins the D10 contract: 202 -> awaiting_node, non-202 -> send_failed,
ack -> acked/mismatch with loud detail, in-flight query for the
re-send warning, replay-on-restart, and fresh-id allocation that never
reuses a logged id.

Run (repo root):
  python3 -m unittest tests.test_gui_lifecycle -v
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "bm_command_gui"))

import lifecycle as lc  # noqa: E402

GOOD_ST = {"roi": 2, "foc": 0, "awb": 0, "exp": 0, "win": 0}


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, "gui_commands.jsonl")
        self.store = lc.CommandLifecycle(self.path)

    def _send(self, cmd_id=1001, c="roi", v=2, status=202):
        self.store.record_sent(
            cmd_id, "SPOT-TEST", "53171fa3d81a8e6f", c, v,
            f'bm pub bmcam/cmd {{"id":{cmd_id},"c":"{c}","v":{v}}} 1 1',
            status, {"status": "success"})


class TestTransitions(LifecycleTestCase):
    def test_202_goes_awaiting_node(self):
        self._send()
        self.assertEqual(self.store.get(1001)["state"], lc.AWAITING_NODE)

    def test_non_202_goes_send_failed(self):
        self._send(status=400)
        self.assertEqual(self.store.get(1001)["state"], lc.SEND_FAILED)
        self.assertEqual(self.store.in_flight(), [])

    def test_matching_ack_goes_acked(self):
        self._send()
        self.store.record_ack(1001, {"id": 1001, "ok": 1, "st": GOOD_ST})
        cmd = self.store.get(1001)
        self.assertEqual(cmd["state"], lc.ACKED)
        self.assertNotIn("mismatch_detail", cmd)

    def test_wrong_value_ack_goes_mismatch_loudly(self):
        self._send()  # sent roi=2
        st = dict(GOOD_ST, roi=0)
        self.store.record_ack(1001, {"id": 1001, "ok": 1, "st": st})
        cmd = self.store.get(1001)
        self.assertEqual(cmd["state"], lc.MISMATCH)
        self.assertIn("roi", cmd["mismatch_detail"])

    def test_device_reject_goes_mismatch(self):
        self._send()
        self.store.record_ack(1001, {"id": 1001, "ok": 0, "e": "val",
                                     "st": GOOD_ST})
        self.assertEqual(self.store.get(1001)["state"], lc.MISMATCH)

    def test_ping_ack_needs_no_value_match(self):
        self._send(cmd_id=1002, c="ping", v=None)
        self.store.record_ack(1002, {"id": 1002, "ok": 1,
                                     "st": dict(GOOD_ST, roi=4)})
        self.assertEqual(self.store.get(1002)["state"], lc.ACKED)

    def test_unknown_ack_is_mismatch(self):
        self.store.record_ack(9999, {"id": 9999, "ok": 1, "st": GOOD_ST})
        self.assertEqual(self.store.get(9999)["state"], lc.MISMATCH)


class TestInFlight(LifecycleTestCase):
    def test_in_flight_until_acked(self):
        self._send()
        self.assertEqual(len(self.store.in_flight()), 1)
        self.assertEqual(len(self.store.in_flight("SPOT-TEST")), 1)
        self.assertEqual(len(self.store.in_flight("SPOT-OTHER")), 0)
        self.store.record_ack(1001, {"id": 1001, "ok": 1, "st": GOOD_ST})
        self.assertEqual(self.store.in_flight(), [])


class TestPersistence(LifecycleTestCase):
    def test_replay_after_restart(self):
        self._send()
        self.store.record_ack(1001, {"id": 1001, "ok": 1, "st": GOOD_ST})
        again = lc.CommandLifecycle(self.path)
        self.assertEqual(again.get(1001)["state"], lc.ACKED)
        self.assertEqual(len(again.get(1001)["history"]), 2)

    def test_torn_tail_line_tolerated(self):
        self._send()
        with open(self.path, "a") as f:
            f.write('{"torn')
        again = lc.CommandLifecycle(self.path)
        self.assertEqual(again.get(1001)["state"], lc.AWAITING_NODE)

    def test_next_id_never_reuses_logged_ids(self):
        self.assertEqual(self.store.next_command_id(), 1000)
        self._send(cmd_id=1400)
        self.assertEqual(self.store.next_command_id(), 1401)
        again = lc.CommandLifecycle(self.path)
        self.assertEqual(again.next_command_id(), 1401)


if __name__ == "__main__":
    unittest.main()
