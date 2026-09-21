#!/usr/bin/env python3
# filename: test_rc_video_tx_cycle.py
# description: Sprint22 Phase 2 — the record -> fit -> send -> halt cycle and its config island.
"""
Sprint22 Phase 2 — rc_video_tx cycle tests. Every side effect is injected
(gate, recorder, encoder, UART, halt, clock): zero sleep, no hardware.

Pins: island defaults = DISABLED; bad values fail at config time naming the
key; the cycle's order (time gate BEFORE record); budget = min(cap, what the
time budget can pace); the wire a full cycle emits; no bus contact without
--transmit; the halt runs on every path including failures.

Run (repo root):
  python3 -m unittest tests.test_rc_video_tx_cycle -v
"""

import os
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    stub = types.ModuleType("serial")
    stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = stub

import rc_video_tx as vtx  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")


def write_yaml(text):
    fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    fh.write(text)
    fh.close()
    return fh.name


class TestConfig(unittest.TestCase):
    def test_absent_island_is_disabled_defaults(self):
        cfg = vtx.load_video_tx_config(write_yaml('capture_mode: "video"\nvideo:\n  fps: 15\n'))
        self.assertFalse(cfg["enabled"])
        self.assertEqual((cfg["source"], cfg["output_wh"], cfg["message_cap"], cfg["preset"]),
                         ("defaults", (480, 270), 126, "medium"))
        self.assertEqual(cfg["keyframe_repeat_max"], 30)
        self.assertFalse(vtx.load_video_tx_config("/nonexistent.yaml")["enabled"])

    def test_island_parses_and_stops_at_the_next_section(self):
        cfg = vtx.load_video_tx_config(write_yaml(
            'video_tx:\n  enabled: true   # soak\n  duration_s: 3\n  output: "320x180"\n'
            '  message_cap: 88\n  preset: "veryfast"\nvideo:\n  fps: 15\n'))
        self.assertTrue(cfg["enabled"])
        self.assertEqual((cfg["duration_s"], cfg["output_wh"], cfg["message_cap"], cfg["preset"], cfg["fps"]),
                         (3.0, (320, 180), 88, "veryfast", 10))

    def test_bad_values_name_the_key(self):
        for body, key in (("enabled: yes", "enabled"), ("duration_s: 0", "duration_s"),
                          ("output: 481x270", "output"), ("preset: placebo", "preset"),
                          ("message_cap: 4", "message_cap"), ("bitrate: 40", "bitrate")):
            with self.assertRaisesRegex(ValueError, f"video_tx.{key}"):
                vtx.load_video_tx_config(write_yaml(f"video_tx:\n  {body}\n"))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def settings(tmp, **kw):
    base = {"config_path": "x.yaml", "budget_seconds": 600, "pacing_delay_seconds": 1.0,
            "pacing_chunk_b64_chars": 384, "timezone": "UTC", "enforce_time_window": False,
            "capture_backend": "auto", "power_halt_enabled": True, "power_halt_dry_run": True,
            "power_halt_mode": "halt", "power_halt_script_path": "/x", "transmit_phase_cfg": {},
            "video": {"dir": tmp, "storage": {}, "clip_minutes": 5.0,
                      "geometry": {"output_wh": (1920, 1080), "fps": 15,
                                   "crop_native_xywh": (0, 0, 4608, 2592)}}}
    base.update(kw)
    return base


class TestCycle(unittest.TestCase):
    def run_cycle(self, *, transmit=True, settings_kw=None, record_ok=True, gate_allowed=True,
                  fit_error=None, elapsed_before_fit=0.0):
        tmp = tempfile.mkdtemp()
        clk, wire, calls = Clock(), [], []
        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True, source="test"))
        with open(os.path.join(VEC, "payload.h264"), "rb") as fh:
            payload = fh.read()

        def gate(path):
            calls.append("gate")
            return gate_allowed, {"reason": "test gate"}

        def record(s, vcfg, video_dir, **kw):
            calls.append(("record", round(vcfg["clip_minutes"] * 60, 3)))
            clk.t += elapsed_before_fit
            return {"ok": record_ok, "stage": "done" if record_ok else "encode", "bytes": 1,
                    "basename": "2026-09-21T07-30-05Z_video_1920x1080_15fps",
                    "mp4": os.path.join(video_dir, "c.mp4")}

        def fit(src, work, **kw):
            calls.append(("fit", kw["budget_msgs"], kw["raw_bytes_per_msg"], kw["source_wh"]))
            if fit_error:
                raise fit_error
            return {"payload": payload, "bytes": len(payload), "msgs": 126, "budget_msgs": kw["budget_msgs"],
                    "used_pct": 99.3, "target_kbps": 55.7, "pass2_tries": 2, "frames_trimmed": 0,
                    "frames": 50, "duration_s": 5.0, "keyframe_end": 2236,
                    "prescale_s": 8.2, "encode_s": 3.7}

        halts = []
        summary = vtx.run_video_tx_cycle(
            settings(tmp, **(settings_kw or {})), cfg, transmit=transmit, gate_fn=gate,
            record_fn=record, fit_fn=fit, tx_open_fn=lambda path: wire.append,
            bm_close_fn=lambda: calls.append("close"),
            halt_fn=lambda **kw: halts.append(kw) or {"action": "dry_run"},
            ensure_room_fn=lambda d, st: {"paused": False}, sleep_fn=clk.sleep, clock=clk,
            encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg")
        return summary, wire, calls, halts

    def test_full_cycle_emits_the_contract_wire(self):
        summary, wire, calls, halts = self.run_cycle()
        self.assertEqual(calls[0], "gate")                              # Spotter time BEFORE recording
        self.assertEqual(calls[1], ("record", 7.0))                     # 5 s + 2 s lead-in
        self.assertEqual(calls[2], ("fit", 126, 288, (1920, 1080)))     # cap wins: 600 s budget is ample
        start = wire[0].decode()
        self.assertTrue(start.startswith(
            "<START IMG> filename: 2026-09-21T07-30-05Z_video_5s.h264, timestamp: "))
        self.assertIn(", length: 126, fmt=h264, fps=10, dur=5.0, res=480x270, "
                      "crop=4608x2592+0+0, br=56, cmp=1", start)
        self.assertEqual(len(wire), 126 + 8 + 2)                        # chunks + keyframe repeat + START/END
        self.assertEqual(wire[127:135], wire[1:9])
        self.assertEqual(summary["fit"]["keyframe_chunks"], 8)
        self.assertIn(b"fmt: h264", wire[-1])
        self.assertTrue(summary["transmit_result"]["complete_send"])
        self.assertEqual((summary["stage"], summary["error"], len(halts)), ("done", None, 1))
        self.assertIn("close", calls)

    def test_no_transmit_never_touches_the_bus(self):
        summary, wire, calls, halts = self.run_cycle(transmit=False)
        self.assertEqual(wire, [])
        self.assertNotIn("gate", calls)
        self.assertNotIn("close", calls)
        self.assertEqual((summary["stage"], len(halts)), ("done_no_transmit", 1))

    def test_budget_is_what_the_time_budget_can_still_pace(self):
        _, _, calls, _ = self.run_cycle(settings_kw={"budget_seconds": 100})
        self.assertEqual(calls[2][1], 100 - 2 - 30)                     # paceable - START/END - repeat reserve

    def test_failures_report_the_stage_and_still_halt(self):
        summary, wire, _, halts = self.run_cycle(record_ok=False)
        self.assertEqual((wire, summary["stage"], len(halts)), ([], "record", 1))
        self.assertIn("recording failed", summary["error"])
        summary, wire, _, halts = self.run_cycle(fit_error=vtx.rc_video_clip.ClipError("prescale failed"))
        self.assertEqual((wire, summary["stage"], len(halts)), ([], "fit", 1))
        self.assertIn("prescale failed", summary["error"])

    def test_closed_window_skips_the_cycle_only_when_enforced(self):
        summary, wire, calls, halts = self.run_cycle(gate_allowed=False,
                                                     settings_kw={"enforce_time_window": True})
        self.assertEqual((wire, summary["schedule_allowed"], len(halts)), ([], False, 1))
        self.assertNotIn(("record", 7.0), calls)
        summary, wire, _, _ = self.run_cycle(gate_allowed=False)       # not enforced -> runs
        self.assertEqual(len(wire), 136)


if __name__ == "__main__":
    unittest.main()
