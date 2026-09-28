#!/usr/bin/env python3
# filename: test_gui_server.py
# description: Sprint10 §7 / Sprint26 S4 c.3 — tests for the operator GUI server logic (commands v9).
"""
Tests for tools/bm_command_gui/server.py (GuiState; no real HTTP
server, no network — post/fetch are mocked).

Pins the D9/D10 contract on commands v9: controls generated from
config_registry (groups, presets, short names, guard badges), v9 JSON
built per verb with a remote-range id (floor 1e6) and validated by
command_wire.decode + the registry before any network call (> 248 B
refused), pending lockout with explicit override, shared rate-limit
view with the CLI send log, 202 -> awaiting_node, the slim-ack poll
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

import command_wire as W     # noqa: E402
import config_registry as R   # noqa: E402
import server as gui          # noqa: E402
import sofar_send_command as ssc  # noqa: E402

ACK_OK = {"id": 1_000_000, "ok": 1, "h": "a41c09e2"}


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
                    c="set", fields={"kv": {"d": 8}})
        args.update(kw)
        with mock.patch.object(gui.ssc, "post_command",
                               return_value=(202, {"status": "success"})):
            return self.state.send(**args)


class TestConfig(GuiStateTestCase):
    def test_controls_generated_from_registry(self):
        cfg = self.state.config()
        self.assertEqual(cfg["registry_version"], R.REGISTRY_VERSION)
        self.assertEqual([g["name"] for g in cfg["groups"]], R.groups())
        paths = [k["path"] for g in cfg["groups"] for k in g["keys"]]
        self.assertEqual(paths, [k.path for k in R.KEYS])
        keys = {k["path"]: k for g in cfg["groups"] for k in g["keys"]}
        crop = keys["still.crop"]
        self.assertEqual(crop["short"], "r")
        self.assertEqual([p["label"] for p in crop["presets"]],
                         [label for label, _ in R.BY_PATH["still.crop"].presets])
        self.assertEqual(keys["power.halt.enabled"]["badge"], "staged until cfm")
        self.assertEqual(keys["power.halt.enabled"]["guard_when"], [True])
        self.assertEqual(keys["mode.output"]["badge"], "reverts without cfm")
        self.assertFalse(keys["commands.runtime"]["settable"])      # locked
        self.assertFalse(keys["uplink.chunk_chars"]["settable"])    # service
        self.assertTrue(keys["video.send.duration_s"]["one_shot"])
        self.assertEqual(cfg["short_names"], R.SHORT_NAMES)
        self.assertEqual([t["v"] for t in cfg["trg_values"]], list(W.TRG_VALUES))
        self.assertEqual(cfg["id_floor"], 1_000_000)
        self.assertEqual(cfg["max_json_bytes"], 248)
        self.assertEqual(cfg["targets"][0]["spotter_id"], "SPOT-TEST")
        self.assertNotIn("roi", cfg["verbs"])
        json.dumps(cfg)   # the page gets it as JSON


class TestSend(GuiStateTestCase):
    def test_default_mode_is_wake_scheduled_no_post(self):
        with mock.patch.object(gui.ssc, "post_command") as p:
            out = self.state.send("SPOT-TEST", "53171fa3d81a8e6f", "set",
                                  {"kv": {"d": 8}})
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

    def test_invalid_commands_rejected_before_network(self):
        bad = [("roi", {"v": 2}),                                # a retired v8 verb
               ("set", {"kv": {"r": "bad"}}),                    # registry value
               ("set", {"kv": {"nope.key": 1}}),                 # unknown key
               ("set", {"kv": {"commands.runtime": "legacy"}}),  # locked
               ("get", {"k": ["a", "b", "c", "d", "e"]}),        # decode: > 4
               ("trg", {"v": 0, "kv": {"d": 8}}),                # decode: cancel + kv
               ("trg", {"v": 2, "kv": {"schedule.timezone": "UTC"}}),  # not one-shot
               ("set", {"kv": {"camera.image_processing.denoise": "x" * 48,
                               "camera.image_processing.hdr": "y" * 48,
                               "video.record.framing": "z" * 48,
                               "video.record.sensor_mode": "w" * 48}})]  # > 248 B
        with mock.patch.object(gui.ssc, "post_command") as p:
            for c, fields in bad:
                out = self.state.send("SPOT-TEST", "n", c, fields, mode="now")
                self.assertIn("invalid command", out.get("error", ""), (c, fields))
        p.assert_not_called()
        self.assertEqual(self.state.store.all_commands(), [])

    def test_every_verb_builds_v9_json(self):
        cases = [("set", {"kv": {"power.halt.enabled": False}}),
                 ("get", {"k": ["mode"]}), ("reset", {"all": 1}),
                 ("reset", {"k": ["schedule.window"]}), ("cfm", {"ref": 1_000_000}),
                 ("trg", {"v": 2, "kv": {"d": 8}}), ("hld", {"v": 30}),
                 ("ping", {}), ("help", {}), ("wap", {"v": 1})]
        for i, (c, fields) in enumerate(cases):
            out = self.state.send("SPOT-TEST", "n", c, fields,
                                  override_in_flight=True)   # wake mode: no POST
            self.assertEqual(out["cmd_id"], 1_000_000 + i, c)
            msg = self.state.store.get(out["cmd_id"])["message"]
            body = msg[len("bm pub bmcam/cmd "):-len(" 1 1")]
            cmd = W.decode(body)
            self.assertEqual((cmd.id, cmd.verb, cmd.range),
                             (out["cmd_id"], c, "remote"))
            self.assertEqual(cmd.fields, fields)

    def test_ids_skip_past_the_shared_cli_log(self):
        with open(self.state.send_log, "w") as f:
            f.write(json.dumps({"message": 'bm pub bmcam/cmd {"id":1000500,'
                                           '"c":"ping"} 1 1'}) + "\n")
        out = self._send()
        self.assertEqual(out["cmd_id"], 1_000_501)

    def test_non_202_lands_send_failed(self):
        with mock.patch.object(gui.ssc, "post_command",
                               return_value=(400, {"status": "bad request"})):
            out = self.state.send("SPOT-TEST", "n", "ping", {}, mode="now")
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


class TestUnwedge(GuiStateTestCase):
    def test_unwedge_sends_v9_ping_with_clear_queue(self):
        with mock.patch.object(gui.ssc, "post_command",
                               return_value=(202, {"status": "success"})) as p:
            out = self.state.unwedge("SPOT-TEST", "53171fa3d81a8e6f")
        (_, _, body), _ = p.call_args
        self.assertTrue(body["clear_command_queue"])
        self.assertEqual(body["message"],
                         f'bm pub bmcam/cmd {{"id":{out["cmd_id"]},"c":"ping"}} 1 1')
        self.assertEqual(out["cmd_id"], 1_000_000)
        self.assertEqual(out["state"], "awaiting_node")


class TestAckPolling(GuiStateTestCase):
    def test_poll_resolves_in_flight_command(self):
        out = self._send(mode="now")  # set d=8
        ack = dict(ACK_OK, id=out["cmd_id"])
        with mock.patch.object(gui.spa, "fetch_acks",
                               return_value=[("2026-07-27T18:00:00Z", ack,
                                              "53171fa3d81a8e6f")]):
            self.state.poll_acks_once()
        self.assertEqual(self.state.store.get(out["cmd_id"])["state"],
                         "acked")
        self.assertEqual(self.state.store.get(out["cmd_id"])["h"], "a41c09e2")
        self.assertEqual(self.state.last_poll["acks_seen"], 1)

    def test_poll_rejection_lands_rejected(self):
        out = self._send(mode="now")
        ack = {"id": out["cmd_id"], "ok": 0, "e": "xk", "k": "mode.media"}
        with mock.patch.object(gui.spa, "fetch_acks",
                               return_value=[("T", ack, "53171fa3d81a8e6f")]):
            self.state.poll_acks_once()
        cmd = self.state.store.get(out["cmd_id"])
        self.assertEqual((cmd["state"], cmd["e"], cmd["k"]),
                         ("rejected", "xk", "mode.media"))
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
