#!/usr/bin/env python3
# filename: test_gui_server.py
# description: Sprint10 §7 — tests for the operator GUI server logic.
"""
Sprint10 — tests for tools/bm_command_gui/server.py (GuiState; no real
HTTP server, no network — post/fetch are mocked).

Pins the D9/D10 contract: dropdown options generated from
command_tables, pending lockout with explicit override, shared
rate-limit view with the CLI send log, 202 -> awaiting_node, ack poll
resolving in-flight commands, and the poller surviving sweep errors.

Also pins the post-diagnosis delivery-robustness contract (fd12b23,
2026-07-31 mailbox-wedge REPORT): the DEFAULT send mode is "wake" —
the command is armed locally (scheduled_wake, no network POST) and
fires from check_wakes() when a fresh sensor-data row shows the unit
awake; check_retries() re-sends the same id until acked or
retry_exhausted. Tests that pin the direct-POST path use mode="now".

Run (repo root):
  python3 -m unittest tests.test_gui_server -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "bm_command_gui"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_tables as ct   # noqa: E402
import server as gui          # noqa: E402
import sofar_send_command as ssc  # noqa: E402

ACK_OK = {"id": 1000, "ok": 1,
          "st": {"roi": 2, "foc": 0, "awb": 0, "exp": 0, "win": 0}}


class GuiStateTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        targets = os.path.join(self.dir, "targets.json")
        with open(targets, "w") as f:
            json.dump({"targets": [{"label": "bench",
                                    "spotter_id": "SPOT-TEST",
                                    "node_id": "53171fa3d81a8e6f"}]}, f)
        self.state = gui.GuiState(
            targets,
            gui_log=os.path.join(self.dir, "gui.jsonl"),
            send_log=os.path.join(self.dir, "sends.jsonl"))
        os.environ[ssc.TOKEN_ENV] = "test-token"
        self.addCleanup(os.environ.pop, ssc.TOKEN_ENV, None)

    def _send(self, **kw):
        """Send with post_command mocked. Default mode is the server's
        default ("wake" -> scheduled_wake, no POST); pass mode="now" to
        exercise the direct-POST path."""
        args = dict(spotter_id="SPOT-TEST", node_id="53171fa3d81a8e6f",
                    c="roi", v=2)
        args.update(kw)
        with mock.patch.object(gui.ssc, "post_command",
                               return_value=(202, {"status": "success"})):
            return self.state.send(**args)


class TestConfig(GuiStateTestCase):
    def test_dropdowns_generated_from_tables(self):
        cfg = self.state.config()
        self.assertEqual(cfg["tables_version"], ct.TABLES_VERSION)
        self.assertEqual([o["v"] for o in cfg["commands"]["roi"]],
                         sorted(ct.ROI_TABLE))
        self.assertIn(ct.ROI_TABLE[2]["label"],
                      cfg["commands"]["roi"][2]["label"])
        self.assertEqual(len(cfg["commands"]["win"]), len(ct.WIN_TABLE))
        self.assertEqual(cfg["targets"][0]["spotter_id"], "SPOT-TEST")


class TestSend(GuiStateTestCase):
    def test_default_mode_is_wake_scheduled_no_post(self):
        with mock.patch.object(gui.ssc, "post_command") as p:
            out = self.state.send("SPOT-TEST", "53171fa3d81a8e6f", "roi", 2)
        self.assertEqual(out["state"], "scheduled_wake")
        p.assert_not_called()  # armed locally; fires on the next wake
        self.assertEqual(self.state.store.scheduled()[0]["cmd_id"],
                         out["cmd_id"])

    def test_202_lands_awaiting_node(self):
        out = self._send(mode="now")
        self.assertEqual(out["state"], "awaiting_node")
        self.assertEqual(out["http_status"], 202)
        self.assertEqual(self.state.store.in_flight()[0]["cmd_id"],
                         out["cmd_id"])

    def test_in_flight_lockout_and_override(self):
        first = self._send()
        blocked = self._send()
        self.assertEqual(blocked["error"], "in_flight")
        self.assertEqual(blocked["detail"], [first["cmd_id"]])
        # override must also clear the 60 s rate limit to actually send
        with mock.patch.object(gui.ssc, "load_last_success_ts",
                               return_value=None):
            forced = self._send(override_in_flight=True)
        self.assertNotIn("error", forced)

    def test_rate_limit_shared_with_cli_send_log(self):
        self._send(mode="now")  # lands in the shared send log
        with mock.patch.object(self.state.store, "pending",
                               return_value=[]):
            out = self._send(mode="now")
        self.assertEqual(out["error"], "rate_limited")
        self.assertGreater(out["retry_in_s"], 0)

    def test_invalid_value_rejected_before_network(self):
        with mock.patch.object(gui.ssc, "post_command") as p:
            out = self.state.send("SPOT-TEST", "n", "roi", 99)
        self.assertIn("invalid command", out["error"])
        p.assert_not_called()

    def test_non_202_lands_send_failed(self):
        with mock.patch.object(gui.ssc, "post_command",
                               return_value=(400, {"status": "bad request"})):
            out = self.state.send("SPOT-TEST", "n", "ping", None, mode="now")
        self.assertEqual(out["state"], "send_failed")
        self.assertEqual(self.state.store.in_flight(), [])


class TestWakeAndRetry(GuiStateTestCase):
    def test_cancel_scheduled_command(self):
        out = self._send()  # default wake mode -> scheduled
        cancelled = self.state.cancel(out["cmd_id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(self.state.store.scheduled(), [])
        again = self.state.cancel(out["cmd_id"])
        self.assertIn("only scheduled", again["error"])

    def test_fresh_row_fires_scheduled_command(self):
        out = self._send()  # scheduled_wake
        with mock.patch.object(gui.spa, "fetch_latest_row_utc",
                               return_value="2099-01-01T00:00:00.000Z"), \
             mock.patch.object(gui.ssc, "post_command",
                               return_value=(202, {"status": "success"})) as p:
            self.state.check_wakes()
        p.assert_called_once()
        self.assertEqual(self.state.store.get(out["cmd_id"])["state"],
                         "awaiting_node")

    def test_stale_row_does_not_fire(self):
        out = self._send()  # scheduled_wake
        with mock.patch.object(gui.spa, "fetch_latest_row_utc",
                               return_value="2020-01-01T00:00:00.000Z"), \
             mock.patch.object(gui.ssc, "post_command") as p:
            self.state.check_wakes()
        p.assert_not_called()
        self.assertEqual(self.state.store.get(out["cmd_id"])["state"],
                         "scheduled_wake")

    def test_overdue_command_retried_same_id(self):
        out = self._send(mode="now")  # awaiting_node, attempt 1
        self.state.retry_after_s = 0
        with mock.patch.object(gui.ssc, "load_last_success_ts",
                               return_value=None), \
             mock.patch.object(gui.ssc, "post_command",
                               return_value=(202, {"status": "success"})) as p:
            self.state.check_retries()
        p.assert_called_once()
        cmd = self.state.store.get(out["cmd_id"])
        self.assertEqual(cmd["state"], "awaiting_node")
        self.assertEqual(cmd["attempt"], 2)

    def test_retry_exhausted_at_max_attempts(self):
        out = self._send(mode="now")  # attempt 1
        self.state.retry_after_s = 0
        self.state.max_attempts = 1
        with mock.patch.object(gui.ssc, "post_command") as p:
            self.state.check_retries()
        p.assert_not_called()  # gives up instead of re-sending
        self.assertEqual(self.state.store.get(out["cmd_id"])["state"],
                         "retry_exhausted")


class TestAckPolling(GuiStateTestCase):
    def test_poll_resolves_in_flight_command(self):
        out = self._send(mode="now")  # roi=2
        ack = dict(ACK_OK, id=out["cmd_id"])
        with mock.patch.object(gui.spa, "fetch_acks",
                               return_value=[("2026-07-27T18:00:00Z", ack,
                                              "53171fa3d81a8e6f")]):
            self.state.poll_acks_once()
        self.assertEqual(self.state.store.get(out["cmd_id"])["state"],
                         "acked")
        self.assertEqual(self.state.last_poll["acks_seen"], 1)
        self.assertIsNone(self.state.last_poll["error"])

    def test_sweep_error_recorded_not_raised(self):
        self._send(mode="now")
        with mock.patch.object(gui.spa, "fetch_acks",
                               side_effect=OSError("api down")):
            self.state.poll_acks_once()
        self.assertIn("api down", self.state.last_poll["error"])

    def test_no_awaiting_commands_no_fetch(self):
        with mock.patch.object(gui.spa, "fetch_acks") as f:
            self.state.poll_acks_once()
        f.assert_not_called()


if __name__ == "__main__":
    unittest.main()
