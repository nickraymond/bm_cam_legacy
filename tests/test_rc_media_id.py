#!/usr/bin/env python3
# filename: test_rc_media_id.py
# description: Sprint10 — tests for the media gid module + wire integration.
"""
Sprint10 — tests for BM_Devel_Pi/rc_media_id.py and the gid's ride
through the START builder and chunk framing.

Pins: base36 encoding/wrap, counter persistence + corrupt recovery,
chunk prefix formats, island config loader, gid field in START (never
dropped, absent by default), and the transmit path emitting gid chunks
only when media_gid is passed — the legacy wire stays byte-identical.

Run (repo root):
  python3 -m unittest tests.test_rc_media_id -v
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_media_id as mid  # noqa: E402
from rc_uplink_messages import build_rc_start_message  # noqa: E402


class TestGidEncoding(unittest.TestCase):
    def test_zero_and_padding(self):
        self.assertEqual(mid.encode_gid(0), "000")
        self.assertEqual(mid.encode_gid(35), "00z")
        self.assertEqual(mid.encode_gid(36), "010")

    def test_wraps_at_modulus(self):
        self.assertEqual(mid.encode_gid(mid.GID_MOD), "000")
        self.assertEqual(mid.encode_gid(mid.GID_MOD + 1), "001")


class TestCounter(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "gid.txt")

    def test_fresh_starts_at_zero_then_increments(self):
        self.assertEqual(mid.next_gid(self.path), "000")
        self.assertEqual(mid.next_gid(self.path), "001")
        self.assertEqual(mid.next_gid(self.path), "002")

    def test_corrupt_state_restarts_loudly_at_zero(self):
        with open(self.path, "w") as f:
            f.write("garbage")
        self.assertEqual(mid.next_gid(self.path), "000")

    def test_wrap(self):
        with open(self.path, "w") as f:
            f.write(str(mid.GID_MOD - 1))
        self.assertEqual(mid.next_gid(self.path), "000")


class TestChunkPrefix(unittest.TestCase):
    def test_legacy_format_unchanged(self):
        self.assertEqual(mid.chunk_prefix(7), "<I7>")
        self.assertEqual(mid.chunk_prefix(173), "<I173>")

    def test_gid_format(self):
        self.assertEqual(mid.chunk_prefix(7, "ab2"), "<Iab2.7>")


class TestConfigLoader(unittest.TestCase):
    def _cfg(self, text):
        p = os.path.join(tempfile.mkdtemp(), "camera_schedule.yaml")
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_absent_island_disabled(self):
        p = self._cfg("capture_mode: progressive_jpeg\n")
        self.assertFalse(mid.load_media_gid_config(p)["enabled"])

    def test_enabled_island(self):
        p = self._cfg("media_gid:\n  enabled: true\n")
        self.assertTrue(mid.load_media_gid_config(p)["enabled"])

    def test_disabled_island_and_comments(self):
        p = self._cfg("media_gid:\n  enabled: false  # off\n")
        self.assertFalse(mid.load_media_gid_config(p)["enabled"])

    def test_missing_file_disabled(self):
        self.assertFalse(mid.load_media_gid_config("/nonexistent")["enabled"])


class TestStartMessageGid(unittest.TestCase):
    def _start(self, **kw):
        return build_rc_start_message(
            "img.jpg", "2026-07-27T20:00:00Z", 180,
            quality=20, enc_attempts=2, complete=True, **kw)

    def test_default_has_no_gid_byte_identical(self):
        self.assertNotIn("gid:", self._start())

    def test_gid_rides_as_base_field(self):
        msg = self._start(gid="ab2")
        self.assertIn("gid: ab2", msg)
        self.assertLessEqual(len(msg.encode("ascii")), 285)

    def test_gid_survives_metadata_drop_pressure(self):
        meta = {"software_sha": "a" * 12, "hostname": "h" * 24,
                "timezone": "America/Los_Angeles", "image_res_key": "1000x562",
                "window_start": "00:01", "window_end": "23:59"}
        msg = self._start(gid="zz9", start_metadata=meta)
        self.assertIn("gid: zz9", msg)
        self.assertLessEqual(len(msg.encode("ascii")), 285)


class TestTransmitGidWire(unittest.TestCase):
    """transmit_progressive_image emits gid chunks only when asked."""

    def _run(self, media_gid=None):
        from rc_time_budget import CycleBudget
        from rc_transmit import transmit_progressive_image
        sent = []
        fake = {"t": 0.0}

        def clock():
            return fake["t"]

        def sleep(s):
            fake["t"] += s

        budget = CycleBudget(10000, 1.0, clock=clock)
        transmit_progressive_image(
            lambda b: sent.append(b), budget,
            jpeg_data=b"x" * 600, compressed_file_name="img.jpg",
            quality=20, enc_attempts=1, fits=True,
            chunk_b64_chars=384, delay_seconds=1.0,
            current_timestamp="2026-07-27T20:00:00Z",
            sleep_fn=sleep, clock=clock, media_gid=media_gid)
        return [s.decode("ascii", "replace") for s in sent]

    def test_legacy_wire_byte_identical(self):
        msgs = self._run(media_gid=None)
        chunkmsgs = [m for m in msgs if m.startswith("<I")]
        self.assertTrue(chunkmsgs and all(
            m.startswith(f"<I{i}>") for i, m in enumerate(chunkmsgs)))
        self.assertNotIn("gid:", msgs[0])

    def test_gid_wire(self):
        msgs = self._run(media_gid="ab2")
        self.assertIn("gid: ab2", msgs[0])  # START
        chunkmsgs = [m for m in msgs if m.startswith("<I")]
        self.assertTrue(chunkmsgs and all(
            m.startswith(f"<Iab2.{i}>") for i, m in enumerate(chunkmsgs)))


if __name__ == "__main__":
    unittest.main()
