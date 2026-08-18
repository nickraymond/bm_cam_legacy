#!/usr/bin/env python3
# filename: test_video_manifest.py
# description: Sprint15 chunk 3 — sidecars, manifest.json, status messages.
"""
Sprint15 metadata tests (D-S15-4/6):
  - sidecar record fields + sha256 prefix
  - status line: exact field set, compact JSON, hard size bound
  - pause status line
  - manifest.json regeneration: newest-first, sidecar-degraded entries
  - StatusQueue: drop-oldest cap, failed send retries at next flush,
    flush never raises

Run: python3 -m unittest tests.test_video_manifest -v
"""

import collections
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import video_manifest as vm  # noqa: E402
import video_recorder  # noqa: E402

Usage = collections.namedtuple("Usage", "total used free")

BASE = "2026-08-17T23-40-00Z_video_1000x562_14fps"

# Sprint17: the sidecar's geometry comes from the VIDEO island, not the stills
# keys. SETTINGS is kept only for the arguments build_clip_record still takes.
SETTINGS = {
    "output_size": (1000, 562),
    "crop_native_xywh": (1504, 846, 1600, 900),
}


def _make_vcfg(**over):
    cfg = copy.deepcopy(video_recorder.DEFAULT_VIDEO_CONFIG)
    cfg.update(over)
    with contextlib.redirect_stdout(io.StringIO()):
        return video_recorder.validate_video_config(cfg)


VCFG = _make_vcfg(clip_minutes=5, bitrate_mbps=2.0)
RING = {"deleted_count": 2}


class SidecarMixin:
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidman_test_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.mp4 = os.path.join(self.dir, BASE + ".mp4")
        with open(self.mp4, "wb") as f:
            f.write(b"m" * 1000)

    def _clip_result(self, **over):
        result = {
            "ok": True, "basename": BASE, "mp4": self.mp4,
            "thumb": os.path.join(self.dir, BASE + "_thumb.jpg"),
            "bytes": 1000, "encode_s": 300.4, "boundary_s": 8.2,
            "requested_controls": {"camera_controls_enabled": False},
        }
        result.update(over)
        return result


class TestClipRecord(SidecarMixin, unittest.TestCase):
    def test_record_fields(self):
        record = vm.build_clip_record(
            self._clip_result(), SETTINGS, VCFG, RING,
            cpu_temp_c=52.13, disk_usage=Usage(104 * vm.GIB, 21 * vm.GIB,
                                               83 * vm.GIB))
        self.assertEqual(record["fn"], BASE + ".mp4")
        self.assertEqual(record["sz"], 1000)
        self.assertEqual(record["res"], "1000x562")
        self.assertEqual(record["fps"], 14)   # effective (mode readout clamp)
        self.assertEqual(record["br"], 2.0)
        self.assertEqual(record["dur"], 300)
        self.assertEqual(record["tmp"], 52.1)
        self.assertEqual(record["du"], 21.0)
        self.assertEqual(record["dt"], 104.0)
        self.assertEqual(record["rd"], 2)
        self.assertEqual(record["utc"], "2026-08-17T23:40:00Z")
        self.assertEqual(record["thumb"], BASE + "_thumb.jpg")
        self.assertEqual(len(record["sha256_16"]), 16)

    def test_record_survives_missing_telemetry(self):
        record = vm.build_clip_record(
            self._clip_result(thumb=None), SETTINGS, VCFG, None,
            cpu_temp_c=None, disk_usage=None)
        self.assertIsNone(record["tmp"])
        self.assertIsNone(record["du"])
        self.assertIsNone(record["thumb"])
        self.assertEqual(record["rd"], 0)

    def test_sidecar_written_atomically(self):
        record = vm.build_clip_record(
            self._clip_result(), SETTINGS, VCFG, RING)
        path = vm.write_sidecar(self.dir, BASE, record)
        self.assertEqual(os.path.basename(path), BASE + ".json")
        with open(path) as f:
            self.assertEqual(json.load(f)["fn"], BASE + ".mp4")
        self.assertFalse(os.path.exists(path + ".tmp"))


class TestStatusLine(SidecarMixin, unittest.TestCase):
    def test_status_line_exact_shape(self):
        record = vm.build_clip_record(
            self._clip_result(), SETTINGS, VCFG, RING,
            cpu_temp_c=52.1,
            disk_usage=Usage(104 * vm.GIB, 21 * vm.GIB, 83 * vm.GIB))
        line = vm.status_line_from_record(record)
        obj = json.loads(line)
        self.assertEqual(obj["t"], "vid")
        self.assertEqual(
            list(obj.keys()),
            ["t", "fn", "sz", "res", "fps", "br", "dur", "tmp", "du", "dt", "rd"])
        self.assertNotIn(" ", line)          # compact separators

    def test_status_line_size_bound(self):
        # Worst-realistic-case values must stay one message.
        record = vm.build_clip_record(
            self._clip_result(bytes=99_999_999_999), SETTINGS,
            _make_vcfg(clip_minutes=60, bitrate_mbps=25.0,
                       preset="wide_720p", fps=30),
            {"deleted_count": 9999},
            cpu_temp_c=99.9,
            disk_usage=Usage(1000 * vm.GIB, 999 * vm.GIB, vm.GIB))
        line = vm.status_line_from_record(record)
        self.assertLessEqual(
            len(line.encode("ascii")), vm.STATUS_MAX_BYTES)

    def test_pause_line(self):
        obj = json.loads(vm.pause_status_line(
            {"used_pct": 91.2, "free_gb": 3.4, "deleted_count": 0}))
        self.assertEqual(obj["t"], "vid")
        self.assertEqual(obj["a"], "pause")
        self.assertEqual(obj["du"], 91.2)


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidman_test_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.clips = [
            "2026-08-17T10-00-00Z_video_1000x562_15fps",
            "2026-08-17T10-05-00Z_video_1000x562_15fps",
            "2026-08-17T10-10-00Z_video_1000x562_15fps",
        ]
        for i, stem in enumerate(self.clips):
            with open(os.path.join(self.dir, stem + ".mp4"), "wb") as f:
                f.write(b"m" * (100 + i))
            with open(os.path.join(self.dir, stem + "_thumb.jpg"), "wb") as f:
                f.write(b"j" * 10)
            with open(os.path.join(self.dir, stem + ".json"), "w") as f:
                json.dump({"dur": 300, "res": "1000x562", "fps": 15}, f)

    def test_manifest_newest_first(self):
        path = vm.write_manifest(self.dir, generated_utc="2026-08-17T11:00:00Z")
        with open(path) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["count"], 3)
        self.assertEqual(
            [c["name"] for c in manifest["clips"]],
            [s + ".mp4" for s in reversed(self.clips)])
        newest = manifest["clips"][0]
        self.assertEqual(newest["bytes"], 102)
        self.assertEqual(newest["thumb"], self.clips[2] + "_thumb.jpg")
        self.assertEqual(newest["dur"], 300)
        self.assertEqual(newest["utc"], "2026-08-17T10:10:00Z")

    def test_manifest_degrades_without_sidecar_and_thumb(self):
        os.remove(os.path.join(self.dir, self.clips[0] + ".json"))
        os.remove(os.path.join(self.dir, self.clips[0] + "_thumb.jpg"))
        path = vm.write_manifest(self.dir)
        with open(path) as f:
            manifest = json.load(f)
        entry = manifest["clips"][-1]        # oldest = degraded one
        self.assertEqual(entry["name"], self.clips[0] + ".mp4")
        self.assertIsNone(entry["thumb"])
        self.assertIsNone(entry["dur"])

    def test_manifest_ignores_debris_and_itself(self):
        for name in ("x.h264.part", "y.mp4.tmp"):
            open(os.path.join(self.dir, name), "wb").close()
        vm.write_manifest(self.dir)
        path = vm.write_manifest(self.dir)   # rerun includes no manifest entry
        with open(path) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["count"], 3)


class TestStatusQueue(unittest.TestCase):
    def test_drop_oldest_beyond_cap(self):
        q = vm.StatusQueue(cap=3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for i in range(5):
                q.append(f"line{i}")
        self.assertEqual(q.lines, ["line2", "line3", "line4"])
        self.assertEqual(q.dropped, 2)
        self.assertIn("dropped oldest", out.getvalue())

    def test_flush_sends_fifo(self):
        q = vm.StatusQueue()
        sent = []
        q.append("a")
        q.append("b")
        with contextlib.redirect_stdout(io.StringIO()):
            n = q.flush(sent.append)
        self.assertEqual(n, 2)
        self.assertEqual(sent, ["a\n", "b\n"])
        self.assertEqual(q.lines, [])

    def test_failed_send_retries_next_flush(self):
        q = vm.StatusQueue()
        q.append("a")
        q.append("b")
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("uart busy")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(q.flush(flaky), 0)   # first send fails, stop
            self.assertEqual(q.lines, ["a", "b"])  # nothing lost
            self.assertEqual(q.flush(flaky), 2)   # retry drains all
        self.assertEqual(q.lines, [])


if __name__ == "__main__":
    unittest.main()
