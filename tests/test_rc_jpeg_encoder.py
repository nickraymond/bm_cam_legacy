#!/usr/bin/env python3
# filename: test_rc_jpeg_encoder.py
# description: Sprint08 P2 — M2 byte checks vs the Sprint07 Pi run (p1_grid_20260724T165653Z).
"""
Sprint08 P2 — off-device tests for the M2 progressive-JPEG encoder.

The core check is BYTE EXACTNESS: rc_jpeg_encoder must reproduce, to the
sha256, the Sprint07 Pi run cells for the two repo-committed sources
(card + coral_primary). Expected values live in the committed fixture
tests/fixtures/sprint07_p1_expected.json (extracted from the
p1_grid_20260724T165653Z CSVs; provenance inside the file). Cross-version
validity: S07 P0 proved Mac Pillow 12.3.0 byte-identical to Pi 11.3.0.

Also pinned: the ceil message formula, encode determinism, in-memory ==
file-save bytes, and loud geometry/input validation.

Run (repo root; needs PIL, ~10 s for the two native prepares):
  python3 -m unittest tests.test_rc_jpeg_encoder -v
  # or: python3 tests/test_rc_jpeg_encoder.py
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

from rc_jpeg_encoder import (  # noqa: E402
    NATIVE_SIZE,
    encode_progressive,
    output_size_for_crop,
    prepare_source,
)

FIXTURE_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "sprint07_p1_expected.json")

# Ladder qualities the RC actually uses (q_max 15 -> q_min 9 by 2).
LADDER_QUALITIES = {9, 11, 13, 15}
CHUNK_B64_CHARS = 300


def _load_fixture():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class TestByteExactVsSprint07(unittest.TestCase):
    """Encode both committed sources at every ladder quality; all fields must
    match the Sprint07 Pi run exactly (sha256, bytes, base64_len, msgs)."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = _load_fixture()
        inputs = cls.fixture["provenance"]["inputs"]
        # One prepare per source (the expensive step), reused across qualities —
        # exactly how the RC ladder will use M2.
        cls.sources = {}
        cls.crops = {}
        for cell in cls.fixture["cells"]:
            cls.crops[cell["label"]] = tuple(cell["crop_xywh"])
        for label, rel_path in inputs.items():
            cls.sources[label] = prepare_source(
                os.path.join(REPO_ROOT, rel_path),
                cls.crops[label],
                output_width=1000,
            )

    def test_sources_are_1000x562(self):
        for label, img in self.sources.items():
            self.assertEqual(img.size, (1000, 562), msg=f"label={label}")

    def test_ladder_cells_byte_exact(self):
        checked = 0
        failures = []
        for cell in self.fixture["cells"]:
            if cell["quality"] not in LADDER_QUALITIES:
                continue
            result = encode_progressive(
                self.sources[cell["label"]], cell["quality"], CHUNK_B64_CHARS
            )
            for field in ("jpeg_bytes", "base64_len", "message_count", "jpeg_sha256"):
                if result[field] != cell[field]:
                    failures.append(
                        f"{cell['label']} q{cell['quality']}: {field} "
                        f"got {result[field]} expected {cell[field]}"
                    )
            checked += 1
        self.assertEqual(checked, 8, msg="expected 2 sources x 4 ladder qualities")
        self.assertEqual(failures, [], msg="byte mismatches vs Sprint07 Pi run:\n" + "\n".join(failures))

    def test_sprint07_headline_message_counts(self):
        # The §4 numbers the whole budget model rests on: card q13=75, q15=81.
        card = self.sources["card"]
        self.assertEqual(encode_progressive(card, 13, CHUNK_B64_CHARS)["message_count"], 75)
        self.assertEqual(encode_progressive(card, 15, CHUNK_B64_CHARS)["message_count"], 81)


class TestEncodeBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = _load_fixture()
        rel = fixture["provenance"]["inputs"]["coral_primary"]
        cls.source = prepare_source(
            os.path.join(REPO_ROOT, rel), (1504, 846, 1600, 900), output_width=1000
        )

    def test_deterministic(self):
        a = encode_progressive(self.source, 13, CHUNK_B64_CHARS)
        b = encode_progressive(self.source, 13, CHUNK_B64_CHARS)
        self.assertEqual(a["jpeg_sha256"], b["jpeg_sha256"])
        self.assertEqual(a["jpeg_data"], b["jpeg_data"])

    def test_in_memory_equals_file_save(self):
        # Guard vs the reference tool, which saves to a file: same Pillow call,
        # same bytes, whether in memory or on disk.
        mem = encode_progressive(self.source, 13, CHUNK_B64_CHARS)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            path = f.name
        try:
            self.source.save(path, format="JPEG", quality=13, progressive=True, optimize=True)
            with open(path, "rb") as f:
                disk = f.read()
            self.assertEqual(mem["jpeg_data"], disk)
        finally:
            os.unlink(path)

    def test_message_count_is_ceil(self):
        result = encode_progressive(self.source, 13, CHUNK_B64_CHARS)
        b64 = result["base64_len"]
        self.assertEqual(result["message_count"], -(-b64 // CHUNK_B64_CHARS))
        # Chunk size of 1 char -> one message per base64 char (formula edge).
        self.assertEqual(
            encode_progressive(self.source, 13, 1)["message_count"], b64
        )

    def test_invalid_quality_and_chunk_rejected(self):
        for bad_q in (0, 96, -1):
            with self.assertRaises(ValueError):
                encode_progressive(self.source, bad_q, CHUNK_B64_CHARS)
        with self.assertRaises(ValueError):
            encode_progressive(self.source, 13, 0)


class TestPrepareSourceValidation(unittest.TestCase):
    def test_output_size_rounding(self):
        self.assertEqual(output_size_for_crop(1600, 900, 1000), (1000, 562))
        self.assertEqual(output_size_for_crop(3072, 1728, 2688), (2688, 1512))

    def test_output_width_bounds(self):
        with self.assertRaises(ValueError):
            output_size_for_crop(1600, 900, 1601)  # no upsampling
        with self.assertRaises(ValueError):
            output_size_for_crop(1600, 900, 0)

    def test_out_of_bounds_crop_rejected(self):
        fixture = _load_fixture()
        native = os.path.join(REPO_ROOT, fixture["provenance"]["inputs"]["card"])
        with self.assertRaises(ValueError):
            prepare_source(native, (4000, 846, 1600, 900), 1000)
        with self.assertRaises(ValueError):
            prepare_source(native, (-1, 0, 1600, 900), 1000)

    def test_wrong_native_size_rejected(self):
        # A non-native input must fail loud before any encode is attempted.
        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            path = f.name
        try:
            Image.new("RGB", (1280, 720)).save(path, format="JPEG")
            with self.assertRaises(ValueError):
                prepare_source(path, (0, 0, 1600, 900), 1000)
        finally:
            os.unlink(path)

    def test_native_size_constant(self):
        self.assertEqual(NATIVE_SIZE, (4608, 2592))


if __name__ == "__main__":
    unittest.main(verbosity=2)
