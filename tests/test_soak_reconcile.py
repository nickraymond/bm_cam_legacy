#!/usr/bin/env python3
# filename: test_soak_reconcile.py
# description: Sprint10 soak — tests for the backend reconciler grouping logic.
"""
Sprint10 — tests for tools/soak_reconcile.py.

Pins: dual chunk-format parsing (legacy `<I{i}>` and gid `<Igid.{i}>`),
gid-exact attribution under interleaved/straggler arrival, declared
totals (START length / END sent_buffers) driving missing-tail
detection, and legacy arrival-order fallback staying intact.

Run (repo root):
  python3 -m unittest tests.test_soak_reconcile -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import soak_reconcile as sr  # noqa: E402


def row(ts, text):
    return {"timestamp": ts, "value": text.encode().hex(),
            "bristlemouth_node_id": "0x53171fa3d81a8e6f"}


def start(ts, fn, length, gid=None):
    g = f", gid: {gid}" if gid else ""
    return row(ts, f"<START IMG> filename: {fn}, timestamp: {ts}, "
                   f"length: {length}{g}, fmt=pjpg, q=20, att=1, cmp=1")


def end(ts, fn, sent):
    return row(ts, f"<END IMG> filename: {fn}, uart_duration_sec: 60.0, "
                   f"sent_buffers: {sent}")


class TestClassify(unittest.TestCase):
    def test_legacy_chunk(self):
        self.assertEqual(sr.classify("<I17>abcd"), ("chunk", (None, 17)))

    def test_gid_chunk(self):
        self.assertEqual(sr.classify("<Iab2.17>abcd"), ("chunk", ("ab2", 17)))

    def test_start_fields(self):
        f = sr._start_fields("<START IMG> filename: x.jpg, timestamp: T, "
                             "length: 180, gid: 0a1, fmt=pjpg")
        self.assertEqual(f["length"], 180)
        self.assertEqual(f["gid"], "0a1")
        self.assertEqual(f["fn"], "x.jpg")


class TestLegacyGrouping(unittest.TestCase):
    def test_complete_image_with_declared_total(self):
        rows = [start("T1", "a.jpg", 3)] + \
            [row(f"T{i+2}", f"<I{i}>data") for i in range(3)] + \
            [end("T9", "a.jpg", 3)]
        out = sr.reconcile(rows)
        img = out["images"][0]
        self.assertTrue(img["complete"])
        self.assertEqual(img["missing"], [])

    def test_missing_tail_detected_via_sent_buffers(self):
        # Device sent 5 (END says so); backend only has 0..2 -> tail lost.
        rows = [start("T1", "a.jpg", 5)] + \
            [row(f"T{i+2}", f"<I{i}>data") for i in range(3)] + \
            [end("T9", "a.jpg", 5)]
        out = sr.reconcile(rows)
        img = out["images"][0]
        self.assertFalse(img["complete"])
        self.assertEqual(img["missing"], [3, 4])


class TestGidGrouping(unittest.TestCase):
    def test_interleaved_cycles_attribute_exactly(self):
        # Cycle A (gid aaa) loses chunk 1; its straggler arrives DURING
        # cycle B (gid bbb). Legacy grouping would corrupt both counts.
        rows = [
            start("T1", "a.jpg", 3, gid="aaa"),
            row("T2", "<Iaaa.0>x"), row("T3", "<Iaaa.2>x"),
            end("T4", "a.jpg", 3),
            start("T5", "b.jpg", 2, gid="bbb"),
            row("T6", "<Ibbb.0>x"),
            row("T7", "<Iaaa.1>x"),   # straggler from A
            row("T8", "<Ibbb.1>x"),
            end("T9", "b.jpg", 2),
        ]
        out = sr.reconcile(rows)
        a = next(i for i in out["images"] if i["gid"] == "aaa")
        b = next(i for i in out["images"] if i["gid"] == "bbb")
        self.assertTrue(a["complete"], a)   # straggler recovered A
        self.assertTrue(b["complete"], b)
        self.assertEqual(a["filename"], "a.jpg")

    def test_straggler_without_start_gets_own_group(self):
        out = sr.reconcile([row("T1", "<Iccc.4>x")])
        img = out["images"][0]
        self.assertEqual(img["gid"], "ccc")
        self.assertFalse(img["complete"])


if __name__ == "__main__":
    unittest.main()
