#!/usr/bin/env python3
# filename: test_wap_command.py
# description: Sprint15 chunk 6 — wap command: table, daemon dispatch, help/cfg.
"""
Sprint15 `wap` tests (D-S15-10), no hardware and NO network side effects —
the daemon's wap_action_fn is injected.

Covers:
  - WAP_TABLE shape + factory-reset doctrine (index 0 = client WiFi)
  - daemon: applied wap fires the action once; duplicates ack WITHOUT
    re-firing; no action wired -> loud warn but still acked; a raising
    action never kills process_pending
  - state: wap is NOT persisted as a setting (reboot = client WiFi)
  - help/cfg render wap rows; quick action parses
  - make_wap_action_fn builds the right script argv (Popen mocked)

Run: python3 -m unittest tests.test_wap_command -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_DIR = os.path.join(REPO_ROOT, "BM_Devel_Pi")

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

sys.path.insert(0, PI_DIR)

import command_tables as ct  # noqa: E402
import command_help as ch  # noqa: E402
import rc_command_hooks as hooks  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from command_state import CommandState  # noqa: E402


class FakeBM:
    """Just enough BristlemouthSerial for a daemon that never starts its
    reader thread."""

    def __init__(self):
        self.uart = types.SimpleNamespace(timeout=0.1)
        self.sent = []

    def spotter_tx(self, payload):
        self.sent.append(payload)
        return True


def make_daemon(tmpdir, wap_action_fn=None):
    state = CommandState(path=os.path.join(tmpdir, "state.json"))
    daemon = CommandDaemon(FakeBM(), state, topic="bmcam/cmd",
                           wap_action_fn=wap_action_fn)
    return daemon, state


def payload(command_id, cmd="wap", value=1):
    return json.dumps({"id": command_id, "c": cmd, "v": value}).encode()


class TestWapTable(unittest.TestCase):
    def test_table_shape(self):
        # Sprint16 v7: 0 = boot default, 1 = AP now, 2 = HQ now.
        self.assertEqual(sorted(ct.WAP_TABLE), [0, 1, 2])
        # Factory-reset doctrine: index 0 must be shipped behaviour —
        # the YAML boot default, never a forced flip.
        self.assertEqual(ct.WAP_TABLE[0]["verb"], "default")
        self.assertEqual(ct.WAP_TABLE[1]["verb"], "ap")
        self.assertEqual(ct.WAP_TABLE[2]["verb"], "hq")
        # Remote flips carry a timer default; wap 0 must NOT (it cancels).
        self.assertEqual(ct.WAP_TABLE[1]["timeout_min"], 60)
        self.assertEqual(ct.WAP_TABLE[2]["timeout_min"], 60)
        self.assertNotIn("timeout_min", ct.WAP_TABLE[0])

    def test_wap_is_immediate_not_setting(self):
        self.assertIn("wap", ct.COMMANDS)
        self.assertIn("wap", ct.IMMEDIATE_COMMANDS)
        self.assertNotIn("wap", ct.SETTINGS_COMMANDS)
        self.assertNotIn("wap", ct.ACTION_COMMANDS)
        self.assertNotIn("wap", ct.DEFAULT_SETTINGS)
        self.assertTrue(ct.valid_value("wap", 1))
        self.assertTrue(ct.valid_value("wap", 2))   # v7: HQ now
        self.assertFalse(ct.valid_value("wap", 3))


class TestDaemonDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _process(self, daemon, *payloads):
        for p in payloads:
            daemon._inbound.put(p)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            events = daemon.process_pending()
        return events, out.getvalue()

    def test_applied_wap_fires_action_once(self):
        calls = []
        daemon, state = make_daemon(self.tmp.name, wap_action_fn=calls.append)
        events, _ = self._process(daemon, payload(1, value=1))
        self.assertEqual([e["action"] for e in events], ["applied"])
        self.assertEqual(calls, [1])
        self.assertEqual(daemon.pending_acks, 1)

    def test_duplicate_acks_without_refiring(self):
        calls = []
        daemon, state = make_daemon(self.tmp.name, wap_action_fn=calls.append)
        events, _ = self._process(daemon, payload(7), payload(7))
        self.assertEqual([e["action"] for e in events],
                         ["applied", "duplicate"])
        self.assertEqual(calls, [1])          # fired exactly once

    def test_no_action_wired_warns_but_acks(self):
        daemon, state = make_daemon(self.tmp.name, wap_action_fn=None)
        events, log = self._process(daemon, payload(2))
        self.assertEqual(events[0]["action"], "applied")
        self.assertIn("no action wired", log)
        self.assertEqual(daemon.pending_acks, 1)

    def test_raising_action_never_kills_processing(self):
        def boom(value):
            raise RuntimeError("hostapd exploded")

        daemon, state = make_daemon(self.tmp.name, wap_action_fn=boom)
        events, log = self._process(daemon, payload(3), payload(4, cmd="ping",
                                                               value=0))
        self.assertEqual([e["action"] for e in events],
                         ["applied", "applied"])
        self.assertIn("wap action failed", log)

    def test_wap_not_persisted_as_setting(self):
        daemon, state = make_daemon(self.tmp.name, wap_action_fn=lambda v: None)
        self._process(daemon, payload(5, value=1))
        # dedupe id recorded, but a reboot must come up in client WiFi:
        # no settings key, no pending trigger.
        self.assertNotIn("wap", state.settings)
        self.assertIsNone(state.pending_trigger)
        reloaded = CommandState(path=state.path)
        self.assertTrue(reloaded.is_duplicate(5))
        self.assertNotIn("wap", reloaded.settings)


class TestHelpCfg(unittest.TestCase):
    def test_help_includes_wap(self):
        text = "\n".join(ch.render_help())
        self.assertIn("wap - WIFI MODE", text)
        self.assertIn("open hotspot (SSID = camera name)", text)
        self.assertIn("Nereus HQ WiFi", text)
        self.assertIn("192.168.50.1:8080", text)
        self.assertIn('"c":"wap","v":1', text)      # quick action line

    def test_wap_text_reads_live_mode_file(self):
        # Sprint16: the mode FILE CONTENT drives the text (v6 read mere
        # existence).
        self.assertEqual(ch._wap_text("/nonexistent/marker"),
                         "normal WiFi (client)")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mode",
                                         delete=False) as f:
            f.write("ap\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(ch._wap_text(f.name),
                         "WiFi HOTSPOT (open, temporary)")
        with open(f.name, "w") as fh:
            fh.write("client:nereus-hq\n")
        self.assertEqual(ch._wap_text(f.name), "client WiFi (nereus-hq)")
        with open(f.name, "w") as fh:
            fh.write("joining\n")
        self.assertEqual(ch._wap_text(f.name), "joining a WiFi network...")

    def test_help_lines_stay_under_width(self):
        for line in ch.render_help():
            self.assertLessEqual(len(line), ch.MAX_LINE_CHARS, repr(line))


class TestActionBuilder(unittest.TestCase):
    def _argvs(self, yaml_body, values):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False) as f:
            f.write(yaml_body)
        self.addCleanup(os.unlink, f.name)
        with mock.patch("subprocess.Popen") as popen:
            action = hooks.make_wap_action_fn("/x/network_ap.sh",
                                              config_path=f.name)
            with contextlib.redirect_stdout(io.StringIO()):
                for v in values:
                    action(v)
        return [c.args[0] for c in popen.call_args_list]

    def test_action_argv_with_island(self):
        yaml_body = ("capture_mode: \"video\"\nnetwork:\n  default: ap\n"
                     "  ap_fallback_s: 120\n  ap_timeout_min: 30\n")
        argvs = self._argvs(yaml_body, (1, 2, 0))
        # Remote flips pass the island's timer; the script arms+verifies
        # the revert timer BEFORE flipping (refuses otherwise).
        self.assertEqual(argvs[0], ["sudo", "-n", "/x/network_ap.sh",
                                    "ap", "30"])
        self.assertEqual(argvs[1], ["sudo", "-n", "/x/network_ap.sh",
                                    "hq", "30"])
        # wap 0 re-applies the YAML boot default (here: ap).
        self.assertEqual(argvs[2], ["sudo", "-n", "/x/network_ap.sh",
                                    "default", "ap", "120"])

    def test_action_argv_without_island_uses_defaults(self):
        argvs = self._argvs("capture_mode: \"video\"\n", (1, 0))
        self.assertEqual(argvs[0], ["sudo", "-n", "/x/network_ap.sh",
                                    "ap", "60"])
        self.assertEqual(argvs[1], ["sudo", "-n", "/x/network_ap.sh",
                                    "default", "nereus_hq", "90"])


if __name__ == "__main__":
    unittest.main()
