#!/usr/bin/env python3
# filename: test_s3c_save_local_video.py
# description: Sprint26 S3c.5 — video x save_local: what the goldens cannot reach.
"""
Sprint26 S3c.5 (PLAN_S3c.md J3 + §5 C1, C9, C14). The wire and files are pinned
by the goldens (vectors_save_local/save_local_video*, vectors_stay_on/
stay_on_save_local_video); this file pins:

  - a failed Spotter read inside the gate: the clip is recorded and saved
    anyway (time_source "system"), the window not enforced;
  - the window disabled: the action reads Spotter time itself;
  - the saved clip is never fitted or sent; its sidecar says output save_local;
  - a sidecar/manifest failure never costs the saved clip;
  - stay_on: no per-action status line, "uplinked" False;
  - a TRANSMIT action on a full SD still raises, exactly as before S3c.

Run (repo root):
  python3 -m unittest tests.test_s3c_save_local_video -v
"""

import json
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_video_tx as vtx_mod  # noqa: E402
import video_recorder  # noqa: E402
from tests.test_s3a_supervisor import Base, quiet  # noqa: E402

VTX = {"duration_s": 5.0, "lead_in_s": 2.0, "output_wh": (480, 270), "fps": 10,
       "message_cap": 126, "keyframe_repeat_max": 30, "preset": "medium"}


class VideoSaveLocal(Base):
    def setUp(self):
        super().setUp()
        self.videos = os.path.join(self.tmp.name, "videos")
        vcfg = video_recorder.load_video_config("/nonexistent.yaml")
        vcfg["dir"] = self.videos
        self.settings = {"video": vcfg, "config_path": "/nonexistent.yaml",
                         "enforce_time_window": True, "timezone": "UTC",
                         "window_start": "00:00", "window_end": "23:59",
                         "capture_backend": "rpicam", "pacing_chunk_b64_chars": 384,
                         "pacing_delay_seconds": 1.3}
        self.fit = mock.Mock(side_effect=AssertionError("save_local never fits"))
        self.wakes = []

    def record(self, settings, vcfg, video_dir, **kw):
        base = "2026-09-24T15-00-00Z_video_480x270_15fps"
        mp4 = os.path.join(video_dir, base + ".mp4")
        with open(mp4, "wb") as fh:
            fh.write(b"\0" * 100)
        self.record_s = round(vcfg["clip_minutes"] * 60.0, 3)
        return {"ok": True, "stage": "done", "basename": base, "mp4": mp4, "bytes": 100}

    def act(self, gate, *, output="save_local", run="per_boot", room=None, time_read=None):
        boot = self.boot()
        boot.output, boot.run = output, run
        if time_read is not None:
            boot.save_local_time_read = time_read
        summary = {"command_events": [], "error": None, "stage": "start"}
        room = room or (lambda d, s: {"paused": False, "used_pct": 10.0, "free_gb": 20.0,
                                      "deleted_count": 0})
        return quiet(vtx_mod.video_action, self.settings, VTX, summary, None,
                     mock.Mock(), {"opened": False}, transmit=True, skip_time_window=False,
                     gate_fn=lambda path, **kw: gate, record_fn=self.record, fit_fn=self.fit,
                     tx_open_fn=None, ensure_room_fn=room, sleep_fn=self.clock.sleep,
                     clock=self.clock, now_fn=lambda: __import__("datetime").datetime(2026, 9, 24),
                     encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg",
                     bm_commands_cfg={}, bench_commands=False, bench_drop_chunks=None,
                     supervised=boot, wake_fn=lambda **kw: self.wakes.append(kw["action"]))

    def sidecar(self):
        with open(os.path.join(self.videos, "2026-09-24T15-00-00Z_video_480x270_15fps.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)

    def test_saved_at_record_quality_never_fitted(self):
        summary = self.act((True, {"source_time": "spotter", "reason": "ok"}))
        self.assertEqual(summary["stage"], "saved")
        self.assertEqual(self.record_s, 7.0)
        self.fit.assert_not_called()
        meta = self.sidecar()
        self.assertEqual((meta["output"], meta["time_source"], meta["dur"]),
                         ("save_local", "spotter", 7))
        self.assertTrue(os.path.exists(os.path.join(self.videos, "manifest.json")))

    def test_failed_spotter_read_records_anyway(self):
        summary = self.act((False, {"source_time": "system", "spotter_time_error": "timeout",
                                    "reason": "Spotter time unavailable"}))
        self.assertEqual(summary["stage"], "saved")
        self.assertEqual(summary["saved"]["time_source"], "system")

    def test_window_off_reads_time_itself(self):
        self.settings["enforce_time_window"] = False
        reads = []
        summary = self.act((True, {"source_time": "skipped"}),
                           time_read=lambda d, s: reads.append(1) or "spotter")
        self.assertEqual(reads, [1])
        self.assertEqual(summary["saved"]["time_source"], "spotter")

    def test_metadata_failure_keeps_the_clip(self):
        with mock.patch("video_manifest.write_sidecar", side_effect=OSError("ro")):
            summary = self.act((True, {"source_time": "spotter"}))
        self.assertEqual(summary["stage"], "saved")
        self.assertTrue(os.path.exists(summary["saved"]["mp4"]))

    def test_stay_on_sends_no_status_line(self):
        full = lambda d, s: {"paused": True, "used_pct": 99.0, "free_gb": 0.1}  # noqa: E731
        summary = self.act((True, {"source_time": "spotter"}), run="stay_on", room=full)
        self.assertEqual((summary["stage"], summary["uplinked"]), ("storage_full", False))
        self.assertEqual(self.wakes, [])
        summary = self.act((True, {"source_time": "spotter"}), run="stay_on")
        self.assertIs(summary["uplinked"], False)

    def test_transmit_on_a_full_sd_still_raises(self):
        full = lambda d, s: {"paused": True, "used_pct": 99.0, "free_gb": 0.1}  # noqa: E731
        with self.assertRaises(RuntimeError):
            self.act((True, {"source_time": "spotter"}), output="transmit", room=full)


if __name__ == "__main__":
    unittest.main()
