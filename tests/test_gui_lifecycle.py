#!/usr/bin/env python3
# filename: test_gui_lifecycle.py
# description: Sprint10 §7 / Sprint26 S4 c.3 — unit tests for the GUI command lifecycle store (commands v9).
"""
Tests for tools/bm_command_gui/lifecycle.py.

Pins the D10 contract on the v9 slim ack (DESIGN_supervisor.md §6.2,
PLAN_S4.md G8/G9): 202 -> awaiting_node, non-202 -> send_failed,
ok:1 -> acked with the config hash `h` recorded, ok:0 -> rejected with
e/k, d:1 (the unit's repeat of an earlier answer) still acked, s:1
staged noted, wrong node / unknown id -> mismatch; in-flight query for
the re-send warning, replay-on-restart (pre-v9 events too), and
fresh-id allocation in the remote range (floor 1 000 000) that never
reuses a logged id.

Run (repo root):
  python3 -m unittest tests.test_gui_lifecycle -v
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "bm_command_gui"))

import lifecycle as lc  # noqa: E402

CID = 1_000_101
H = "a41c09e2"


def ack(cid=CID, ok=1, **extra):
    return dict({"id": cid, "ok": ok}, **({"h": H} if ok == 1 else {}), **extra)


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, "gui_commands.jsonl")
        self.store = lc.CommandLifecycle(self.path)

    def _send(self, cmd_id=CID, c="set", fields=None, status=202):
        fields = {"kv": {"d": 8}} if fields is None else fields
        body = json.dumps(dict({"id": cmd_id, "c": c}, **fields),
                          separators=(",", ":"))
        self.store.record_sent(
            cmd_id, "SPOT-TEST", "53171fa3d81a8e6f", c, fields,
            f"bm pub bmcam/cmd {body} 1 1", status, {"status": "success"})


class TestTransitions(LifecycleTestCase):
    def test_202_goes_awaiting_node(self):
        self._send()
        cmd = self.store.get(CID)
        self.assertEqual(cmd["state"], lc.AWAITING_NODE)
        self.assertEqual(cmd["fields"], {"kv": {"d": 8}})

    def test_non_202_goes_send_failed(self):
        self._send(status=400)
        self.assertEqual(self.store.get(CID)["state"], lc.SEND_FAILED)
        self.assertEqual(self.store.in_flight(), [])

    def test_ok_ack_goes_acked_and_records_hash(self):
        self._send()
        self.store.record_ack(CID, ack())
        cmd = self.store.get(CID)
        self.assertEqual(cmd["state"], lc.ACKED)
        self.assertEqual(cmd["h"], H)
        self.assertNotIn("mismatch_detail", cmd)

    def test_no_st_check_in_v9(self):
        # A v9 ack carries no `st`; nothing about the value is compared.
        self._send(fields={"kv": {"still.crop": [0, 0, 4608, 2592]}})
        self.store.record_ack(CID, ack())
        self.assertEqual(self.store.get(CID)["state"], lc.ACKED)

    def test_rejection_goes_rejected_with_e_and_k(self):
        self._send()
        self.store.record_ack(CID, ack(ok=0, e="val", k="video.send.duration_s"))
        cmd = self.store.get(CID)
        self.assertEqual(cmd["state"], lc.REJECTED)
        self.assertEqual((cmd["e"], cmd["k"]), ("val", "video.send.duration_s"))
        self.assertIn("e=val", cmd["mismatch_detail"])
        self.assertIn("k=video.send.duration_s", cmd["mismatch_detail"])
        self.assertEqual(self.store.in_flight(), [])

    def test_duplicate_answer_still_acked(self):
        self._send()
        self.store.record_ack(CID, ack(d=1))
        cmd = self.store.get(CID)
        self.assertEqual(cmd["state"], lc.ACKED)
        self.assertTrue(cmd["duplicate"])

    def test_staged_ack_is_acked_with_cfm_hint(self):
        self._send(fields={"kv": {"power.halt.enabled": True}})
        self.store.record_ack(CID, ack(s=1))
        cmd = self.store.get(CID)
        self.assertEqual(cmd["state"], lc.ACKED)
        self.assertTrue(cmd["staged"])
        self.assertIn("cfm", cmd["mismatch_detail"])

    def test_hold_grant_recorded(self):
        self._send(c="hld", fields={"v": 30})
        self.store.record_ack(CID, ack(v=30))
        self.assertEqual(self.store.get(CID)["granted_min"], 30)

    def test_malformed_ok_is_mismatch(self):
        self._send()
        self.store.record_ack(CID, {"id": CID, "ok": True})   # bool is not 1
        self.assertEqual(self.store.get(CID)["state"], lc.MISMATCH)

    def test_ping_ack(self):
        self._send(cmd_id=CID + 1, c="ping", fields={})
        self.store.record_ack(CID + 1, ack(cid=CID + 1))
        self.assertEqual(self.store.get(CID + 1)["state"], lc.ACKED)

    def test_unknown_ack_is_mismatch(self):
        self.store.record_ack(9_999_999, ack(cid=9_999_999))
        self.assertEqual(self.store.get(9_999_999)["state"], lc.MISMATCH)

    def test_wrong_node_id_is_loud_mismatch(self):
        self._send()  # expected node 53171fa3d81a8e6f
        self.store.record_ack(CID, ack(), node_id="c3c564b91856226c")
        cmd = self.store.get(CID)
        self.assertEqual(cmd["state"], lc.MISMATCH)
        self.assertIn("WRONG DEVICE", cmd["mismatch_detail"])

    def test_matching_node_id_acked(self):
        self._send()
        self.store.record_ack(CID, ack(), node_id="53171fa3d81a8e6f")
        self.assertEqual(self.store.get(CID)["state"], lc.ACKED)
        self.assertEqual(self.store.get(CID)["ack_node_id"], "53171fa3d81a8e6f")

    def test_missing_node_id_does_not_block_ack(self):
        self._send()
        self.store.record_ack(CID, ack(), node_id=None)
        self.assertEqual(self.store.get(CID)["state"], lc.ACKED)


class TestVerifyAck(unittest.TestCase):
    CMD = {"cmd_id": CID, "node_id": "", "c": "set"}

    def test_states(self):
        self.assertEqual(lc.verify_ack(self.CMD, ack()), (lc.ACKED, None))
        self.assertEqual(lc.verify_ack(self.CMD, ack(ok=0, e="old"))[0], lc.REJECTED)
        self.assertEqual(lc.verify_ack(None, ack())[0], lc.MISMATCH)
        self.assertEqual(lc.verify_ack(self.CMD, ack(d=1)), (lc.ACKED, None))


class TestInFlight(LifecycleTestCase):
    def test_in_flight_until_acked(self):
        self._send()
        self.assertEqual(len(self.store.in_flight()), 1)
        self.assertEqual(len(self.store.in_flight("SPOT-TEST")), 1)
        self.assertEqual(len(self.store.in_flight("SPOT-OTHER")), 0)
        self.store.record_ack(CID, ack())
        self.assertEqual(self.store.in_flight(), [])


class TestPersistence(LifecycleTestCase):
    def test_replay_after_restart(self):
        self._send()
        self.store.record_ack(CID, ack())
        again = lc.CommandLifecycle(self.path)
        self.assertEqual(again.get(CID)["state"], lc.ACKED)
        self.assertEqual(again.get(CID)["h"], H)
        self.assertEqual(len(again.get(CID)["history"]), 2)

    def test_pre_v9_events_still_replay(self):
        with open(self.path, "w") as f:
            f.write(json.dumps({"utc": "T", "cmd_id": 1001, "state": "acked",
                                "c": "roi", "v": 2}) + "\n")
        again = lc.CommandLifecycle(self.path)
        self.assertEqual(again.get(1001)["v"], 2)

    def test_torn_tail_line_tolerated(self):
        self._send()
        with open(self.path, "a") as f:
            f.write('{"torn')
        again = lc.CommandLifecycle(self.path)
        self.assertEqual(again.get(CID)["state"], lc.AWAITING_NODE)


class TestNextId(LifecycleTestCase):
    def test_floor_is_the_remote_range(self):
        self.assertEqual(self.store.next_command_id(), 1_000_000)
        self.assertEqual(lc.REMOTE_ID_FLOOR, 1_000_000)

    def test_never_reuses_logged_ids(self):
        self._send(cmd_id=1_000_400)
        self.assertEqual(self.store.next_command_id(), 1_000_401)
        again = lc.CommandLifecycle(self.path)
        self.assertEqual(again.next_command_id(), 1_000_401)

    def test_old_v8_ids_do_not_count(self):
        self._send(cmd_id=1400)
        self.assertEqual(self.store.next_command_id(), 1_000_000)

    def test_service_range_ids_from_the_cli_ignored(self):
        # a signed service id in the shared send log must not push the GUI
        # into the service range (which needs a signature)
        self.assertEqual(self.store.next_command_id(
            extra_used=[1_000_007, 100_000_001]), 1_000_008)

    def test_range_exhausted_raises(self):
        with self.assertRaises(ValueError):
            self.store.next_command_id(extra_used=[lc.REMOTE_ID_CEILING])


if __name__ == "__main__":
    unittest.main()
