#!/usr/bin/env python3
# filename: test_w9_chunk_total.py
# description: Sprint26 S4w (W9) — keyed chunks carry the media total `<I{key}.{i}/{M}>`.
"""
Sprint26 S4w, W9 (DESIGN_supervisor.md §8.2, §10 O11; PLAN_S4w.md). Pins:

  backend accepts   every chunk of a keyed still (complete + bounded a=inc) and a keyed
                    video (incl. the keyframe repeat) matches the nvd `/M` parser regex,
                    and key / i / M parse back with M == the START `length`
  gate              chunk_total off (the default) or no key -> no `/`; the module flag
                    defaults to False (only the v2 supervisor sets it)
  heals             a record written by the REAL prepare_keyed_send with chunk_total heals
                    byte-identical to the bytes the REAL transmit sent (`/M` included); a
                    record without chunk_total (pre-W9) heals without `/M`
  budget            worst-case line 402 B at 384 chars (Spotter fast path ~430 B)

The regex is copied verbatim from nereus-vision-dev `origin/staging` a2721d3,
backend/app/services/bm_image_parser.py:155 (RE_CHUNK_MARKER, the W9 parser, nvd PR #65).
If the backend regex changes, re-copy it here.

Run (repo root):  python3 -m pytest tests/test_w9_chunk_total.py -q
"""

import base64
import contextlib
import io
import os
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")
    _stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = _stub

import rc_heal  # noqa: E402
import rc_media_key as mk  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from rc_transmit import transmit_progressive_image, transmit_video_clip  # noqa: E402

# nvd origin/staging backend/app/services/bm_image_parser.py:155 (verbatim).
# Groups: 1 key, 2 keyed index, 3 total, 4 legacy index.
RE_CHUNK_MARKER = re.compile(r"<I(?:([0-9a-z]{6})\.(\d+)(?:/(\d+))?|(\d+))>")
RE_START_LEN = re.compile(rb"length: (\d+), key=([0-9a-z]{6})")

VIDEO = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264", "payload.h264")
KEY = "0dhnso"
T_KEY = datetime(2026, 9, 20, 6, 10, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def send_still(jpeg, key=KEY, chunk_total=True, fits=True, budget_s=600):
    clk, wire = Clock(), []
    with contextlib.redirect_stdout(io.StringIO()):
        res = transmit_progressive_image(
            wire.append, CycleBudget(budget_s, 1.0, clock=clk), jpeg_data=jpeg,
            compressed_file_name="2026-09-20T06:10:00Z_image_compressed.jpg", quality=60,
            enc_attempts=1, fits=fits, selector_reason=None if fits else "budget",
            chunk_b64_chars=384, delay_seconds=1.0, current_timestamp="2026-09-20T06:10:04Z",
            sleep_fn=clk.sleep, clock=clk, media_key=key, chunk_total=chunk_total)
    return wire, res


def send_video(payload, key=KEY, chunk_total=True):
    clk, wire = Clock(), []
    with contextlib.redirect_stdout(io.StringIO()):
        res = transmit_video_clip(
            wire.append, CycleBudget(900, 1.0, clock=clk), payload=payload,
            file_name="2026-09-20T06-10-00Z_video_5s.h264", fps=10, dur=5.0, res="480x270",
            crop="na", crf=40, keyframe_chunks=8, chunk_b64_chars=384, delay_seconds=1.0,
            current_timestamp="2026-09-20T06:10:04Z", sleep_fn=clk.sleep, clock=clk,
            media_key=key, chunk_total=chunk_total)
    return wire, res


def chunk_lines(wire):
    return [x for x in wire if x.startswith(b"<I")]


class TestBackendAccepts(unittest.TestCase):
    def assert_all_accepted(self, wire):
        start = [x for x in wire if x.startswith(b"<START")]
        self.assertEqual(len(start), 1)
        length, key = RE_START_LEN.search(start[0]).groups()
        chunks = chunk_lines(wire)
        self.assertTrue(chunks)
        for line in chunks:
            m = RE_CHUNK_MARKER.match(line.decode("ascii"))
            self.assertIsNotNone(m, line[:40])
            self.assertEqual(m.group(1), key.decode())
            self.assertEqual(int(m.group(3)), int(length), "M == START length")
            self.assertLess(int(m.group(2)), int(m.group(3)))
            self.assertIsNone(m.group(4))
            # the base64 body starts right after the marker, as the parser slices it
            base64.b64decode(line[m.end():].rstrip(b"\n"))
        return int(length), chunks

    def test_still_complete(self):
        wire, res = send_still(b"\xff\xd8" + os.urandom(20000))
        length, chunks = self.assert_all_accepted(wire)
        self.assertEqual((length, len(chunks)), (res["planned"], res["planned"]))

    def test_still_bounded_partial_uses_planned_total(self):
        # 40 s budget at 1 msg/s cannot hold ~70 chunks: a=inc + bounded send.
        wire, res = send_still(b"\xff\xd8" + os.urandom(20000), fits=False, budget_s=40)
        self.assertTrue(res["incomplete_emitted"])
        self.assertLess(res["sent"], res["planned"])
        length, chunks = self.assert_all_accepted(wire)
        self.assertEqual(length, res["planned"], "M is the planned total, not the bounded count")
        self.assertEqual(len(chunks), res["sent"])

    def test_video_with_keyframe_repeat(self):
        wire, res = send_video(read(VIDEO))
        length, chunks = self.assert_all_accepted(wire)
        self.assertEqual(length, 126)
        self.assertEqual(len(chunks), 126 + 8, "the repeat carries its original i/M, never counted")
        self.assertEqual(chunks[126:], chunks[:8], "keyframe repeat is byte-identical")

    def test_worst_case_line_402(self):
        wire, _ = send_video(read(VIDEO))
        self.assertEqual(max(len(x) for x in chunk_lines(wire)), len(b"<I0dhnso.125/126>") + 384 + 1)
        self.assertLessEqual(len(mk.chunk_prefix(999, KEY, 999)) + 384 + 1, 402)


class TestGate(unittest.TestCase):
    def test_module_default_off(self):
        self.assertFalse(mk.CHUNK_TOTAL)

    def test_off_is_rev5(self):
        for wire in (send_still(b"\xff\xd8" + b"x" * 3000, chunk_total=False)[0],
                     send_video(read(VIDEO), chunk_total=False)[0]):
            self.assertTrue(all(b"/" not in x.split(b">", 1)[0] for x in chunk_lines(wire)))

    def test_no_key_never_gets_total(self):
        self.assertEqual(mk.chunk_prefix(7, None, 9), "<I7>")
        for wire in (send_still(b"\xff\xd8" + b"x" * 3000, key=None)[0],
                     send_video(read(VIDEO), key=None)[0]):
            for line in chunk_lines(wire):
                self.assertRegex(line.decode("ascii"), r"^<I\d+>")

    def test_prefix_forms(self):
        self.assertEqual(mk.chunk_prefix(7, KEY), f"<I{KEY}.7>")
        self.assertEqual(mk.chunk_prefix(7, KEY, 126), f"<I{KEY}.7/126>")


class TestHealByteIdentical(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.cfg = {"enabled": True, "retain_days": 3.0, "source": "test",
                    "sent_dir": os.path.join(self.d.name, "sent"),
                    "state_path": os.path.join(self.d.name, "last.txt")}
        self.gate = {"source_time": "spotter", "utc_time": T_KEY.isoformat()}

    def prepare(self, payload, chunk_total, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return mk.prepare_keyed_send(
                {"media_key_cfg": self.cfg}, gate_info=self.gate, daemon=None,
                chunk_b64_chars=384, payload=payload, chunk_total=chunk_total, **kw)

    def heal(self, key, ns):
        rec = mk.find_sent_record(self.cfg["sent_dir"], key)
        return rec, [line for _n, line in rc_heal.heal_lines(rec, ns)]

    def test_video_record_heals_with_total(self):
        payload = read(VIDEO)
        key = self.prepare(payload, True, stem="clip", fmt="h264", filename="clip.h264")
        wire, _ = send_video(payload, key=key, chunk_total=True)
        rec, healed = self.heal(key, [0, 5, 17, 125])
        self.assertIs(rec["chunk_total"], True)
        sent = chunk_lines(wire)
        self.assertEqual(healed, [sent[0], sent[5], sent[17], sent[125]])
        self.assertTrue(healed[1].startswith(f"<I{key}.5/126>".encode()))

    def test_still_record_heals_with_total(self):
        d = os.path.join(self.d.name, "img")
        os.makedirs(d)
        jpeg = b"\xff\xd8" + os.urandom(9000)
        path = os.path.join(d, "img.jpg")
        with open(path, "wb") as fh:
            fh.write(jpeg)
        key = self.prepare(jpeg, True, stem="img", fmt="pjpg", filename="img.jpg", payload_path=path)
        wire, res = send_still(jpeg, key=key, chunk_total=True)
        _rec, healed = self.heal(key, [1, res["planned"] - 1])
        sent = chunk_lines(wire)
        self.assertEqual(healed, [sent[1], sent[-1]])

    def test_pre_w9_record_heals_without_total(self):
        payload = read(VIDEO)
        key = self.prepare(payload, False, stem="clip", fmt="h264", filename="clip.h264")
        wire, _ = send_video(payload, key=key, chunk_total=False)
        rec, healed = self.heal(key, [3])
        self.assertNotIn("chunk_total", rec, "pre-W9 sidecar bytes unchanged")
        self.assertEqual(healed, [chunk_lines(wire)[3]])
        self.assertTrue(healed[0].startswith(f"<I{key}.3>".encode()))


if __name__ == "__main__":
    unittest.main()
