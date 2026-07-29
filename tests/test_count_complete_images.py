#!/usr/bin/env python3
# filename: test_count_complete_images.py
# description: Sprint11 metric 1 — offline tests for the complete-image counter.
"""
Sprint11 — tests for tools/count_complete_images.py against synthetic
backend payloads built from the REAL wire builders in rc_uplink_messages,
so the parser is pinned to the format the device actually emits rather than
to a hand-written approximation of it.

No network: `analyze`/`summarize` are pure.

Run (repo root):
  python3 -m unittest tests.test_count_complete_images -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import count_complete_images as cci  # noqa: E402
from rc_media_id import chunk_prefix  # noqa: E402
from rc_uplink_messages import (  # noqa: E402
    build_rc_end_message,
    build_rc_start_message,
)

T0 = "2026-07-29T20:00:00Z"


def entry(text, ts):
    """A sensor-data row: the payload is hex-encoded, as on the wire."""
    return {"value": text.encode("utf-8").hex(), "timestamp": ts}


def image_messages(name, planned, *, gid=None, drop=(), t_base=0, end=True):
    """One image's worth of backend rows, with `drop` chunk indexes missing."""
    rows = [entry(build_rc_start_message(
        name, T0, planned, quality=20, enc_attempts=1, complete=True,
        gid=gid), f"2026-07-29T20:{t_base:02d}:00Z")]
    for i in range(planned):
        if i in drop:
            continue
        rows.append(entry(f"{chunk_prefix(i, gid)}payload{i}\n",
                          f"2026-07-29T20:{t_base:02d}:{i % 60:02d}Z"))
    if end:
        rows.append(entry(build_rc_end_message(
            name, uart_duration_sec=float(planned), sent_buffers=planned,
            cpu_temp_text="51.2"), f"2026-07-29T20:{t_base + 1:02d}:00Z"))
    return rows


class TestCompleteImage(unittest.TestCase):
    def test_all_chunks_plus_start_and_end_is_complete(self):
        images, stats = cci.analyze(image_messages("a.jpg", 10, gid="0a1"))
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].complete())
        self.assertIsNone(images[0].first_gap())
        self.assertEqual(stats["chunks_seen"], 10)
        self.assertEqual(stats["orphan_chunks"], 0)

    def test_a_missing_chunk_makes_it_incomplete_and_locates_the_gap(self):
        images, _ = cci.analyze(
            image_messages("a.jpg", 10, gid="0a1", drop=(3,)))
        self.assertFalse(images[0].complete())
        self.assertEqual(images[0].first_gap(), 3)
        self.assertEqual(images[0].report()["usable_prefix_pct"], 30.0)

    def test_missing_end_is_not_complete_even_with_every_chunk(self):
        """END is the device's own statement that the send finished. Without
        it we cannot claim the image landed."""
        images, _ = cci.analyze(
            image_messages("a.jpg", 10, gid="0a1", end=False))
        self.assertEqual(len(images[0].chunks), 10)
        self.assertFalse(images[0].complete())

    def test_legacy_wire_without_gid_still_parses(self):
        images, _ = cci.analyze(image_messages("a.jpg", 8))
        self.assertTrue(images[0].complete())
        self.assertIsNone(images[0].gid)


class TestMultipleImages(unittest.TestCase):
    def rows(self):
        return (image_messages("a.jpg", 10, gid="0a1", t_base=0)
                + image_messages("b.jpg", 10, gid="0a2", drop=(7,), t_base=10)
                + image_messages("c.jpg", 10, gid="0a3", t_base=20))

    def test_counts_complete_images_not_percentages(self):
        images, stats = cci.analyze(self.rows())
        report = cci.summarize(images, stats, "SPOT-TEST", {})
        self.assertEqual(report["images_attempted"], 3)
        self.assertEqual(report["complete_images"], 2)
        self.assertEqual(report["complete_ratio"], "2/3")

    def test_chunk_percent_is_reported_but_is_not_the_headline(self):
        """The D8 trap made concrete: 29/30 chunks = 96.7 %, which sounds
        fine, while a third of the images are unusable."""
        images, stats = cci.analyze(self.rows())
        report = cci.summarize(images, stats, "SPOT-TEST", {})
        self.assertAlmostEqual(report["chunk_delivery_pct"], 96.67, places=1)
        self.assertEqual(report["complete_images"], 2)

    def test_gid_attribution_survives_an_out_of_order_straggler(self):
        """A chunk of image A arriving AFTER image B's START. With gids this
        must still land on A -- the Sofar path is not FIFO."""
        rows = (image_messages("a.jpg", 10, gid="0a1", drop=(9,), t_base=0)
                + image_messages("b.jpg", 10, gid="0a2", t_base=10))
        rows.append(entry(f"{chunk_prefix(9, '0a1')}late\n",
                          "2026-07-29T20:15:00Z"))
        images, stats = cci.analyze(rows)
        by_name = {i.filename: i for i in images}
        self.assertTrue(by_name["a.jpg"].complete())
        self.assertTrue(by_name["b.jpg"].complete())
        self.assertEqual(stats["orphan_chunks"], 0)
        report = cci.summarize(images, stats, "SPOT-TEST", {})
        self.assertIn("gid (exact)", report["attribution"])

    def test_legacy_attribution_is_flagged_as_a_fallback(self):
        images, stats = cci.analyze(image_messages("a.jpg", 5))
        report = cci.summarize(images, stats, "SPOT-TEST", {})
        self.assertIn("fallback", report["attribution"])

    def test_end_correlates_by_filename_not_by_arrival_order(self):
        """P4: START<->END correlate by filename. An END that lands after the
        next image's START must not close the wrong image."""
        rows = (image_messages("a.jpg", 5, gid="0a1", t_base=0, end=False)
                + image_messages("b.jpg", 5, gid="0a2", t_base=10))
        rows.append(entry(build_rc_end_message(
            "a.jpg", uart_duration_sec=5.0, sent_buffers=5,
            cpu_temp_text="50.0"), "2026-07-29T20:15:00Z"))
        images, _ = cci.analyze(rows)
        by_name = {i.filename: i for i in images}
        self.assertTrue(by_name["a.jpg"].ended)
        self.assertTrue(by_name["a.jpg"].complete())


class TestNoise(unittest.TestCase):
    def test_wake_status_acks_and_junk_are_ignored(self):
        rows = image_messages("a.jpg", 5, gid="0a1")
        rows.append(entry("<WS> a=cap, hn=bmcam003\n", "2026-07-29T20:00:30Z"))
        rows.append(entry('{"id":3001,"ok":1,"st":{"roi":2}}',
                          "2026-07-29T20:00:40Z"))
        rows.append({"value": "not-hex", "timestamp": "2026-07-29T20:00:50Z"})
        images, stats = cci.analyze(rows)
        self.assertTrue(images[0].complete())
        self.assertEqual(stats["other"], 3)

    def test_no_traffic_is_zero_complete_not_a_crash(self):
        images, stats = cci.analyze([])
        report = cci.summarize(images, stats, "SPOT-TEST", {})
        self.assertEqual(report["complete_images"], 0)
        self.assertEqual(report["images_attempted"], 0)
        self.assertIsNone(report["chunk_delivery_pct"])


if __name__ == "__main__":
    unittest.main()
