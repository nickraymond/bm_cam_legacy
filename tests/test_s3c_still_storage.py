#!/usr/bin/env python3
# filename: test_s3c_still_storage.py
# description: Sprint26 S3c.3 — the stills storage guard (tiers, heal-payload protection, dry run, full).
"""
Sprint26 S3c.3 (PLAN_S3c.md J4 as amended by §5 C3–C5).

Pins, on a temp images/ + sent/ with sparse files (apparent sizes in GiB) and a
fake disk:
  - under both limits: nothing is touched, "over" False;
  - prune order: stale temp debris -> natives of non-save_local stems (oldest
    first) -> whole save_local stems -> transmitted pairs no live record names;
  - a compressed JPEG named by a LIVE sent record (the heal payload) is never
    deleted, even when that leaves the SD full; an aged record frees it;
  - pruning stops as soon as both limits hold (arithmetic, disk read once);
  - dry run deletes nothing, reports would_free_bytes, and is "full";
  - a fresh temp file (a live writer's) is never debris;
  - a delete that fails is logged and the guard goes on;
  - _still_storage_guard: supervisor only, summary "storage" only when over,
    storage_reason set/cleared, a transmit action never refused.

Run (repo root):
  python3 -m unittest tests.test_s3c_still_storage -v
"""

import collections
import io
import json
import contextlib
import os
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_media_key  # noqa: E402
import rc_still_storage as S  # noqa: E402

GIB = S.GIB
Usage = collections.namedtuple("Usage", "total used free")
LIMITS = {"max_used_pct": 75.0, "min_free_gb": 1.0, "ring_dry_run": False}


def disk(used_gib, total_gib=100):
    return lambda path: Usage(total_gib * GIB, used_gib * GIB, (total_gib - used_gib) * GIB)


class Tree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.images = os.path.join(self.tmp.name, "images")
        self.sent = os.path.join(self.tmp.name, "sent")
        os.makedirs(self.images)
        os.makedirs(self.sent)
        self.removed = []

    def file(self, name, gib=0.0, age_s=None, text=None):
        path = os.path.join(self.images, name)
        if text is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            with open(path, "wb") as fh:
                fh.truncate(int(gib * GIB))        # sparse: apparent size only
        if age_s is not None:
            t = time.time() - age_s
            os.utime(path, (t, t))
        return path

    def stem(self, stem, *, native_gib=1.0, compressed=True, output=None):
        """A stills stem as a cycle leaves it: native group (+ compressed pair)."""
        self.file(f"{stem}_native_full.jpg", native_gib)
        for side in S.NATIVE_SIDE:
            self.file(f"{stem}_native_full{side}", text="x")
        if compressed:
            self.file(f"{stem}_compressed.jpg", 0.25)
            meta = {"output": output} if output else {"capture_mode": "progressive_jpeg"}
            self.file(f"{stem}_compressed.jpg.capture_metadata.json", text=json.dumps(meta))

    BASE_KEY_S = 24 * 86400 * 30                     # some wake in 2026 (key seconds)

    def sent_record(self, stem, age_days=1.0, mtime_age_days=None):
        """A keyed sent record whose KEY is age_days before BASE_KEY_S. Its file mtime
        (the Pi clock) is now, or mtime_age_days ago: the guard must ignore it."""
        rec = os.path.join(self.sent, f"{stem}_compressed.sent.json")
        key = rc_media_key.encode_key(self.BASE_KEY_S - int(age_days * 86400))
        with open(rec, "w", encoding="utf-8") as fh:
            json.dump({"key": key,
                       "payload": os.path.join(self.images, f"{stem}_compressed.jpg")}, fh)
        if mtime_age_days is not None:
            t = time.time() - mtime_age_days * 86400
            os.utime(rec, (t, t))

    def history(self, newest_age_days=0.0, span_days=40):
        """A continuous daily sent history (no gap) from newest_age_days back span_days:
        the reference the guard ages against (the newest record, not the Pi clock)."""
        for d in range(span_days + 1):
            self.sent_record(f"h{d:03d}", age_days=newest_age_days + d)

    def run_guard(self, used_gib, limits=LIMITS, retain_days=14):
        def remove(path):
            self.removed.append(os.path.basename(path))
            os.remove(path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r = S.ensure_room(self.images, limits, sent_dir=self.sent, retain_days=retain_days,
                              disk_usage_fn=disk(used_gib), remove_fn=remove)
        self.log = out.getvalue()
        return r

    def removed_stems(self, suffix):
        return [n[: -len(suffix)] for n in self.removed if n.endswith(suffix)]


class Guard(Tree):
    def test_under_the_limits_nothing_is_touched(self):
        self.stem("2026-09-01T00:00:00Z_image")
        r = self.run_guard(used_gib=70)
        self.assertFalse(r["over"] or r["full"])
        self.assertEqual(self.removed, [])

    def test_tier_order(self):
        self.file(".x_compressed.jpg.abc.tmp", 0.5, age_s=3600)          # tier 0
        self.stem("2026-09-02T00:00:00Z_image", output="save_local")      # tier 2
        self.stem("2026-09-01T00:00:00Z_image")                           # tier 1 then 3
        self.stem("2026-09-03T00:00:00Z_image", compressed=False)         # capture-only: tier 1
        # 100 GiB, 80 used, cap 75 %: everything eligible goes (0.5 + 1 + 1 + 1.25 + 0.25 < 5)
        r = self.run_guard(used_gib=80)
        self.assertTrue(r["full"])
        order = [n for n in self.removed if n.endswith(("_native_full.jpg", ".tmp",
                                                        "_compressed.jpg"))]
        self.assertEqual(order, [
            ".x_compressed.jpg.abc.tmp",
            "2026-09-01T00:00:00Z_image_native_full.jpg",
            "2026-09-03T00:00:00Z_image_native_full.jpg",
            "2026-09-02T00:00:00Z_image_native_full.jpg",      # whole save_local stem
            "2026-09-02T00:00:00Z_image_compressed.jpg",
            "2026-09-01T00:00:00Z_image_compressed.jpg",       # unsent pair, last
        ])
        self.assertEqual(r["pruned"], {"debris": 1, "natives": 2, "save_local": 1, "unsent": 1})
        self.assertEqual(os.listdir(self.images), [])

    def test_stops_as_soon_as_the_limits_hold(self):
        for day in (1, 2, 3):
            self.stem(f"2026-09-0{day}T00:00:00Z_image")
        r = self.run_guard(used_gib=76.5)                 # 1.5 GiB over: two natives
        self.assertFalse(r["full"])
        self.assertEqual(self.removed_stems("_native_full.jpg"),
                         ["2026-09-01T00:00:00Z_image", "2026-09-02T00:00:00Z_image"])
        self.assertNotIn("2026-09-01T00:00:00Z_image_compressed.jpg", self.removed)
        self.assertEqual(r["freed_bytes"], 2 * GIB + 6)  # 2 natives + their 3-byte side files

    def test_min_free_floor_alone(self):
        self.stem("2026-09-01T00:00:00Z_image")
        limits = dict(LIMITS, max_used_pct=95.0, min_free_gb=20.0)
        r = self.run_guard(used_gib=80, limits=limits)   # 20 GiB free: not below the floor
        self.assertFalse(r["over"])
        r = self.run_guard(used_gib=80.5, limits=limits)
        self.assertTrue(r["over"])
        self.assertIn("2026-09-01T00:00:00Z_image_native_full.jpg", self.removed)

    def test_heal_payload_is_never_deleted(self):
        self.stem("2026-09-01T00:00:00Z_image")
        self.sent_record("2026-09-01T00:00:00Z_image", age_days=1)
        r = self.run_guard(used_gib=90)
        self.assertTrue(r["full"])
        self.assertIn("2026-09-01T00:00:00Z_image_native_full.jpg", self.removed)
        self.assertNotIn("2026-09-01T00:00:00Z_image_compressed.jpg", self.removed)
        self.assertTrue(os.path.exists(os.path.join(
            self.images, "2026-09-01T00:00:00Z_image_compressed.jpg.capture_metadata.json")))

    def test_an_aged_record_frees_its_pair(self):
        self.stem("2026-09-01T00:00:00Z_image")
        self.sent_record("2026-09-01T00:00:00Z_image", age_days=20)
        self.history()
        self.run_guard(used_gib=90, retain_days=14)
        self.assertIn("2026-09-01T00:00:00Z_image_compressed.jpg", self.removed)

    def test_retain_days_is_capped_at_30(self):
        self.stem("2026-09-01T00:00:00Z_image")
        self.sent_record("2026-09-01T00:00:00Z_image", age_days=31)
        self.history()
        self.run_guard(used_gib=90, retain_days=365)
        self.assertIn("2026-09-01T00:00:00Z_image_compressed.jpg", self.removed)

    def test_a_record_inside_retain_of_the_newest_stays_protected(self):
        self.stem("2026-09-01T00:00:00Z_image")
        self.sent_record("2026-09-01T00:00:00Z_image", age_days=10)
        self.history()
        self.run_guard(used_gib=90, retain_days=14)
        self.assertNotIn("2026-09-01T00:00:00Z_image_compressed.jpg", self.removed)

    def test_a_fast_pi_clock_unprotects_nothing(self):
        """The defect: payload liveness was record mtime vs the Pi clock, so a clock
        >= retain_days fast un-protected every heal payload. Now the Pi clock (mtime
        and now) is ignored: the newest record is the reference."""
        self.stem("2026-09-01T00:00:00Z_image")
        self.sent_record("2026-09-01T00:00:00Z_image", age_days=1, mtime_age_days=60)
        self.sent_record("newest", age_days=0, mtime_age_days=60)
        with contextlib.redirect_stdout(io.StringIO()):
            S.ensure_room(self.images, LIMITS, sent_dir=self.sent, retain_days=14,
                          disk_usage_fn=disk(90), remove_fn=self.removed.append,
                          now_fn=lambda: time.time() + 60 * 86400)
        self.assertNotIn("2026-09-01T00:00:00Z_image_compressed.jpg",
                         [os.path.basename(p) for p in self.removed])

    def test_a_record_behind_a_gap_stays_protected(self):
        """A jump (or a long power-off) leaves a gap > retain_days: never aged across it."""
        self.stem("2026-09-01T00:00:00Z_image")
        self.sent_record("2026-09-01T00:00:00Z_image", age_days=31)
        self.sent_record("after_the_jump", age_days=0)
        self.run_guard(used_gib=90, retain_days=14)
        self.assertNotIn("2026-09-01T00:00:00Z_image_compressed.jpg", self.removed)

    def test_dry_run_deletes_nothing_and_is_full(self):
        self.stem("2026-09-01T00:00:00Z_image")
        r = self.run_guard(used_gib=76.5, limits=dict(LIMITS, ring_dry_run=True))
        self.assertEqual(self.removed, [])
        self.assertTrue(r["full"])
        self.assertGreater(r["would_free_bytes"], GIB)
        self.assertIn("[STORE][DRY]", self.log)

    def test_fresh_temp_file_is_not_debris(self):
        self.file(".x_compressed.jpg.abc.tmp", 0.5, age_s=10)
        r = self.run_guard(used_gib=80)
        self.assertEqual(self.removed, [])
        self.assertTrue(r["full"])

    def test_a_failed_delete_is_logged_and_the_guard_goes_on(self):
        self.stem("2026-09-01T00:00:00Z_image")

        def remove(path):
            raise OSError("read-only")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            r = S.ensure_room(self.images, LIMITS, sent_dir=self.sent, retain_days=14,
                              disk_usage_fn=disk(76.5), remove_fn=remove)
        self.assertIn("failed to delete", out.getvalue())
        self.assertIn("natives", r["pruned"])

    def test_missing_images_dir(self):
        r = S.ensure_room(os.path.join(self.tmp.name, "nope"), LIMITS, sent_dir=self.sent,
                          disk_usage_fn=disk(80), log_fn=lambda *a: None)
        self.assertTrue(r["full"])


class ActionHook(Tree):
    """rc_progressive_jpeg._still_storage_guard: the action's side of the guard."""

    class Sup:
        def __init__(self, output="transmit", storage_cfg=LIMITS):
            self.output, self.storage_cfg, self.storage_reason = output, storage_cfg, "x"

    def hook(self, sup, used_gib):
        import rc_progressive_jpeg as rc
        cfg = os.path.join(self.tmp.name, "camera_schedule.yaml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(f"media_key:\n  enabled: true\n  retain_days: 14\n")
        summary = {}
        orig = S.DISK_USAGE_FN
        S.DISK_USAGE_FN = disk(used_gib)
        self.addCleanup(setattr, S, "DISK_USAGE_FN", orig)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            full = rc._still_storage_guard({"config_path": cfg}, summary, sup, self.images)
        return full, summary, out.getvalue()

    def test_under_limit_leaves_the_summary_alone(self):
        sup = self.Sup()
        full, summary, _ = self.hook(sup, 50)
        self.assertEqual((full, summary, sup.storage_reason), (False, {}, None))

    def test_full_transmit_warns_and_sets_the_reason(self):
        sup = self.Sup()
        full, summary, log = self.hook(sup, 90)
        self.assertTrue(full)
        self.assertEqual(sup.storage_reason, "storage_full")
        self.assertTrue(summary["storage"]["full"])
        self.assertIn("captures anyway", log)

    def test_legacy_and_unconfigured_boots_never_run_it(self):
        import rc_progressive_jpeg as rc
        self.assertFalse(rc._still_storage_guard({}, {}, None, self.images))
        self.assertFalse(rc._still_storage_guard({}, {}, self.Sup(storage_cfg=None), self.images))


if __name__ == "__main__":
    unittest.main()
