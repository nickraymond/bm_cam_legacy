#!/usr/bin/env python3
# filename: test_command_daemon.py
# description: Sprint10 §2 — CommandDaemon reader/apply/ack flow on a fake UART.
"""
Sprint10 — tests for command_daemon.py.

Runs the REAL reader thread against a fake UART: command frames are
encoded with the production bm_serial encoder, injected as received
bytes, and must come out the other side as applied state + acks written
back through spotter_tx. Also covers: duplicate/reject/unackable
classification, time sync over the shared port (raw clock payload, same
pattern-scan as production), YAML island loading, persist-failure ack
policy, and reader resilience to uart read errors.

Run (repo root; serial stubbed, no UART):
  python3 -m unittest tests.test_command_daemon -v
"""

import json
import os
import queue
import struct
import sys
import tempfile
import time
import types
import unittest
import datetime as dt

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

from bm_serial import BristlemouthSerial  # noqa: E402
from command_daemon import (  # noqa: E402
    DEFAULT_BM_COMMANDS_CONFIG,
    CommandDaemon,
    load_bm_commands_config,
)
from command_state import CommandState  # noqa: E402

TOPIC = "bmcam/cmd"


class FakeUart:
    """Injectable fake serial port: read() drains injected chunks."""

    def __init__(self, timeout=0.1):
        self.timeout = timeout
        self.written = []
        self._rx = queue.Queue()
        self.fail_next_reads = 0

    def inject(self, data):
        self._rx.put(bytes(data))

    def read(self, n):
        if self.fail_next_reads > 0:
            self.fail_next_reads -= 1
            raise OSError("simulated uart glitch")
        try:
            return self._rx.get(timeout=0.01)
        except queue.Empty:
            return b""

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def make_cmd_frame(payload_dict, topic=TOPIC):
    """Encode a command frame exactly as the production encoder would."""
    encoder = BristlemouthSerial(uart=FakeUart(), node_id=0xF365, network_type=0x02)
    topic_b = topic.encode()
    payload = json.dumps(payload_dict).encode()
    packet = encoder.get_pub_header() + len(topic_b).to_bytes(2, "little") + topic_b + payload
    return encoder.finalize_packet(packet)


class DaemonTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.uart = FakeUart()
        self.bm = BristlemouthSerial(uart=self.uart, node_id=0xF365, network_type=0x02)
        self.state = CommandState(path=os.path.join(self.tmpdir.name, "state.json"))
        self.daemon = CommandDaemon(self.bm, self.state, topic=TOPIC)

    def tearDown(self):
        self.daemon.stop(join_timeout=1.0)

    def _await(self, predicate, timeout=3.0, message="condition"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(f"timeout awaiting {message}")

    def _written_blob(self):
        return b"".join(self.uart.written)


class TestLifecycle(DaemonTestCase):
    def test_start_requires_uart_timeout(self):
        bm = BristlemouthSerial(uart=FakeUart(timeout=None), node_id=1, network_type=0x02)
        daemon = CommandDaemon(bm, self.state, topic=TOPIC)
        with self.assertRaises(RuntimeError):
            daemon.start()

    def test_start_sends_subscribe_and_stop_joins(self):
        self.daemon.start()
        self.assertEqual(len(self.uart.written), 1)  # SUB frame
        self.assertIn(TOPIC.encode(), self.daemon.accumulator.topic)
        self.daemon.stop(join_timeout=1.0)
        self.assertFalse(self.daemon._reader.is_alive())


class TestCommandFlow(DaemonTestCase):
    def test_apply_persist_ack_end_to_end(self):
        self.daemon.start()
        self.uart.inject(make_cmd_frame({"id": 417, "c": "roi", "v": 2}))
        self._await(lambda: self.daemon._inbound.qsize() > 0, message="frame decode")

        events = self.daemon.process_pending()
        self.assertEqual([e["action"] for e in events], ["applied"])
        self.assertEqual(self.state.settings["roi"], 2)
        # Persisted (survives "reboot"):
        self.assertEqual(CommandState(path=self.state.path).settings["roi"], 2)

        self.assertEqual(self.daemon.pending_acks, 1)
        self.assertEqual(self.daemon.drain_acks(), 1)
        self.assertEqual(self.daemon.pending_acks, 0)
        # Ack JSON rides spotter/transmit-data; ASCII survives COBS.
        self.assertIn(b'"id":417,"ok":1', self._written_blob())
        self.assertIn(b'"roi":2', self._written_blob())

    def test_duplicate_id_acked_not_reapplied(self):
        self.daemon.start()
        self.uart.inject(make_cmd_frame({"id": 7, "c": "win", "v": 3}))
        self._await(lambda: self.daemon._inbound.qsize() > 0, message="first frame")
        self.daemon.process_pending()
        self.assertEqual(self.state.settings["win"], 3)

        # Same id again with a DIFFERENT value: must not re-apply (D4).
        self.uart.inject(make_cmd_frame({"id": 7, "c": "win", "v": 1}))
        self._await(lambda: self.daemon._inbound.qsize() > 0, message="dup frame")
        events = self.daemon.process_pending()
        self.assertEqual([e["action"] for e in events], ["duplicate"])
        self.assertEqual(self.state.settings["win"], 3)
        self.assertEqual(self.daemon.stats["duplicates"], 1)
        self.assertEqual(self.daemon.pending_acks, 2)

    def test_invalid_value_rejected_with_error_ack(self):
        self.daemon.start()
        self.uart.inject(make_cmd_frame({"id": 9, "c": "awb", "v": 42}))
        self._await(lambda: self.daemon._inbound.qsize() > 0, message="frame")
        events = self.daemon.process_pending()
        self.assertEqual([e["action"] for e in events], ["rejected"])
        self.assertEqual(self.state.settings, dict(CommandState(path=self.state.path).settings))
        self.daemon.drain_acks()
        self.assertIn(b'"ok":0,"e":"val"', self._written_blob())

    def test_unackable_garbage_payload_dropped(self):
        self.daemon.start()
        frame = make_cmd_frame({"id": 1, "c": "ping"})
        # Wrap non-JSON bytes in a VALID frame on the command topic.
        encoder = BristlemouthSerial(uart=FakeUart(), node_id=2, network_type=0x02)
        topic_b = TOPIC.encode()
        packet = (encoder.get_pub_header() + len(topic_b).to_bytes(2, "little")
                  + topic_b + b"\x8b\x00garbled\xff")
        self.uart.inject(encoder.finalize_packet(packet))
        self.uart.inject(frame)  # stream must recover
        self._await(lambda: self.daemon._inbound.qsize() >= 2, message="both frames")
        events = self.daemon.process_pending()
        self.assertEqual(
            sorted(e["action"] for e in events), ["applied", "dropped"]
        )
        self.assertEqual(self.daemon.stats["unackable"], 1)

    def test_persist_failure_means_no_ok_ack(self):
        self.daemon.start()
        self.uart.inject(make_cmd_frame({"id": 55, "c": "foc", "v": 1}))
        self._await(lambda: self.daemon._inbound.qsize() > 0, message="frame")

        def boom(*a, **k):
            raise OSError("disk full")

        self.state.record = boom
        events = self.daemon.process_pending()
        self.assertEqual([e["action"] for e in events], ["persist_failed"])
        self.daemon.drain_acks()
        self.assertIn(b'"ok":0', self._written_blob())
        self.assertNotIn(b'"ok":1', self._written_blob())

    def test_ack_send_failure_requeues(self):
        self.daemon.start()
        self.uart.inject(make_cmd_frame({"id": 3, "c": "ping"}))
        self._await(lambda: self.daemon._inbound.qsize() > 0, message="frame")
        self.daemon.process_pending()

        original_tx = self.bm.spotter_tx
        self.bm.spotter_tx = lambda data: (_ for _ in ()).throw(OSError("tx fail"))
        self.assertEqual(self.daemon.drain_acks(), 0)
        self.assertEqual(self.daemon.pending_acks, 1)
        self.bm.spotter_tx = original_tx
        self.assertEqual(self.daemon.drain_acks(), 1)

    def test_listen_window_processes_and_acks(self):
        self.daemon.start()
        self.uart.inject(make_cmd_frame({"id": 21, "c": "exp", "v": 4}))
        events = self.daemon.listen_window(0.6)  # real time, short window
        self.assertEqual([e["action"] for e in events], ["applied"])
        self.assertEqual(self.state.settings["exp"], 4)
        self.assertEqual(self.daemon.pending_acks, 0)  # drained in-window

    def test_reader_survives_uart_errors(self):
        self.daemon.start()
        self.uart.fail_next_reads = 1
        self.uart.inject(make_cmd_frame({"id": 12, "c": "ping"}))
        self._await(lambda: self.daemon._inbound.qsize() > 0, timeout=4.0,
                    message="frame after glitch")
        self.assertGreaterEqual(self.daemon.stats["read_errors"], 1)


class TestTimeSync(DaemonTestCase):
    def test_wait_for_spotter_utc_from_raw_stream(self):
        self.daemon.start()
        utc = dt.datetime(2026, 7, 26, 17, 0, 0, tzinfo=dt.timezone.utc)
        raw = b"spotter/utc-time" + struct.pack("<Q", int(utc.timestamp() * 1e6))
        self.uart.inject(raw)
        got = self.daemon.wait_for_spotter_utc(timeout_seconds=3)
        self.assertEqual(got, utc)
        # Subscribe frames: command topic (start) + utc topic (wait call).
        self.assertEqual(len(self.uart.written), 2)

    def test_wait_for_spotter_utc_timeout(self):
        self.daemon.start()
        with self.assertRaises(TimeoutError):
            self.daemon.wait_for_spotter_utc(timeout_seconds=0.3)


class TestConfigIsland(unittest.TestCase):
    def _write(self, text):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        tmp.write(text)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_missing_island_disabled_defaults(self):
        path = self._write("capture_mode: heic\n")
        self.assertEqual(load_bm_commands_config(path), DEFAULT_BM_COMMANDS_CONFIG)

    def test_missing_file_disabled_defaults(self):
        cfg = load_bm_commands_config("/nonexistent/nowhere.yaml")
        self.assertEqual(cfg, DEFAULT_BM_COMMANDS_CONFIG)

    def test_island_values_parsed(self):
        path = self._write(
            "bm_commands:\n"
            "  enabled: true\n"
            "  topic: bmcam003/cmd\n"
            "  pre_capture_listen_s: 90\n"
            "  state_path: /tmp/x.json\n"
        )
        cfg = load_bm_commands_config(path)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["topic"], "bmcam003/cmd")
        self.assertEqual(cfg["pre_capture_listen_s"], 90.0)
        self.assertEqual(cfg["state_path"], "/tmp/x.json")

    def test_bad_values_fall_back_per_key(self):
        path = self._write(
            "bm_commands:\n"
            "  enabled: true\n"
            "  topic: ''\n"
            "  pre_capture_listen_s: -5\n"
        )
        cfg = load_bm_commands_config(path)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["topic"], DEFAULT_BM_COMMANDS_CONFIG["topic"])
        self.assertEqual(
            cfg["pre_capture_listen_s"],
            DEFAULT_BM_COMMANDS_CONFIG["pre_capture_listen_s"],
        )


if __name__ == "__main__":
    unittest.main()
