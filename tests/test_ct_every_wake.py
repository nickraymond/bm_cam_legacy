#!/usr/bin/env python3
# filename: test_ct_every_wake.py
# description: CPU temperature on every transmitting wake — END's cpu_temp_c is read at END time (peak).
"""
Nick 2026-10-03: get the camera CPU temperature out on EVERY wake. The `ct=`
key rides only <WS> lines. A video per_boot transmit wake sends no <WS> at all,
so on an alternating still/video rig ct= arrived every ~2 h. Every transmitting
wake (stills AND video) already sends END, and END already carries the
temperature as `cpu_temp_c`. What was wrong is the timing: END's value was read
BEFORE the burst (rc_progressive_jpeg / rc_video_tx built send args with
_cpu_temp_text() pre-START), so it reported the cooler pre-transmit
temperature. Now the senders pass the reader itself and rc_transmit reads it
when END is built: after the work, i.e. the wake's peak. Same source
(rc_telemetry.get_cpu_temperature, vcgencmd). No wire change.

Pins:
  END-time read   video + stills END carry the temperature read AFTER the
                  last chunk, read once
  never breaks    a failing read -> cpu_temp_c: na, END still goes out
  string kept     a string (tests, goldens) is used as given: wire unchanged
  callers         rc_video_tx.video_action passes the reader, not a pre-read value

Run (repo root):
  python3 -m pytest tests/test_ct_every_wake.py -q
"""

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")
    _stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = _stub

import rc_video_tx as vtx  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from rc_transmit import end_temp_text, transmit_progressive_image, transmit_video_clip  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")
with open(os.path.join(VEC, "payload.h264"), "rb") as _fh:
    PAYLOAD = _fh.read()           # 126 chunks at 384 b64 chars
JPEG = bytes(range(225)) * 8


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += float(s)


class TempReader:
    """A temperature that climbs with the fake clock (the burst heats the SoC);
    records when it was read."""

    def __init__(self, clk):
        self.clk, self.reads = clk, []

    def __call__(self):
        self.reads.append(self.clk.t)
        return f"{40.0 + self.clk.t / 100.0:.1f}"


def end_line(wire):
    ends = [m for m in wire if m.startswith(b"<END IMG> ")]
    assert len(ends) == 1, ends
    return ends[0].decode("ascii")


class TestEndTimeRead(unittest.TestCase):
    def test_video_end_carries_the_temperature_read_after_the_burst(self):
        clk, wire = Clock(), []
        temp = TempReader(clk)
        result = transmit_video_clip(
            wire.append, CycleBudget(1000.0, 1.0, clock=clk), payload=PAYLOAD,
            file_name="a_video_5s.h264", fps=10, dur=5.0, res="480x270", crop="na", br=40,
            chunk_b64_chars=384, delay_seconds=1.0, cpu_temp_text=temp,
            sleep_fn=clk.sleep, clock=clk)
        self.assertTrue(result["complete_send"])
        self.assertEqual(temp.reads, [128.0])  # once, after START + 126 chunks + 1 repeat
        self.assertIn("cpu_temp_c: 41.3", end_line(wire))

    def test_stills_end_carries_the_temperature_read_after_the_burst(self):
        clk, wire = Clock(), []
        temp = TempReader(clk)
        result = transmit_progressive_image(
            wire.append, CycleBudget(1000.0, 1.0, clock=clk), jpeg_data=JPEG,
            compressed_file_name="a.jpg", quality=80, enc_attempts=1, fits=True,
            chunk_b64_chars=100, delay_seconds=1.0, cpu_temp_text=temp,
            sleep_fn=clk.sleep, clock=clk, current_timestamp="2026-10-03T12:00:00Z")
        self.assertTrue(result["complete_send"])
        self.assertEqual(temp.reads, [float(result["sent"] + 1)])   # after START + chunks
        self.assertIn(f"cpu_temp_c: {40.0 + (result['sent'] + 1) / 100.0:.1f}",
                      end_line(wire))

    def test_failed_read_sends_na_and_end_still_goes_out(self):
        clk, wire = Clock(), []

        def broken():
            raise OSError("vcgencmd missing")
        transmit_video_clip(
            wire.append, CycleBudget(1000.0, 1.0, clock=clk), payload=PAYLOAD[:500],
            file_name="a_video_5s.h264", fps=10, dur=5.0, res="480x270", crop="na", br=40,
            chunk_b64_chars=384, delay_seconds=1.0, cpu_temp_text=broken,
            sleep_fn=clk.sleep, clock=clk)
        self.assertIn("cpu_temp_c: na", end_line(wire))

    def test_string_and_none_unchanged(self):
        self.assertEqual(end_temp_text("41.2"), "41.2")
        self.assertEqual(end_temp_text(None), "na")
        self.assertEqual(end_temp_text(lambda: None), "na")


class TestVideoActionPassesTheReader(unittest.TestCase):
    def test_video_action_reads_the_temperature_at_end(self):
        clk, wire = Clock(), []
        temp = TempReader(clk)
        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True,
                                                source="test"))

        def fit(src, work, **kw):
            return {"payload": PAYLOAD, "bytes": len(PAYLOAD), "msgs": 126,
                    "budget_msgs": kw["budget_msgs"], "used_pct": 99.0, "target_kbps": 55.0,
                    "pass2_tries": 1, "frames_trimmed": 0, "frames": 50, "duration_s": 5.0,
                    "keyframe_end": 2236, "prescale_s": 1.0, "encode_s": 1.0}

        with tempfile.TemporaryDirectory() as tmp:
            settings = {"config_path": "x.yaml", "budget_seconds": 480,
                        "pacing_delay_seconds": 1.0, "pacing_chunk_b64_chars": 384,
                        "timezone": "UTC", "enforce_time_window": False,
                        "capture_backend": "auto", "transmit_phase_cfg": {},
                        "video": {"dir": tmp, "storage": {}, "clip_minutes": 5.0,
                                  "geometry": {"output_wh": (1920, 1080), "fps": 15,
                                               "crop_native_xywh": (0, 0, 4608, 2592)}}}
            with mock.patch("rc_telemetry.get_cpu_temperature",
                            lambda: float(temp())), \
                    contextlib.redirect_stdout(io.StringIO()):
                summary = {"command_events": []}
                vtx.video_action(
                    settings, cfg, summary, None, CycleBudget(480, 1.0, clock=clk),
                    {"opened": False}, transmit=True, skip_time_window=False,
                    gate_fn=lambda path, **kw: (True, {"reason": "test gate"}),
                    record_fn=lambda *a, **kw: {
                        "ok": True, "stage": "done", "bytes": 1, "mp4": "c.mp4",
                        "basename": "2026-10-03T12-00-00Z_video_1920x1080_15fps"},
                    fit_fn=fit, tx_open_fn=lambda p: wire.append,
                    ensure_room_fn=lambda d, st: {"paused": False}, sleep_fn=clk.sleep,
                    clock=clk, now_fn=lambda: __import__("datetime").datetime(2026, 10, 3, 12),
                    encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg",
                    bm_commands_cfg=None, bench_commands=False, bench_drop_chunks=None)
        self.assertEqual(summary["stage"], "done")
        start_t = summary["transmit_result"]["uart_duration_sec"]   # burst began at t~0
        self.assertEqual(len(temp.reads), 1)          # not read pre-START any more
        self.assertGreaterEqual(temp.reads[0], start_t)
        self.assertIn(f"cpu_temp_c: {40.0 + temp.reads[0] / 100.0:.1f}", end_line(wire))


if __name__ == "__main__":
    unittest.main()
