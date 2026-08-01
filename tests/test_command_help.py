#!/usr/bin/env python3
# filename: test_command_help.py
# description: Sprint13 — content-completeness + query semantics for help/cfg.
"""
Sprint13 — tests for command_help.py and the help/cfg query commands.

The gates these pin (SPEC acceptance gate 1):
  - CONTENT-COMPLETE: every command and every table index appears in
    render_help(); every copy-paste example round-trips through
    parse_command() (D9: help can never show a line the daemon rejects).
  - FORMAT: plain ASCII, every line under MAX_LINE_CHARS (no wrapping
    on a standard terminal).
  - CFG PARITY: rows cover every SETTINGS_COMMANDS key; values track the
    resolved settings dict; the source column flips with an active
    command override and falls back to "config file" on index 0
    (hlt/twn/tmz no-payload doctrine).
  - QUERY SEMANTICS: help/cfg change no setting, arm no trigger, and
    dedupe like any command (duplicate id acks without re-applying).

Run (repo root; pure module, no hardware):
  python3 -m unittest tests.test_command_help -v
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_tables as ct  # noqa: E402
import command_help as chp  # noqa: E402
from command_messages import parse_command  # noqa: E402
from command_state import CommandState  # noqa: E402
from command_bindings import overlay_rc_settings, overlay_camera_controls  # noqa: E402


def _resolved_settings():
    """A production-shaped resolved-settings slice (the keys render_cfg
    reads; values match the shipped profile defaults)."""
    return {
        "crop_native_xywh": (1504, 846, 1600, 900),
        "output_width": 1000,
        "output_size": (1000, 562),
        "max_run_time_min": 12,
        "budget_seconds": 720,
        "pacing_delay_seconds": 1.0,
        "message_cap": 195,
        "source_image_path": None,
        "power_halt_enabled": True,
        "power_halt_dry_run": False,
        "power_halt_source": "yaml",
        "window_start": "10:00",
        "window_end": "15:00",
        "transmit_window": "10:00-15:00",
        "window_source": "yaml",
        "timezone": "America/New_York",
        "timezone_source": "yaml",
    }


class HelpTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state = CommandState(
            path=os.path.join(self.tmpdir.name, "state.json"))


class TestHelpContentComplete(unittest.TestCase):
    def setUp(self):
        self.lines = chp.render_help()
        self.text = "\n".join(self.lines)

    def test_every_command_appears(self):
        for cmd in ct.COMMANDS:
            self.assertRegex(self.text, rf"(?m)^{cmd}\s+- ",
                             f"command {cmd} missing from help")

    def test_every_table_index_appears_with_label(self):
        # Multi-value commands list every index + its exact table label.
        for cmd in ct.COMMANDS:
            if cmd == "ping" or cmd in ct.QUERY_COMMANDS:
                continue  # single-line commands carry no v= rows
            for index, entry in ct.table_for(cmd).items():
                self.assertIn(f"v={index}   {entry['label']}", self.text,
                              f"{cmd}[{index}] missing from help")

    def test_every_note_appears(self):
        for cmd, info in ct.COMMAND_INFO.items():
            for note in info["notes"]:
                self.assertIn(note, self.text)

    def test_quick_action_examples_all_parse_valid(self):
        # D9's strongest form: every example line help prints must be a
        # command the daemon would ACCEPT verbatim.
        command_id = 101
        for _desc, cmd, value in chp.QUICK_ACTIONS:
            line = chp.example_line(cmd, value, command_id)
            self.assertIn(line, self.text)
            payload = line.split(None, 3)[3].rsplit(" 1 1", 1)[0]
            result = parse_command(payload)
            self.assertTrue(result["ok"], f"example rejected: {line}")
            self.assertEqual(result["cmd"], cmd)
            command_id += 1

    def test_force_capture_example_present(self):
        # The SPEC calls out trg 2 (force capture + send) by name.
        self.assertIn('"c":"trg","v":2', self.text)

    def test_ascii_and_width(self):
        for line in self.lines:
            self.assertTrue(all(ord(c) < 128 for c in line),
                            f"non-ASCII in: {line!r}")
            self.assertLessEqual(len(line), chp.MAX_LINE_CHARS,
                                 f"too wide: {line!r}")

    def test_tables_version_stamped(self):
        self.assertIn(f"tables v{ct.TABLES_VERSION}", self.text)

    def test_topic_parameter_flows_into_examples(self):
        text = "\n".join(chp.render_help(topic="other/topic"))
        self.assertIn("bm pub other/topic", text)
        self.assertNotIn("bm pub bmcam/cmd", text)


class TestCfgContent(HelpTestCase):
    def _render(self, settings=None, controls=None):
        return chp.render_cfg(settings or _resolved_settings(),
                              self.state, controls)

    def test_every_settings_command_has_a_row(self):
        text = "\n".join(self._render())
        for cmd in ct.SETTINGS_COMMANDS:
            self.assertIn(f"({cmd})", text, f"cfg row for {cmd} missing")
        self.assertIn("Pending trigger", text)

    def test_all_yaml_sources_when_untouched(self):
        text = "\n".join(self._render())
        self.assertNotRegex(text, r"command \w+=")
        self.assertIn("config file", text)

    def test_values_track_resolved_settings(self):
        text = "\n".join(self._render())
        self.assertIn("1600x900 - default", text)   # roi label match
        self.assertIn("12 min per wake", text)
        self.assertIn("1 s per message", text)
        self.assertIn("195 per image", text)
        self.assertIn("10:00 - 15:00", text)
        self.assertIn("halt ON (power savings)", text)
        self.assertIn("America/New_York", text)
        self.assertIn("live camera", text)

    def test_command_override_flips_source_column(self):
        # The bench gate case: an active override must show its command.
        self.state.record(2001, "hlt", 2)
        self.state.record(2002, "twn", 2)
        settings, _ = overlay_rc_settings(_resolved_settings(), self.state)
        text = "\n".join(self._render(settings))
        self.assertIn("command hlt=2", text)
        self.assertIn("command twn=2", text)
        self.assertIn("halt DRY-RUN (logs only)", text)
        self.assertIn("ALL DAY (24 h)", text)
        # Untouched rows stay file-owned.
        self.assertIn("config file", text)

    def test_index_zero_shows_config_file_source(self):
        # hlt/twn/tmz index 0 = no payload = the file governs (D-S12-1).
        self.state.record(2001, "hlt", 0)
        self.state.record(2002, "tmz", 0)
        settings, _ = overlay_rc_settings(_resolved_settings(), self.state)
        text = "\n".join(self._render(settings))
        self.assertNotIn("command hlt=0", text)
        self.assertNotIn("command tmz=0", text)

    def test_commanded_camera_controls_render(self):
        self.state.record(2001, "foc", 3)
        self.state.record(2002, "exp", 5)
        controls = overlay_camera_controls(None, self.state)
        text = "\n".join(self._render(controls=controls))
        self.assertIn("manual (lens 1)", text)
        self.assertIn("+1 EV", text)
        self.assertIn("command foc=3", text)
        self.assertIn("command exp=5", text)

    def test_pending_trigger_row(self):
        self.state.record(2001, "trg", 2)
        text = "\n".join(self._render())
        self.assertIn("armed: capture + send", text)
        self.assertIn("command trg=2", text)

    def test_reef_test_crop_renders_by_label(self):
        self.state.record(2001, "roi", 5)
        settings, _ = overlay_rc_settings(_resolved_settings(), self.state)
        text = "\n".join(self._render(settings))
        self.assertIn("800x450 - reef test A", text)
        self.assertIn("command roi=5", text)

    def test_ascii_and_width(self):
        self.state.record(2001, "hlt", 1)
        self.state.record(2002, "tmz", 1)
        self.state.record(2003, "trg", 3)
        settings, _ = overlay_rc_settings(_resolved_settings(), self.state)
        for line in self._render(settings):
            self.assertTrue(all(ord(c) < 128 for c in line),
                            f"non-ASCII in: {line!r}")
            self.assertLessEqual(len(line), chp.MAX_LINE_CHARS,
                                 f"too wide: {line!r}")

    def test_table_columns_align(self):
        # Every table row must be exactly as wide as its border bar —
        # misalignment is what a customer notices first.
        lines = self._render()
        bar_len = len(chp._ROW_BAR)
        for line in lines:
            if line.startswith(" |"):
                self.assertEqual(len(line), bar_len, f"misaligned: {line!r}")


class TestQuerySemantics(HelpTestCase):
    def test_parse_help_cfg_without_v(self):
        for cmd in ct.QUERY_COMMANDS:
            result = parse_command(('{"id":9,"c":"%s"}' % cmd).encode())
            self.assertTrue(result["ok"])
            self.assertEqual(result["value"], 0)

    def test_record_changes_no_settings_and_arms_nothing(self):
        before_settings = dict(self.state.settings)
        self.state.record(501, "help", 0)
        self.state.record(502, "cfg", 0)
        self.assertEqual(self.state.settings, before_settings)
        self.assertEqual(self.state.touched, set())
        self.assertIsNone(self.state.pending_trigger)

    def test_query_ids_dedupe(self):
        self.state.record(501, "help", 0)
        self.assertTrue(self.state.is_duplicate(501))

    def test_state_file_settings_identical_after_query(self):
        # The file may gain the dedupe id, but settings/trigger stay
        # byte-identical — a help can never change camera behaviour.
        import json
        self.state.record(1, "roi", 2)  # some pre-existing state
        with open(self.state.path) as f:
            before = json.load(f)
        self.state.record(502, "cfg", 0)
        with open(self.state.path) as f:
            after = json.load(f)
        self.assertEqual(before["settings"], after["settings"])
        self.assertEqual(before.get("pending_trigger"),
                         after.get("pending_trigger"))


class FakeBM:
    def __init__(self):
        self.sent = []           # spotter_tx (cellular)
        self.printed = []        # spotter_print (console)
        self.fail_print = False

    def spotter_tx(self, data):
        self.sent.append(data)

    def spotter_print(self, data):
        if self.fail_print:
            raise IOError("uart gone")
        self.printed.append(data)


def _bare_daemon(state, render_fn=None):
    import queue as pyqueue
    from command_daemon import CommandDaemon

    daemon = CommandDaemon.__new__(CommandDaemon)
    daemon.bm = FakeBM()
    daemon.state = state
    daemon.query_render_fn = render_fn
    daemon.console_line_delay_s = 0.0
    daemon._inbound = pyqueue.Queue()
    daemon._acks = []
    daemon._console = []
    daemon._last_ack_ts = None
    daemon.ack_interval_s = 0.0
    daemon.stats = {"applied": 0, "rejected": 0, "duplicates": 0,
                    "unackable": 0, "acks_sent": 0, "console_lines_sent": 0}
    return daemon


class TestDaemonQueryPath(unittest.TestCase):
    """process_pending applies a query like any command (ack + dedupe,
    zero settings change) and queues its console response; drain_console
    emits via bm.spotter_print — never the cellular queue."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state = CommandState(
            path=os.path.join(self.tmpdir.name, "state.json"))
        self.daemon = _bare_daemon(
            self.state, render_fn=lambda cmd: [f"{cmd} line 1",
                                               f"{cmd} line 2"])
        self.bm = self.daemon.bm

    def test_help_applied_acked_no_settings_change(self):
        before = dict(self.state.settings)
        self.daemon._inbound.put(b'{"id":700,"c":"help"}')
        events = self.daemon.process_pending()
        self.assertEqual(events[0]["action"], "applied")
        self.assertEqual(self.state.settings, before)
        self.assertEqual(self.daemon.pending_acks, 1)
        self.daemon.drain_acks(clock=lambda: 0)
        self.assertIn('"id":700', self.bm.sent[0])

    def test_query_response_reaches_console_not_cellular(self):
        self.daemon._inbound.put(b'{"id":702,"c":"help"}')
        self.daemon.process_pending()
        self.assertEqual(self.daemon.pending_console_lines, 2)
        sent = self.daemon.drain_console(sleep_fn=lambda s: None)
        self.assertEqual(sent, 2)
        self.assertEqual(self.bm.printed, ["help line 1", "help line 2"])
        self.assertEqual(self.bm.sent, [])  # no cellular traffic yet

    def test_duplicate_query_acks_without_reprint(self):
        self.daemon._inbound.put(b'{"id":701,"c":"cfg"}')
        self.daemon.process_pending()
        self.daemon.drain_console(sleep_fn=lambda s: None)
        printed_once = list(self.bm.printed)
        self.daemon._inbound.put(b'{"id":701,"c":"cfg"}')
        events = self.daemon.process_pending()
        self.assertEqual(events[0]["action"], "duplicate")
        self.assertEqual(self.daemon.stats["duplicates"], 1)
        self.daemon.drain_console(sleep_fn=lambda s: None)
        self.assertEqual(self.bm.printed, printed_once)  # no re-print

    def test_console_send_failure_requeues_and_stops(self):
        self.daemon._inbound.put(b'{"id":703,"c":"help"}')
        self.daemon.process_pending()
        self.bm.fail_print = True
        sent = self.daemon.drain_console(sleep_fn=lambda s: None)
        self.assertEqual(sent, 0)
        self.assertEqual(self.daemon.pending_console_lines, 2)
        self.bm.fail_print = False
        self.assertEqual(self.daemon.drain_console(sleep_fn=lambda s: None), 2)

    def test_no_renderer_still_acks_quietly(self):
        daemon = _bare_daemon(self.state, render_fn=None)
        daemon._inbound.put(b'{"id":704,"c":"help"}')
        events = daemon.process_pending()
        self.assertEqual(events[0]["action"], "applied")
        self.assertEqual(daemon.pending_console_lines, 0)
        self.assertEqual(daemon.pending_acks, 1)

    def test_render_failure_never_kills_processing(self):
        def boom(cmd):
            raise RuntimeError("render broke")
        daemon = _bare_daemon(self.state, render_fn=boom)
        daemon._inbound.put(b'{"id":705,"c":"cfg"}')
        events = daemon.process_pending()
        self.assertEqual(events[0]["action"], "applied")
        self.assertEqual(daemon.pending_console_lines, 0)


class TestSpotterPrintFrame(unittest.TestCase):
    """bm_serial.spotter_print — frame layout mirrors spotter_log with
    topic spotter/printf and a zero-length filename (bm_core print
    header). Bench-verified against v2.16.6 before the transport gate."""

    def _packet_for(self, call):
        from bm_serial import BristlemouthSerial

        class FakeUart:
            def __init__(self):
                self.written = b""

            def write(self, data):
                self.written += bytes(data)
                return len(data)

        uart = FakeUart()
        bm = BristlemouthSerial(uart=uart, network_type=1)
        captured = {}
        original = bm.finalize_packet

        def capture(packet):
            captured["packet"] = bytes(packet)
            return original(packet)

        bm.finalize_packet = capture
        call(bm)
        return captured["packet"], uart.written

    def test_frame_structure(self):
        packet, wire = self._packet_for(lambda bm: bm.spotter_print("hi"))
        header_len = len(bytearray.fromhex("02000000")) + 8 + 2  # pub header
        topic = b"spotter/printf"
        self.assertEqual(
            packet[header_len:header_len + 2],
            len(topic).to_bytes(2, "little"))
        self.assertEqual(
            packet[header_len + 2:header_len + 2 + len(topic)], topic)
        tail = packet[header_len + 2 + len(topic):]
        self.assertEqual(tail[:8], b"\x00" * 8)          # target node id 0
        self.assertEqual(tail[8:10], (0).to_bytes(2, "little"))   # no fname
        self.assertEqual(tail[10:12], (3).to_bytes(2, "little"))  # "hi\n"
        self.assertEqual(tail[12:], b"hi\n")
        self.assertTrue(wire.endswith(b"\x00"))          # COBS delimiter
        self.assertIn(topic, wire)  # ASCII topic survives COBS verbatim

    def test_print_and_log_share_header_convention(self):
        # Same layout as the SD twin: only topic + fname differ.
        p_print, _ = self._packet_for(lambda bm: bm.spotter_print("x"))
        p_log, _ = self._packet_for(lambda bm: bm.spotter_log("f.txt", "x"))
        self.assertEqual(p_print[:4], p_log[:4])
        self.assertIn(b"spotter/printf", p_print)
        self.assertIn(b"spotter/fprintf", p_log)
        self.assertNotIn(b"spotter/transmit-data", p_print)  # never cellular


class TestHooksConsoleFlush(unittest.TestCase):
    """rc_command_hooks: console lines flush at idle drains and before
    the halt (a help response must beat the power cut)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state = CommandState(
            path=os.path.join(self.tmpdir.name, "state.json"))
        self.daemon = _bare_daemon(
            self.state, render_fn=lambda cmd: ["only line"])

    def test_drain_now_flushes_console(self):
        import rc_command_hooks as ch
        self.daemon._inbound.put(b'{"id":710,"c":"help"}')
        summary = {"command_events": []}
        ch.drain_now(self.daemon, summary, clock=lambda: 0)
        self.assertEqual(self.daemon.bm.printed, ["only line"])
        self.assertIn("applied", summary["command_events"])

    def test_shutdown_flushes_console_before_stop(self):
        import rc_command_hooks as ch
        self.daemon._inbound.put(b'{"id":711,"c":"cfg"}')
        self.daemon.process_pending()
        self.daemon.stop = lambda: None
        summary = {"command_events": []}
        ch.shutdown(self.daemon, summary, debug_print=lambda m: None,
                    clock=lambda: 0, sleep_fn=lambda s: None)
        self.assertEqual(self.daemon.bm.printed, ["only line"])


if __name__ == "__main__":
    unittest.main()
