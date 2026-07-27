#!/usr/bin/env python3
# filename: test_command_integration.py
# description: Sprint10 §2/§4 — daemon-in-RC-cycle integration on the coral native.
"""
Sprint10 — integration tests: CommandDaemon wired into the REAL
rc_progressive_jpeg.run_cycle (compress-only on the committed coral
native; fakes for camera/halt/wake; real daemon + reader thread on a
fake UART fed production-encoded frames).

Covers the revised TRACKER §4 integration item (2026-07-26 early-halt
decision): a command in the pre-capture listen window applies to THIS
cycle's capture settings; a command arriving during transmit is acked
in a pacing slot and persists for the NEXT cycle. Plus the D14
zero-regression guard (island disabled -> no daemon, no port).

Slow-ish (real encodes). Run (repo root):
  python3 -m unittest tests.test_command_integration -v
"""

import contextlib
import io
import json
import os
import queue
import sys
import tempfile
import time
import types
import unittest

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

import command_tables as ct  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402
from bm_serial import BristlemouthSerial  # noqa: E402
from bm_frame_decoder import build_raw_pub_frame  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from command_state import CommandState  # noqa: E402

CORAL_NATIVE = os.path.join(
    REPO_ROOT, "reference_images", "prepared", "P7071008",
    "synthetic_native_4608x2592.jpg",
)
TOPIC = "bmcam/cmd"


class FakeUart:
    def __init__(self, timeout=0.1):
        self.timeout = timeout
        self.written = []
        self._rx = queue.Queue()

    def inject(self, data):
        self._rx.put(bytes(data))

    def read(self, n):
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
    # Raw mote->Pi format (Phase B capture) — no COBS, no delimiter.
    return build_raw_pub_frame(0xF365, topic, json.dumps(payload_dict))


def write_yaml():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                    encoding="utf-8")
    f.write("capture_mode: \"progressive_jpeg\"\nenforce_time_window: false\n")
    f.close()
    return f.name


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now


class IntegrationHarness(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config_path = write_yaml()
        self.addCleanup(os.unlink, self.config_path)
        self.uart = FakeUart()
        self.bm = BristlemouthSerial(uart=self.uart, node_id=0xF365,
                                     network_type=0x02)
        self.state_path = os.path.join(self.tmpdir.name, "state.json")
        self.state = CommandState(path=self.state_path)
        self.daemon = CommandDaemon(self.bm, self.state, topic=TOPIC)
        self.cfg = {"enabled": True, "topic": TOPIC,
                    "pre_capture_listen_s": 3.0, "state_path": self.state_path}

    def run_rc(self, *, transmit, bench_commands=False, enabled=True,
               inject_at_sleep_call=None, inject_frame=None):
        settings = rc.resolve_rc_settings(self.config_path)
        if enabled:
            settings = rc._apply_command_overlay(settings, self.state)
        clock = FakeClock()
        sleep_calls = {"n": 0}

        def hybrid_sleep(seconds):
            # Fake time for budget math, a slice of real time so the
            # real reader thread can decode injected frames.
            clock.now += float(seconds)
            sleep_calls["n"] += 1
            if inject_at_sleep_call is not None and \
                    sleep_calls["n"] == inject_at_sleep_call:
                self.uart.inject(inject_frame)
            time.sleep(0.02)

        tx_messages = []
        out_dir = tempfile.mkdtemp(dir=self.tmpdir.name)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            summary = rc.run_cycle(
                settings,
                transmit=transmit,
                native_path=CORAL_NATIVE,
                skip_time_window=True,
                output_dir=out_dir,
                capture_fn=None,  # native_path provided; never called
                bm_open_fn=lambda cfg: lambda p: tx_messages.append(
                    p.decode("ascii")),
                bm_close_fn=lambda: 0,
                wake_fn=lambda **kw: None,
                halt_fn=lambda **kw: {"action": "recorded"},
                sleep_fn=hybrid_sleep,
                clock=clock,
                bm_commands_cfg=self.cfg if enabled else
                    {"enabled": False},
                command_state=self.state if enabled else None,
                bench_commands=bench_commands,
                daemon_factory=lambda s, c, st: self.daemon,
            )
        return types.SimpleNamespace(summary=summary, tx=tx_messages,
                                     stdout=stdout.getvalue(),
                                     out_dir=out_dir)

    def ack_blob(self):
        return b"".join(self.uart.written)


class TestListenWindowApply(IntegrationHarness):
    def test_command_in_listen_window_governs_this_capture(self):
        # Frame is already waiting when the window opens (queued while
        # the node was off — the D5-corrected headline case).
        self.uart.inject(make_cmd_frame({"id": 417, "c": "roi", "v": 1}))
        r = self.run_rc(transmit=False, bench_commands=True)

        self.assertIn("applied", r.summary["command_events"])
        # THIS cycle captured with the commanded full-frame crop:
        sidecar = r.summary["final_path"] + ".capture_metadata.json"
        with open(sidecar) as f:
            meta = json.load(f)
        self.assertEqual(tuple(meta["crop_native_xywh"]),
                         ct.ROI_TABLE[1]["crop"])
        # Ack went out on the wire and state survives a "reboot":
        self.assertIn(b'"id":417,"ok":1', self.ack_blob())
        self.assertEqual(CommandState(path=self.state_path).settings["roi"], 1)

    def test_reject_and_duplicate_in_window(self):
        self.uart.inject(make_cmd_frame({"id": 5, "c": "awb", "v": 42}))
        self.uart.inject(make_cmd_frame({"id": 6, "c": "foc", "v": 2}))
        self.uart.inject(make_cmd_frame({"id": 6, "c": "foc", "v": 4}))
        r = self.run_rc(transmit=False, bench_commands=True)
        self.assertEqual(
            sorted(r.summary["command_events"]),
            ["applied", "duplicate", "rejected"],
        )
        self.assertIn(b'"id":5,"ok":0,"e":"val"', self.ack_blob())
        self.assertEqual(
            CommandState(path=self.state_path).settings["foc"], 2  # not 4
        )


class TestMidTransmitApply(IntegrationHarness):
    def test_command_during_transmit_acks_and_persists_for_next_cycle(self):
        frame = make_cmd_frame({"id": 900, "c": "roi", "v": 4})
        r = self.run_rc(transmit=True, inject_at_sleep_call=20,
                        inject_frame=frame)

        self.assertIn("applied", r.summary["command_events"])
        self.assertIn(b'"id":900,"ok":1', self.ack_blob())
        # This cycle's image used the YAML crop (capture predated the
        # command)...
        sidecar = r.summary["final_path"] + ".capture_metadata.json"
        with open(sidecar) as f:
            meta = json.load(f)
        self.assertEqual(tuple(meta["crop_native_xywh"]), (1504, 846, 1600, 900))
        # ...but the NEXT cycle's overlay picks it up from disk:
        next_state = CommandState(path=self.state_path)
        self.assertEqual(next_state.settings["roi"], 4)
        self.assertIn("roi", next_state.touched)
        # Image send itself is intact (START/chunks/END all present).
        self.assertTrue(r.tx[0].startswith("<START IMG>"))
        self.assertTrue(any(m.startswith("<I0>") for m in r.tx))
        self.assertIn("<END IMG>", r.tx[-1])


class TestDisabledRegressionGuard(IntegrationHarness):
    def test_disabled_island_runs_without_daemon_or_port(self):
        r = self.run_rc(transmit=False, enabled=False)
        self.assertEqual(self.uart.written, [])       # no subscribe, no acks
        self.assertNotIn("[CMD]", r.stdout)
        self.assertEqual(r.summary["command_events"], [])
        self.assertTrue(r.summary["selection"]["fits"])

    def test_daemon_stopped_after_cycle(self):
        self.uart.inject(make_cmd_frame({"id": 1, "c": "ping"}))
        self.run_rc(transmit=False, bench_commands=True)
        self.assertFalse(self.daemon._reader.is_alive())


if __name__ == "__main__":
    unittest.main()
