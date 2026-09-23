#!/usr/bin/env python3
# filename: test_s3_video_tx_daemon.py
# description: Sprint25 S3 — the command daemon in the one-clip video cycle (rc_video_tx).
"""
Sprint25 S3 (sprints/Sprint25_transmit_timing_resend/RESEND_DEVICE.md §1): before S3 a
`video_tx` wake never listened for commands. These tests pin the new lifecycle with a
REAL CommandDaemon + reader thread on a fake UART (production-encoded frames), the golden
Sprint22 clip, and one ordered wire log for chunks, acks, the port close and the halt.

  ordering        a command arriving MID-BURST is parsed + persisted (pump) but its ack
                  never goes out between START and END; it goes out after END, BEFORE
                  the port closes and BEFORE the halt (extends
                  test_hlt_mid_transmit_ack_precedes_halt to the video cycle)
  shared port     the time gate gets read_spotter_utc_fn (rides the daemon's port;
                  no private /dev/ttyAMA0 open racing the reader)
  island off      no daemon, no subscribe on the wire, gate called exactly as Sprint22
  bench           --bench-commands without --transmit: daemon + bounded listen, no gate
  failure         a daemon that cannot start still reaches close + halt
  pump            transmit_video_clip with a pump emits byte-identical wire bytes
  boot marks      [BOOT] <label> uptime_s=<s> from /proc/uptime, `na` off a Pi

Run (repo root):
  python3 -m pytest tests/test_s3_video_tx_daemon.py -q
"""

import contextlib
import io
import os
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
    _stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = _stub

import rc_command_hooks as cmd_hooks  # noqa: E402
import rc_video_tx as vtx  # noqa: E402
from bm_serial import BristlemouthSerial  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from command_state import CommandState  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from rc_transmit import transmit_video_clip  # noqa: E402
from tests.test_command_integration import FakeUart, make_cmd_frame  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")
TOPIC = "bmcam/cmd"
with open(os.path.join(VEC, "payload.h264"), "rb") as _fh:
    PAYLOAD = _fh.read()


def settings(tmp):
    return {"config_path": "x.yaml", "budget_seconds": 600, "pacing_delay_seconds": 1.0,
            "pacing_chunk_b64_chars": 384, "timezone": "UTC", "enforce_time_window": False,
            "capture_backend": "auto", "power_halt_enabled": False, "power_halt_dry_run": True,
            "power_halt_mode": "halt", "power_halt_script_path": "/x", "transmit_phase_cfg": {},
            "video": {"dir": tmp, "storage": {}, "clip_minutes": 5.0,
                      "geometry": {"output_wh": (1920, 1080), "fps": 15,
                                   "crop_native_xywh": (0, 0, 4608, 2592)}}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class VideoTxHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.uart = FakeUart()
        self.bm = BristlemouthSerial(uart=self.uart, node_id=0xF365, network_type=0x02)
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.state = CommandState(path=self.state_path)
        self.daemon = CommandDaemon(self.bm, self.state, topic=TOPIC)
        self.cfg = {"enabled": True, "topic": TOPIC, "post_transmit_listen_s": 0.0,
                    "defer_acks_during_transmit": False, "state_path": self.state_path}
        self.wire = []
        self.gate_calls = []
        real_tx = self.bm.spotter_tx

        def logged_ack_tx(data, network_type=None):
            self.wire.append(("ack", data.decode("utf-8") if isinstance(data, bytes) else str(data)))
            return real_tx(data, network_type)

        self.bm.spotter_tx = logged_ack_tx

    def run_vtx(self, *, transmit=True, enabled=True, bench_commands=False,
                inject_at_sleep_call=None, inject_frame=None, daemon_factory=None):
        clk, sleeps = Clock(), {"n": 0}
        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True, source="test"))

        def gate(path, **kw):
            self.gate_calls.append(sorted(kw))
            return True, {"reason": "test gate"}

        def record(s, vcfg, video_dir, **kw):
            return {"ok": True, "stage": "done", "bytes": 1,
                    "basename": "2026-09-21T07-30-05Z_video_1920x1080_15fps",
                    "mp4": os.path.join(video_dir, "c.mp4")}

        def fit(src, work, **kw):
            return {"payload": PAYLOAD, "bytes": len(PAYLOAD), "msgs": 126, "budget_msgs": kw["budget_msgs"],
                    "used_pct": 99.3, "target_kbps": 55.7, "pass2_tries": 2, "frames_trimmed": 0,
                    "frames": 50, "duration_s": 5.0, "keyframe_end": 2236, "prescale_s": 8.2, "encode_s": 3.7}

        def sleep(seconds):
            clk.t += float(seconds)
            sleeps["n"] += 1
            if inject_at_sleep_call is not None and sleeps["n"] == inject_at_sleep_call:
                self.uart.inject(inject_frame)
            time.sleep(0.01)   # real time so the reader thread can decode the frame

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            summary = vtx.run_video_tx_cycle(
                settings(self.tmp.name), cfg, transmit=transmit, gate_fn=gate, record_fn=record,
                fit_fn=fit, tx_open_fn=lambda p: (lambda b: self.wire.append(("img", b.decode("ascii")))),
                bm_close_fn=lambda: self.wire.append(("close", None)),
                halt_fn=lambda **kw: self.wire.append(("halt", kw)) or {"action": "recorded"},
                ensure_room_fn=lambda d, st: {"paused": False}, sleep_fn=sleep, clock=clk,
                encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg",
                bm_commands_cfg=self.cfg if enabled else {"enabled": False},
                command_state=self.state if enabled else None, bench_commands=bench_commands,
                daemon_factory=daemon_factory or (lambda s, c, st: self.daemon))
        return summary, out.getvalue()

    def idx(self, kind, contains=None):
        return [i for i, (k, m) in enumerate(self.wire)
                if k == kind and (contains is None or contains in str(m))]


class TestVideoTxDaemon(VideoTxHarness):
    def test_mid_burst_command_acked_after_end_before_close_and_halt(self):
        frame = make_cmd_frame({"id": 960, "c": "hlt", "v": 1})
        summary, out = self.run_vtx(inject_at_sleep_call=20, inject_frame=frame)
        self.assertIsNone(summary["error"], summary["error"])
        self.assertEqual(summary["stage"], "done")
        self.assertIn("applied", summary["command_events"])
        start = self.idx("img", "<START IMG>")[0]
        end = self.idx("img", "<END IMG>")[0]
        acks = self.idx("ack", '"id":960')
        self.assertTrue(acks, "the mid-burst command was never acked")
        self.assertFalse([i for i in self.idx("ack") if start < i < end],
                         "an ack went on the wire between START and END (video is pump-only)")
        close, halt = self.idx("close")[0], self.idx("halt")[0]
        self.assertTrue(end < acks[0] < close < halt, self.wire[end:])
        self.assertIn("[CMD] subscribed", out)
        self.assertIn("[BOOT] cmd_subscribed", out)
        # Persisted: the NEXT boot sees the commanded halt mode.
        self.assertEqual(CommandState(path=self.state_path).settings.get("hlt"), 1)

    def test_gate_rides_the_shared_port(self):
        self.run_vtx()
        self.assertEqual(self.gate_calls, [["read_spotter_utc_fn"]])

    def test_island_off_is_sprint22(self):
        summary, out = self.run_vtx(enabled=False)
        self.assertEqual(self.gate_calls, [[]], "gate must be called exactly as before (path only)")
        self.assertEqual(self.uart.written, [], "no subscribe / no daemon traffic with the island off")
        self.assertEqual(self.idx("ack"), [])
        self.assertNotIn("[CMD] subscribed", out)
        self.assertEqual(summary["command_events"], [])
        self.assertLess(self.idx("close")[0], self.idx("halt")[0])

    def test_bench_commands_listen_without_transmit(self):
        self.cfg["post_transmit_listen_s"] = 1.0
        summary, out = self.run_vtx(transmit=False, bench_commands=True)
        self.assertEqual(summary["stage"], "done_no_transmit")
        self.assertEqual(self.gate_calls, [], "no time gate without --transmit")
        self.assertEqual(summary.get("listen_tail_s"), 1.0)
        self.assertEqual(self.idx("img"), [], "no clip on the wire without --transmit")
        self.assertLess(self.idx("close")[0], self.idx("halt")[0])

    def test_daemon_start_failure_still_closes_and_halts(self):
        def broken(s, c, st):
            raise OSError("uart busy")

        summary, _ = self.run_vtx(daemon_factory=broken)
        self.assertIn("uart busy", summary["error"])
        self.assertEqual(summary["stage"], "daemon_start")
        self.assertTrue(self.idx("close") and self.idx("halt"))
        self.assertLess(self.idx("close")[0], self.idx("halt")[0])


class TestPumpWireIdentity(unittest.TestCase):
    def send(self, pump):
        wire, clk = [], Clock()
        budget = CycleBudget(600, 1.0, clock=clk)
        result = transmit_video_clip(
            wire.append, budget, payload=PAYLOAD, file_name="2026-09-20T06-10-00Z_video_5s.h264",
            fps=10, dur=5.0, res="480x270", crop="na", crf=40, keyframe_chunks=8,
            chunk_b64_chars=384, delay_seconds=1.0, current_timestamp="2026-09-20T06:10:04Z",
            sleep_fn=lambda s: setattr(clk, "t", clk.t + s), clock=clk, pending_pump_fn=pump)
        return wire, result

    def test_pump_changes_nothing_on_the_wire(self):
        calls = {"n": 0}
        with_pump, r1 = self.send(lambda: calls.__setitem__("n", calls["n"] + 1))
        without, r2 = self.send(None)
        self.assertEqual(with_pump, without)
        self.assertEqual(r1["sent"], 126)
        self.assertEqual(calls["n"], 1 + 126 + 8, "one pump per pacing slot: START + chunks + repeat")

    def test_a_failing_pump_never_breaks_the_send(self):
        def boom():
            raise RuntimeError("pump failed")

        with contextlib.redirect_stdout(io.StringIO()):
            wire, result = self.send(boom)
        self.assertTrue(result["complete_send"])
        self.assertEqual(wire, self.send(None)[0])


class TestBootMark(unittest.TestCase):
    def test_reads_proc_uptime_or_prints_na(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("38.51 120.00\n")
        self.addCleanup(os.unlink, fh.name)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cmd_hooks.boot_mark("main_entry", uptime_path=fh.name), 38.51)
            self.assertIsNone(cmd_hooks.boot_mark("halt", uptime_path="/nonexistent/uptime"))
        self.assertEqual(out.getvalue().splitlines(),
                         ["[BOOT] main_entry uptime_s=38.51", "[BOOT] halt uptime_s=na"])


class TestPredicate(unittest.TestCase):
    def test_d11(self):
        on, st = {"enabled": True}, object()
        self.assertTrue(cmd_hooks.should_run_daemon(on, st, True, False))
        self.assertTrue(cmd_hooks.should_run_daemon(on, st, False, True))
        self.assertFalse(cmd_hooks.should_run_daemon(on, st, False, False))
        self.assertFalse(cmd_hooks.should_run_daemon(on, None, True, False))
        self.assertFalse(cmd_hooks.should_run_daemon({"enabled": False}, st, True, False))
        self.assertFalse(cmd_hooks.should_run_daemon(None, st, True, False))


if __name__ == "__main__":
    unittest.main()
