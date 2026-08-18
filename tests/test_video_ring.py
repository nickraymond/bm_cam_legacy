#!/usr/bin/env python3
# filename: test_video_ring.py
# description: Sprint15 chunk 2 — ring buffer policy on a temp dir (D-S15-5).
"""
Sprint15 ring-buffer tests (TODO-BM-008 rules, no hardware).

disk_usage is injected as a static snapshot; ensure_room applies deletion
effects arithmetically, so every scenario is deterministic:
  - prune oldest completed triples only, until BOTH limits are satisfied
  - min_free_gb backstop fires independently of max_used_pct
  - dry-run reports, deletes nothing, and still pauses at the real floor
  - floor unmeetable -> paused (pause-not-brick), non-clip files untouched

Run: python3 -m unittest tests.test_video_ring -v
"""

import collections
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import video_ring  # noqa: E402

Usage = collections.namedtuple("Usage", "total used free")


def _fake_usage(total, used):
    return lambda path: Usage(total, used, total - used)


class RingDirMixin:
    """Temp video dir with three completed clip triples (oldest first).

    Sizes: each mp4 100 B, thumb 10 B, sidecar 5 B -> 115 B per triple.
    """

    CLIPS = [
        "2026-08-17T10-00-00Z_video_1000x562_15fps",
        "2026-08-17T10-05-00Z_video_1000x562_15fps",
        "2026-08-17T10-10-00Z_video_1000x562_15fps",
    ]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ring_test_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for stem in self.CLIPS:
            self._write(stem + ".mp4", 100)
            self._write(stem + "_thumb.jpg", 10)
            self._write(stem + ".json", 5)

    def _write(self, name, size):
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(b"x" * size)

    def _ensure(self, storage_cfg, disk_usage_fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = video_ring.ensure_room(
                self.dir, storage_cfg, disk_usage_fn=disk_usage_fn)
        return result, out.getvalue()

    def _names(self):
        return sorted(os.listdir(self.dir))


class TestCandidates(RingDirMixin, unittest.TestCase):
    def test_only_completed_triples_are_candidates(self):
        self._write("in-flight.h264.part", 50)
        self._write("almost.mp4.tmp", 50)
        self._write("manifest.json", 20)
        self._write("stray.log", 20)
        self._write("photo_compressed.jpg", 20)
        triples = video_ring.completed_clip_triples(self.dir)
        self.assertEqual([t["stem"] for t in triples], self.CLIPS)
        self.assertEqual(triples[0]["bytes"], 115)
        self.assertEqual(len(triples[0]["files"]), 3)

    def test_missing_thumb_still_a_triple(self):
        os.remove(os.path.join(self.dir, self.CLIPS[0] + "_thumb.jpg"))
        triples = video_ring.completed_clip_triples(self.dir)
        self.assertEqual(triples[0]["bytes"], 105)
        self.assertEqual(len(triples[0]["files"]), 2)

    def test_missing_dir_is_empty(self):
        self.assertEqual(video_ring.completed_clip_triples("/nonexistent"), [])


class TestEnsureRoom(RingDirMixin, unittest.TestCase):
    CFG = {"max_used_pct": 75, "min_free_gb": 0, "ring_dry_run": False}

    def test_under_limits_no_action(self):
        result, _ = self._ensure(self.CFG, _fake_usage(1000, 500))
        self.assertEqual(result["deleted_count"], 0)
        self.assertFalse(result["paused"])
        self.assertEqual(len(self._names()), 9)

    def test_prunes_oldest_until_under_cap(self):
        # used 900/1000 = 90 % > 75 %: must free > 150 B -> two triples
        # (115 B each), oldest first; the newest survives.
        result, log = self._ensure(self.CFG, _fake_usage(1000, 900))
        self.assertEqual(result["deleted"], self.CLIPS[:2])
        self.assertFalse(result["paused"])
        remaining = self._names()
        self.assertEqual(len(remaining), 3)
        self.assertTrue(all(n.startswith(self.CLIPS[2]) for n in remaining))
        self.assertIn("[RING] deleted", log)

    def test_min_free_backstop_fires_alone(self):
        # used_pct fine (50 %) but free 500 B < floor 600 B: stricter wins.
        cfg = {"max_used_pct": 75, "min_free_gb": 600 / video_ring.GIB,
               "ring_dry_run": False}
        result, _ = self._ensure(cfg, _fake_usage(1000, 500))
        # freeing one triple (115 B) puts free at 615 B > 600 B.
        self.assertEqual(result["deleted"], self.CLIPS[:1])
        self.assertFalse(result["paused"])

    def test_dry_run_reports_deletes_nothing_and_pauses(self):
        cfg = dict(self.CFG, ring_dry_run=True)
        result, log = self._ensure(cfg, _fake_usage(1000, 900))
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["would_delete_count"], 2)
        self.assertEqual(len(self._names()), 9)      # nothing touched
        self.assertIn("[RING][DRY] would delete", log)
        # Real state unchanged and over the cap: recording must pause.
        self.assertTrue(result["paused"])
        self.assertIn("[RING][PAUSE]", log)

    def test_dry_run_under_limits_silent(self):
        cfg = dict(self.CFG, ring_dry_run=True)
        result, _ = self._ensure(cfg, _fake_usage(1000, 100))
        self.assertEqual(result["would_delete_count"], 0)
        self.assertFalse(result["paused"])

    def test_floor_unmeetable_pauses_not_bricks(self):
        # Even emptying the whole ring (345 B) cannot reach the cap:
        # used 990 -> 645/1000 = 64.5 %... it CAN here. Use a harsher cap.
        cfg = {"max_used_pct": 20, "min_free_gb": 0, "ring_dry_run": False}
        result, log = self._ensure(cfg, _fake_usage(1000, 990))
        self.assertEqual(result["deleted"], self.CLIPS)   # ring emptied
        self.assertTrue(result["paused"])
        self.assertIn("[RING][PAUSE]", log)

    def test_unmeetable_never_touches_non_clip_files(self):
        self._write("in-flight.h264.part", 50)
        self._write("manifest.json", 20)
        self._write("stray.log", 20)
        cfg = {"max_used_pct": 10, "min_free_gb": 0, "ring_dry_run": False}
        result, _ = self._ensure(cfg, _fake_usage(1000, 990))
        self.assertTrue(result["paused"])
        self.assertEqual(
            self._names(), ["in-flight.h264.part", "manifest.json", "stray.log"])

    def test_empty_ring_over_limit_pauses(self):
        for name in list(self._names()):
            os.remove(os.path.join(self.dir, name))
        result, _ = self._ensure(self.CFG, _fake_usage(1000, 990))
        self.assertEqual(result["deleted_count"], 0)
        self.assertTrue(result["paused"])

    def test_triple_deleted_together_when_thumb_missing(self):
        os.remove(os.path.join(self.dir, self.CLIPS[0] + "_thumb.jpg"))
        cfg = {"max_used_pct": 75, "min_free_gb": 0, "ring_dry_run": False}
        # need > 50 B freed: first (partial, 105 B) triple suffices.
        result, _ = self._ensure(cfg, _fake_usage(1000, 800))
        self.assertEqual(result["deleted"], self.CLIPS[:1])
        self.assertNotIn(self.CLIPS[0] + ".mp4", self._names())
        self.assertNotIn(self.CLIPS[0] + ".json", self._names())


if __name__ == "__main__":
    unittest.main()
