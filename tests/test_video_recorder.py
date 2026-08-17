#!/usr/bin/env python3
# filename: test_video_recorder.py
# description: Sprint15 chunk 2 — clip pipeline + record loop (D-S15-2/4/8).
"""
Sprint15 recorder tests. Encoder/muxer subprocesses are FAKED via the
injected run_fn (D-S15-2 test strategy) — the fake writes the output file
each argv names, or fails at a chosen stage. Covers:

  - naming (D-S15-4 exact format)
  - the crash-safe pipeline: .part/.tmp -> fsync -> atomic rename; debris
    cleaned on every failure stage; poster failure non-fatal
  - boot-time debris sweep
  - the loop: max_clips bail, session_minutes -> normal halt path,
    ring-pause -> recheck -> resume, failure backoff, hooks never fatal

Run: python3 -m unittest tests.test_video_recorder -v
"""

import contextlib
import copy
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
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

import video_recorder as vr  # noqa: E402
import video_ring  # noqa: E402

NOW = datetime(2026, 8, 17, 23, 40, 0, tzinfo=timezone.utc)
EXPECTED_BASE = "2026-08-17T23-40-00Z_video_1000x562_15fps"


def make_run_fn(fail_stage=None, calls=None):
    """Fake pipeline runner: creates the file each argv outputs, or fails
    at `fail_stage` ('encode' | 'mux' | 'poster')."""

    def run_fn(argv, timeout_s):
        if calls is not None:
            calls.append(list(argv))
        out = argv[argv.index("-o") + 1] if "-o" in argv else argv[-1]
        if out.endswith(".h264.part"):
            if fail_stage == "encode":
                return 1, 0.5
            payload = b"h" * 1000
        elif out.endswith(".mp4.tmp"):
            if fail_stage == "mux":
                return 1, 0.1
            payload = b"m" * 800
        elif out.endswith(".jpg.tmp"):
            if fail_stage == "poster":
                return 1, 0.1
            payload = b"j" * 50
        else:
            raise AssertionError(f"unexpected output path {out}")
        with open(out, "wb") as f:
            f.write(payload)
        return 0, 0.5

    return run_fn


def make_settings(video_dir, **vcfg_over):
    vcfg = copy.deepcopy(vr.DEFAULT_VIDEO_CONFIG)
    vcfg["dir"] = video_dir
    vcfg["clip_minutes"] = 0.05          # 3 s nominal; run_fn is fake anyway
    vcfg.update(vcfg_over)
    return {
        "capture_backend": "rpicam",
        "crop_native_xywh": (1504, 846, 1600, 900),
        "output_size": (1000, 562),
        "config_path": "/nonexistent/nowhere.yaml",
        "camera_controls_override": {},   # skip the YAML island lookup
        "power_halt_enabled": False,
        "power_halt_dry_run": True,
        "power_halt_mode": "halt",
        "power_halt_script_path": "/home/pi/BM_Devel_Pi/tuned_halt.sh",
        "video": vcfg,
    }


class RecorderDirMixin:
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidrec_test_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.settings = make_settings(self.dir)

    def _record(self, run_fn, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = vr.record_one_clip(
                self.settings, self.settings["video"], self.dir,
                encoder_binary="/fake/libcamera-vid",
                ffmpeg_binary="/fake/ffmpeg",
                run_fn=run_fn, now_fn=lambda: NOW, **kwargs)
        return result, out.getvalue()

    def _names(self):
        return sorted(os.listdir(self.dir))


class TestNaming(unittest.TestCase):
    def test_clip_basename_format(self):
        self.assertEqual(
            vr.clip_basename(NOW, (1000, 562), 15), EXPECTED_BASE)

    def test_lexicographic_is_chronological(self):
        earlier = vr.clip_basename(
            datetime(2026, 8, 17, 9, 5, 0, tzinfo=timezone.utc), (1000, 562), 15)
        later = vr.clip_basename(
            datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc), (1000, 562), 15)
        self.assertLess(earlier, later)


class TestBootSweep(RecorderDirMixin, unittest.TestCase):
    def test_sweep_removes_only_debris(self):
        for name, size in [
            ("a.h264.part", 100), ("b.mp4.tmp", 100),
            ("c_thumb.jpg.tmp", 10),
            (EXPECTED_BASE + ".mp4", 800),
            (EXPECTED_BASE + "_thumb.jpg", 50),
            (EXPECTED_BASE + ".json", 20),
            ("manifest.json", 20),
        ]:
            with open(os.path.join(self.dir, name), "wb") as f:
                f.write(b"x" * size)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            removed = vr.sweep_boot_debris(self.dir)
        self.assertEqual(removed, 3)
        self.assertEqual(self._names(), [
            EXPECTED_BASE + ".json",
            EXPECTED_BASE + ".mp4",
            EXPECTED_BASE + "_thumb.jpg",
            "manifest.json",
        ])
        self.assertIn("crash debris", out.getvalue())

    def test_sweep_empty_dir(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vr.sweep_boot_debris(self.dir), 0)


class TestClipPipeline(RecorderDirMixin, unittest.TestCase):
    def test_happy_path_atomic_finals_only(self):
        result, log = self._record(make_run_fn())
        self.assertTrue(result["ok"])
        self.assertEqual(result["basename"], EXPECTED_BASE)
        self.assertEqual(result["bytes"], 800)
        self.assertEqual(self._names(), [
            EXPECTED_BASE + ".mp4", EXPECTED_BASE + "_thumb.jpg"])
        self.assertIn("[VID] clip done", log)

    def test_encoder_argv_geometry(self):
        calls = []
        self._record(make_run_fn(calls=calls))
        enc = " ".join(calls[0])
        self.assertIn("--roi 0.326389,0.326389,0.347222,0.347222", enc)
        self.assertIn("--width 1000 --height 562", enc)
        self.assertIn("-t 3000", enc)                 # 0.05 min = 3 s
        mux = " ".join(calls[1])
        self.assertIn("-c copy", mux)
        self.assertIn(EXPECTED_BASE + ".h264.part", mux)

    def test_encode_failure_cleans_part(self):
        result, log = self._record(make_run_fn(fail_stage="encode"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "encode")
        self.assertEqual(self._names(), [])
        self.assertIn("encode failed", log)

    def test_mux_failure_cleans_part_and_tmp(self):
        result, log = self._record(make_run_fn(fail_stage="mux"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "mux")
        self.assertEqual(self._names(), [])
        self.assertIn("mux failed", log)

    def test_poster_failure_keeps_clip(self):
        result, log = self._record(make_run_fn(fail_stage="poster"))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["thumb"])
        self.assertEqual(self._names(), [EXPECTED_BASE + ".mp4"])
        self.assertIn("poster extraction failed", log)

    def test_empty_part_is_failure(self):
        def run_fn(argv, timeout_s):
            out = argv[argv.index("-o") + 1] if "-o" in argv else argv[-1]
            open(out, "wb").close()           # zero bytes
            return 0, 0.5
        result, _ = self._record(run_fn)
        self.assertFalse(result["ok"])
        self.assertEqual(self._names(), [])


RING_OK = {"used_pct": 30.0, "free_gb": 100.0, "deleted": [],
           "deleted_count": 0, "would_delete_count": 0,
           "paused": False, "dry_run": False}


class TestRunLoop(RecorderDirMixin, unittest.TestCase):
    def _run(self, ring_results=None, **kwargs):
        """Run the loop with the ring MOCKED (the real ensure_room reads
        the host disk, whose fill level must not decide a unit test).
        ring_results: optional list consumed per iteration (last repeats).
        """
        out = io.StringIO()
        kwargs.setdefault("run_fn", make_run_fn())
        kwargs.setdefault("encoder_binary", "/fake/libcamera-vid")
        kwargs.setdefault("ffmpeg_binary", "/fake/ffmpeg")
        kwargs.setdefault("sleep_fn", lambda s: None)
        # unique timestamps per clip so finals never collide
        counter = {"n": 0}

        def now_fn():
            counter["n"] += 1
            return datetime(2026, 8, 17, 23, 40, counter["n"],
                            tzinfo=timezone.utc)
        kwargs.setdefault("now_fn", now_fn)

        results = list(ring_results or [RING_OK])

        def fake_ring(*a, **k):
            return results.pop(0) if len(results) > 1 else results[0]

        with mock.patch.object(video_ring, "ensure_room",
                               side_effect=fake_ring):
            with contextlib.redirect_stdout(out):
                code = vr.run_video_mode(self.settings, **kwargs)
        return code, out.getvalue()

    def test_max_clips_records_n_clips_no_halt(self):
        halt = mock.Mock()
        code, log = self._run(max_clips=3, halt_fn=halt)
        self.assertEqual(code, 0)
        mp4s = [n for n in self._names() if n.endswith(".mp4")]
        self.assertEqual(len(mp4s), 3)
        halt.assert_not_called()
        self.assertIn("max_clips=3 attempts reached", log)

    def test_session_minutes_exits_to_normal_halt(self):
        t = {"now": 0.0}

        def clock():
            return t["now"]

        base_run = make_run_fn()

        def run_fn(argv, timeout_s):
            t["now"] += 40.0                  # each subprocess ~40 fake s
            return base_run(argv, timeout_s)

        halt = mock.Mock(return_value={"action": "dry_run"})
        self.settings["video"]["session_minutes"] = 2   # 120 fake seconds
        code, log = self._run(run_fn=run_fn, clock=clock, halt_fn=halt)
        self.assertEqual(code, 0)
        halt.assert_called_once_with(
            enabled=False, dry_run=True, mode="halt",
            script_path="/home/pi/BM_Devel_Pi/tuned_halt.sh")
        self.assertIn("session_minutes reached", log)

    def test_ring_pause_rechecks_then_resumes(self):
        paused = dict(RING_OK, used_pct=90.0, free_gb=1.0, paused=True)
        sleeps = []
        code, log = self._run(
            ring_results=[paused, paused, RING_OK],
            max_clips=1, sleep_fn=sleeps.append)
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [vr.PAUSE_RECHECK_S, vr.PAUSE_RECHECK_S])
        self.assertEqual(
            len([n for n in self._names() if n.endswith(".mp4")]), 1)
        self.assertIn("[VID][PAUSE]", log)

    def test_failed_clips_bounded_and_backed_off(self):
        sleeps = []
        code, log = self._run(
            max_clips=2, run_fn=make_run_fn(fail_stage="encode"),
            sleep_fn=sleeps.append)
        self.assertEqual(code, 0)
        self.assertEqual(self._names(), [])
        self.assertEqual(sleeps, [vr.FAILED_CLIP_RETRY_S] * 2)

    def test_clip_hook_called_and_never_fatal(self):
        seen = []

        def bad_hook(clip_result, ring_result):
            seen.append(clip_result["basename"])
            raise RuntimeError("hook boom")

        code, log = self._run(max_clips=2, on_clip_fn=bad_hook)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 2)
        self.assertIn("clip boundary hook failed", log)
        self.assertEqual(
            len([n for n in self._names() if n.endswith(".mp4")]), 2)

    def test_boot_sweep_runs_before_loop(self):
        with open(os.path.join(self.dir, "stale.h264.part"), "wb") as f:
            f.write(b"x" * 100)
        code, log = self._run(max_clips=0)
        self.assertEqual(code, 0)
        self.assertEqual(self._names(), [])
        self.assertIn("removed crash debris stale.h264.part", log)


if __name__ == "__main__":
    unittest.main()
