#!/usr/bin/env python3
# filename: test_video_manifest_incremental.py
# description: S3c follow-up F1 — add_to_manifest equals a full rebuild, without re-reading every sidecar.
"""
S3c follow-up F1 (runs/s3c_bench_20260927/RESULTS.md §7): write_manifest re-reads
every clip's sidecar, 0.88 s for 689 clips on bmcam003 and ~25 s at a full card,
once a minute on a save_local video unit. add_to_manifest updates it for one clip.

Pins:
  - after any sequence of adds and ring deletions, the incremental manifest equals
    write_manifest's (generated_utc aside);
  - it opens only the manifest itself (no sidecar re-reads);
  - re-adding a stem does not duplicate it;
  - a missing, torn or foreign manifest falls back to the full rebuild.

Run (repo root):
  python3 -m unittest tests.test_video_manifest_incremental -v
"""

import builtins
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import video_manifest as VM  # noqa: E402


def stem(i):
    return f"2026-09-28T00-{i // 60:02d}-{i % 60:02d}Z_video_1920x1080_15fps"


class Incremental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name

    def clip(self, i, thumb=False):
        s = stem(i)
        with open(os.path.join(self.d, s + ".mp4"), "wb") as fh:
            fh.write(b"\0" * (100 + i))
        if thumb:
            open(os.path.join(self.d, s + "_thumb.jpg"), "wb").close()
        record = {"dur": 7, "res": "1920x1080", "fps": 15, "br": 2.0, "preset": None,
                  "scale": 0.833, "output": "save_local"}
        VM.write_sidecar(self.d, s, record)
        return s, record

    def delete(self, i):
        for suffix in (".mp4", ".json", "_thumb.jpg"):
            p = os.path.join(self.d, stem(i) + suffix)
            if os.path.exists(p):
                os.remove(p)

    def manifest(self):
        with open(os.path.join(self.d, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        m.pop("generated_utc")
        return m

    def rebuilt(self):
        other = tempfile.mkdtemp(dir=self.d)
        for name in os.listdir(self.d):
            src = os.path.join(self.d, name)
            if os.path.isfile(src) and name != "manifest.json":
                os.symlink(src, os.path.join(other, name))
        VM.write_manifest(other, "x")
        with open(os.path.join(other, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        m.pop("generated_utc")
        for name in os.listdir(other):
            os.remove(os.path.join(other, name))
        os.rmdir(other)
        return m

    def test_equals_a_full_rebuild_through_adds_and_ring_deletions(self):
        for i in range(12):
            s, rec = self.clip(i, thumb=i % 3 == 0)
            removed = []
            if i >= 5:                               # the ring keeps 5 clips
                self.delete(i - 5)
                removed = [stem(i - 5)]
            VM.add_to_manifest(self.d, s, rec, removed_stems=removed, generated_utc="t")
            self.assertEqual(self.manifest(), self.rebuilt(), i)
        self.assertEqual(self.manifest()["count"], 5)
        self.assertEqual(self.manifest()["clips"][0]["name"], stem(11) + ".mp4")   # newest first

    def test_reads_only_the_manifest(self):
        for i in range(3):
            s, rec = self.clip(i)
            VM.add_to_manifest(self.d, s, rec, generated_utc="t")
        s, rec = self.clip(3)
        real_open = builtins.open
        opened = []

        def spy(path, mode="r", *a, **k):
            if "r" in mode and "+" not in mode:
                opened.append(os.path.basename(str(path)))
            return real_open(path, mode, *a, **k)
        with mock.patch("builtins.open", spy):
            VM.add_to_manifest(self.d, s, rec, generated_utc="t")
        self.assertEqual(opened, ["manifest.json"])

    def test_re_adding_a_stem_does_not_duplicate(self):
        s, rec = self.clip(0)
        VM.add_to_manifest(self.d, s, rec, generated_utc="t")
        VM.add_to_manifest(self.d, s, rec, generated_utc="t")
        self.assertEqual(self.manifest()["count"], 1)

    def test_missing_torn_or_foreign_manifest_falls_back_to_rebuild(self):
        for i in range(3):
            self.clip(i)
        path = os.path.join(self.d, "manifest.json")
        for content in (None, "{\"clips\": [", json.dumps({"schema": "other", "clips": []})):
            if content is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
            s, rec = self.clip(2)
            VM.add_to_manifest(self.d, s, rec, generated_utc="t")
            self.assertEqual(self.manifest(), self.rebuilt(), repr(content)[:20])
            self.assertEqual(self.manifest()["count"], 3)


if __name__ == "__main__":
    unittest.main()
