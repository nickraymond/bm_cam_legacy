#!/usr/bin/env python3
# filename: test_bm_media_golden_vectors.py
# description: Sprint22 Phase 0 — pins the committed H.264 golden vectors to the production framing.
"""
Sprint22 Phase 0 — golden vectors for docs/bm_media_wire_contract.md.

Pins: the committed files match their manifest; re-framing payload.h264
with the PRODUCTION chunk framing + message builders reproduces
wire_complete.txt byte-for-byte; every damaged variant reassembles (backend
receiver model) to exactly what the manifest promises; START/END obey the
contract's budget and key rules; the SEI strip removes only the x264
user-data SEI. No ffmpeg needed — the vectors are committed bytes.

Run (repo root):
  python3 -m unittest tests.test_bm_media_golden_vectors -v
"""

import hashlib
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import bm_video_tx_loopback as tool  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")


def _read(name):
    with open(os.path.join(VEC, name), "rb") as fh:
        return fh.read()


class TestGoldenVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.man = json.loads(_read("manifest.json"))
        cls.payload = _read("payload.h264")

    def test_committed_files_match_manifest(self):
        self.assertEqual(hashlib.sha256(self.payload).hexdigest(),
                         self.man["payload"]["sha256"])
        for name, v in self.man["variants"].items():
            self.assertEqual(hashlib.sha256(_read(v["wire_file"])).hexdigest(),
                             v["wire_sha256"], name)

    def test_payload_is_sei_free_annexb_with_header_in_chunk0(self):
        self.assertTrue(self.payload.startswith(b"\x00\x00\x00\x01"))
        again, removed = tool.strip_x264_sei(self.payload)
        self.assertEqual((again, removed), (self.payload, 0))
        self.assertLessEqual(tool.parameter_sets_end(self.payload),
                             tool.RAW_BYTES_PER_MSG)

    def test_complete_wire_regenerates_from_production_framing(self):
        chunks = tool.frame_messages(self.payload)
        enc = self.man["encode"]
        start = tool.build_video_start_message(
            tool.GOLDEN_FILENAME, tool.GOLDEN_TIMESTAMP, len(chunks),
            fps=enc["fps"], dur=enc["dur_s"], res=enc["size"], crf=enc["crf"],
            start_metadata=tool.GOLDEN_START_METADATA).encode("ascii")
        end = tool.build_video_end_message(
            tool.GOLDEN_FILENAME, uart_duration_sec=float(len(chunks) + 3),
            sent_buffers=len(chunks),
            cpu_temp_text=tool.GOLDEN_CPU_TEMP).encode("ascii")
        wire = b"".join([start, *chunks, chunks[0], end])
        self.assertEqual(wire, _read("wire_complete.txt"))

    def test_variants_reassemble_as_promised(self):
        for name, v in self.man["variants"].items():
            lines = _read(v["wire_file"]).splitlines(keepends=True)
            data, got = tool.backend_style_reassemble(lines)
            self.assertEqual(len(got), v["received_chunks"], name)
            self.assertEqual([i for i in range(v["expected_chunks"])
                              if i not in got], v["missing_chunks"], name)
            self.assertEqual(hashlib.sha256(data).hexdigest(),
                             v["recovered_sha256"], name)

    def test_chunk0_repeat_rescues_a_lost_chunk0(self):
        v = self.man["variants"]["chunk0_lost"]
        lines = _read(v["wire_file"]).splitlines(keepends=True)
        self.assertFalse(lines[1].startswith(b"<I0>"))   # first <I0> is gone
        data, _ = tool.backend_style_reassemble(lines)
        self.assertEqual(data, self.payload)

    def test_start_and_end_obey_the_contract(self):
        start, end = self.man["start_message"], self.man["end_message"]
        self.assertLessEqual(len(start.encode("ascii")), 285)
        self.assertLessEqual(len(end.encode("ascii")), 295)
        head, tail = start.split("length: ", 1)
        self.assertTrue(head.startswith("<START IMG> filename: "))
        new_keys = [p.split("=")[0].strip() for p in tail.split(", ")[1:]]
        self.assertEqual(new_keys[:7],
                         ["fmt", "fps", "dur", "res", "crop", "crf", "cmp"])
        self.assertIn("crop=na,", start)      # synthetic clip: no camera
        for key in new_keys:
            for bad in tool.FORBIDDEN_KEY_SUBSTRINGS:
                self.assertNotIn(bad, key)
        self.assertIn("fmt=h264", start)
        self.assertIn(", fmt: h264, ", end)
        self.assertIn(", fmt: pjpg, ",
                      _read("wire_fmt_disagree.txt").decode("ascii"))


class TestSeiStrip(unittest.TestCase):
    def test_removes_only_user_data_sei(self):
        sps = b"\x00\x00\x00\x01\x67\x64\x00"
        x264_sei = b"\x00\x00\x01\x06\x05\x10version"
        other_sei = b"\x00\x00\x01\x06\x06\x01\x80"      # recovery point
        idr = b"\x00\x00\x01\x65\x88\x84"
        out, removed = tool.strip_x264_sei(x264_sei + sps + other_sei + idr)
        self.assertEqual(out, sps + other_sei + idr)
        self.assertEqual(removed, 1)

    def test_start_takes_exactly_one_rate_control_key(self):
        kw = dict(fps=10, dur=5.0, res="480x270", crop="4608x2592+0+0")
        args = ("a.h264", "2026-09-20T06:10:04Z", 88)
        self.assertIn(", crop=4608x2592+0+0, br=40, cmp=1",
                      tool.build_video_start_message(*args, br=40, **kw))
        for bad in ({}, {"crf": 40, "br": 40}):
            with self.assertRaises(ValueError):
                tool.build_video_start_message(*args, **bad, **kw)

    def test_start_refuses_a_forbidden_or_oversize_message(self):
        with self.assertRaises(ValueError):
            tool.build_video_start_message(
                "x" * 96 + ".h264", "2026-09-20T06:10:04Z", 1, fps=10,
                dur=5.0, res="480x270", crf=40, max_payload_bytes=100)


if __name__ == "__main__":
    unittest.main()
