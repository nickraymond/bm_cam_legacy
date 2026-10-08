#!/usr/bin/env python3
# filename: test_s28_raw_jxl.py
# description: Sprint28 S1 — unit tests for the nrjxl still path (reader, curve, container, rung walk, guards, config, capture fallback).
"""
Sprint28 SPEC r4 §7.1 desk tests (bm side). The wire level (START fmt=nrjxl and every
pjpg rfb fallback, keyed chunks, a RAW problem never costing the JPEG) is pinned by the
golden vectors `vectors_v9/v9_nrjxl*` (tests/test_golden_vectors.py); this file covers
the pieces underneath.

Fixtures: tests/fixtures/s28/ (make_fixtures.py): mini.dng (real IMX708 data, written by
tifffile) + its tifffile-read oracle, 3 production blobs built from the study DNGs.
Optional (skipped when absent): tifffile (DNG variants), djxl (decode the fixture blobs).
The rig's study decoder + the study's colour metric run in tools/s28_mac_e2e_check.py
(rig venv; SPEC §7.1 "Mac end-to-end").

Run: .venv-dev/bin/python -m pytest -q tests/test_s28_raw_jxl.py
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))

import numpy as np  # noqa: E402

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import config_validate as V  # noqa: E402
import rc_raw_jxl as X  # noqa: E402
import rc_still_storage  # noqa: E402
import rc_uplink_messages as U  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "s28")
MINI = os.path.join(FIX, "mini.dng")

try:
    import tifffile
except ImportError:                     # optional: only the DNG-variant tests need it
    tifffile = None


def _sha_u16(arr):
    return hashlib.sha256(np.ascontiguousarray(arr).astype("<u2").tobytes()).hexdigest()


class Budget:
    """CycleBudget stand-in with a settable clock."""

    def __init__(self, remaining=480.0, pace=1.3):
        self.left, self.seconds_per_message = remaining, pace

    def remaining_s(self):
        return self.left

    def elapsed_s(self):
        return 0.0

    def messages_fit(self, n):
        return self.left >= n * self.seconds_per_message


# ---------------------------------------------------------------------------
# DNG crop reader (SPEC §3.2)
# ---------------------------------------------------------------------------

class Reader(unittest.TestCase):
    def test_crops_equal_the_tifffile_oracle(self):
        with open(os.path.join(FIX, "mini.json")) as fh:
            oracle = json.load(fh)
        for name, w in oracle["windows"].items():
            c = X.read_dng_crop(MINI, w["crop"])
            self.assertEqual(_sha_u16(c["mosaic"]), w["sha256_u16le"], name)
            self.assertEqual(c["mosaic"].shape, (w["crop"][3], w["crop"][2]))
            self.assertEqual((c["cfa"], c["black"], c["white"], c["bits"]), ("BGGR", 64, 1023, 16))
            self.assertEqual((c["native_w"], c["native_h"]), (160, 96))

    def test_refuses_odd_or_outside_crops(self):
        for crop in ([1, 0, 64, 40], [0, 1, 64, 40], [0, 0, 63, 40], [0, 0, 64, 41],
                     [100, 0, 64, 40], [0, 60, 64, 40], [0, 0, 0, 40]):
            with self.assertRaises(X.DngError, msg=str(crop)):
                X.read_dng_crop(MINI, crop)

    def test_refuses_a_file_that_is_not_a_dng(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "x.dng")
            with open(bad, "wb") as fh:
                fh.write(b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00")
            with self.assertRaisesRegex(X.DngError, "no full-resolution CFA"):
                X.read_dng_crop(bad, [0, 0, 2, 2])
            with open(bad, "wb") as fh:
                fh.write(b"JFIF....")
            with self.assertRaisesRegex(X.DngError, "not a TIFF"):
                X.read_dng_crop(bad, [0, 0, 2, 2])

    def test_refuses_a_truncated_dng(self):
        with tempfile.TemporaryDirectory() as d:
            short = os.path.join(d, "short.dng")
            with open(MINI, "rb") as src, open(short, "wb") as dst:
                dst.write(src.read()[:20000])
            with self.assertRaises(X.DngError):
                X.read_dng_crop(short, [0, 0, 160, 96])

    @unittest.skipIf(tifffile is None, "tifffile not installed")
    def test_refuses_the_layouts_the_rig_reader_refuses(self):
        mosaic = tifffile.TiffFile(MINI).pages[0].pages[0].asarray()
        base = [(33421, 3, 2, (2, 2), True), (33422, 1, 4, b"\x02\x01\x01\x00", True),
                (50713, 3, 2, (2, 2), True), (50714, 5, 4, (64, 1, 64, 1, 64, 1, 64, 1), True),
                (50717, 4, 1, 1023, True)]

        def dng(path, tags, data=mosaic, **kw):
            with tifffile.TiffWriter(path) as tw:
                tw.write(data, photometric=32803, metadata=None, extratags=tags, **kw)

        cases = {
            "linearization": (base + [(50712, 3, 4, (0, 1, 2, 3), True)], {}, "Linearization"),
            "cfa3x3": ([(33421, 3, 2, (3, 3), True)] + base[1:], {}, "not 2x2"),
            "cyan": ([base[0], (33422, 1, 4, b"\x03\x01\x01\x00", True)] + base[2:], {},
                     "not an RGB Bayer"),
            "per_position_black": (base[:3] + [(50714, 5, 4, (64, 1, 65, 1, 64, 1, 64, 1), True),
                                               base[4]], {}, "per-position"),
            "odd_active_area": (base + [(50829, 3, 4, (1, 0, 96, 160), True)], {},
                                "odd ActiveArea"),
            "deflate": (base, {"compression": "zlib"}, "compressed"),
            "tiled": (base, {"tile": (32, 32)}, "tiled"),
        }
        with tempfile.TemporaryDirectory() as d:
            for name, (tags, kw, why) in cases.items():
                path = os.path.join(d, name + ".dng")
                dng(path, tags, **kw)
                with self.assertRaisesRegex(X.DngError, why, msg=name):
                    X.read_dng_crop(path, [0, 0, 64, 40])
            # an EVEN ActiveArea offsets the crop (native px = active area)
            path = os.path.join(d, "even_area.dng")
            dng(path, base + [(50829, 3, 4, (2, 4, 96, 160), True)])
            c = X.read_dng_crop(path, [0, 0, 64, 40])
            self.assertTrue(np.array_equal(c["mosaic"], mosaic[2:42, 4:68]))
            self.assertEqual((c["native_w"], c["native_h"]), (156, 94))
            # big-endian files read the same values
            path = os.path.join(d, "be.dng")
            with tifffile.TiffWriter(path, byteorder=">") as tw:
                tw.write(mosaic, photometric=32803, metadata=None, extratags=base)
            self.assertTrue(np.array_equal(X.read_dng_crop(path, [0, 0, 160, 96])["mosaic"],
                                           mosaic))
            # several strips
            path = os.path.join(d, "strips.dng")
            dng(path, base, rowsperstrip=7)
            self.assertTrue(np.array_equal(X.read_dng_crop(path, [8, 10, 64, 40])["mosaic"],
                                           mosaic[10:50, 8:72]))


# ---------------------------------------------------------------------------
# planes + curve (SPEC §3.4)
# ---------------------------------------------------------------------------

def _study_sqrt_lut(black, white, b=12):
    """The rig's compression_study/common.sqrt_lut (pedestal 0), re-typed here as the
    reference (rig origin/main 372d6f2)."""
    raw = np.arange(white + 1, dtype=np.float64)
    v = np.clip(raw - black, 0, white - black)
    return np.floor(np.sqrt(v) * ((2 ** b - 1) / np.sqrt(white - black)) + 0.5).astype(np.uint16)


class Curve(unittest.TestCase):
    def test_lut_equals_the_study_lut(self):
        for black, white in ((64, 1023), (0, 1023), (256, 4095), (64, 4095), (4096, 65535)):
            self.assertTrue(np.array_equal(X.sqrt_lut(black, white), _study_sqrt_lut(black, white)))
        lut = X.sqrt_lut(64, 1023)
        self.assertEqual((int(lut[0]), int(lut[64]), int(lut[65]), int(lut[1023])), (0, 0, 132, 4095))
        # pinned bytes (computed against the rig's own function on the Mac, 2026-10-02)
        self.assertEqual(hashlib.sha256(lut.astype("<u2").tobytes()).hexdigest()[:16],
                         hashlib.sha256(_study_sqrt_lut(64, 1023).astype("<u2").tobytes())
                         .hexdigest()[:16])

    def test_planes_follow_the_cfa(self):
        m = np.arange(16, dtype=np.uint16).reshape(4, 4)
        p = X.split_planes(m, "BGGR")
        self.assertEqual(p["B"].tolist(), [[0, 2], [8, 10]])
        self.assertEqual(p["G1"].tolist(), [[1, 3], [9, 11]])
        self.assertEqual(p["G2"].tolist(), [[4, 6], [12, 14]])
        self.assertEqual(p["R"].tolist(), [[5, 7], [13, 15]])
        self.assertEqual(X.plane_offsets("RGGB"), {"R": (0, 0), "G1": (0, 1), "G2": (1, 0),
                                                    "B": (1, 1)})

    def test_codes_clip_above_white_and_below_black(self):
        crop = {"mosaic": np.array([[0, 64], [1023, 4000]], dtype=np.uint16), "cfa": "RGGB",
                "black": 64, "white": 1023}
        codes = X.code_planes(crop)
        self.assertEqual([int(codes[k][0, 0]) for k in ("R", "G1", "G2", "B")], [0, 0, 4095, 4095])

    def test_pgm_is_16_bit_big_endian_maxval_4095(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.pgm")
            X.write_pgm(path, np.array([[1, 4095]], dtype=np.uint16))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"P5\n2 1\n4095\n\x00\x01\x0f\xff")


# ---------------------------------------------------------------------------
# NR container v1 (CONTAINER.md)
# ---------------------------------------------------------------------------

META = {"ExposureTime": 103005, "AnalogueGain": 1.122807, "ColourGains": [1.6634, 2.4142],
        "ColourCorrectionMatrix": [1.607722, -0.415966, -0.191762, -0.367844, 1.754623,
                                   -0.386781, 0.005148, -0.587414, 1.582275],
        "SensorTemperature": 31.0, "DigitalGain": 1.000063, "ColourTemperature": 3771}


def _sealed(payloads=(b"\xff\x0aR", b"\xff\x0aG1", b"\xff\x0aG2", b"\xff\x0aB")):
    params = X.build_params(crop_xywh=[1504, 846, 1600, 900], native_wh=(4608, 2592), crc=0,
                            colour=X.colour_params(META), distance=3.8, effort=5)
    return X.seal_container(w=1600, h=900, cfa="BGGR", black=64, white=1023, params=params,
                            payloads=list(payloads))


class Container(unittest.TestCase):
    def test_header_bytes(self):
        blob, crc = _sealed()
        # magic, method 14 (D2), flags 0x04 (4pl | sqrt << 2), cfa BGGR = 1, b = 12
        self.assertEqual(blob[:6], b"NR\x0e\x04\x01\x0c")
        head, payloads = X.unpack_container(blob)
        self.assertEqual((head["w"], head["h"], head["black"], head["white"], head["pedestal"]),
                         (1600, 900, 64, 1023, 0))
        self.assertEqual(len(head["params"]), X.N_PARAMS_V1)
        p = head["params"]
        self.assertEqual(p[:5], [1, 1504, 846, 4608, 2592])
        self.assertEqual(p[5], crc)
        self.assertEqual(p[6:10], [103005, 1123, 16634, 24142])
        self.assertEqual(p[10:19], [16077, -4160, -1918, -3678, 17546, -3868, 51, -5874, 15823])
        self.assertEqual(p[19:24], [310, 380, 5, 1000, 3771])
        self.assertEqual(head["lengths"], [3, 4, 4, 3])
        self.assertEqual(payloads, [b"\xff\x0aR", b"\xff\x0aG1", b"\xff\x0aG2", b"\xff\x0aB"])

    def test_varints_and_zigzag_match_the_study(self):
        self.assertEqual(X._uvarint(0), b"\x00")
        self.assertEqual(X._uvarint(127), b"\x7f")
        self.assertEqual(X._uvarint(300), b"\xac\x02")
        for v in (0, 1, -1, 2, -2, 16077, -5874, 2 ** 32 - 1, -32768):
            self.assertEqual(X._unzz(X._zz(v)), v)
        self.assertEqual([X._zz(v) for v in (0, -1, 1, -2, 2)], [0, 1, 2, 3, 4])

    def test_crc_covers_the_header_and_every_payload(self):
        blob, _crc = _sealed()
        X.unpack_container(blob)                         # intact: reads back
        hdr_end = len(blob) - 14
        for i in range(2, len(blob)):
            if i in (2, 3, 4, 5):                       # method / flags / cfa / b: own checks
                continue
            bad = bytearray(blob)
            bad[i] ^= 0x01
            try:
                X.unpack_container(bytes(bad))
            except (ValueError, IndexError):
                continue
            self.fail(f"a flipped bit at byte {i} ({'header' if i < hdr_end else 'payload'}) "
                      "read back silently")

    def test_trailing_or_missing_bytes_are_refused(self):
        blob, _crc = _sealed()
        for bad in (blob + b"\x00", blob[:-1]):
            with self.assertRaises(ValueError):
                X.unpack_container(bad)

    def test_rounding_is_half_away_from_zero(self):
        self.assertEqual([X.scaled(v, 10) for v in (0.05, -0.05, 0.04, -0.04, 31.0)],
                         [1, -1, 0, 0, 310])
        self.assertEqual(X.scaled(3.8, 100), 380)

    def test_missing_required_metadata_is_rfb_err(self):
        for drop in ("ExposureTime", "AnalogueGain", "ColourGains", "ColourCorrectionMatrix"):
            meta = {k: v for k, v in META.items() if k != drop}
            with self.assertRaises(X.RawFallback) as cm:
                X.colour_params(meta)
            self.assertEqual(cm.exception.code, "err")
        with self.assertRaises(X.RawFallback):
            X.colour_params(dict(META, ColourGains=[0.0, 2.0]))
        opt = X.colour_params({k: v for k, v in META.items()
                               if k not in ("SensorTemperature", "DigitalGain", "ColourTemperature")})
        self.assertEqual((opt["temp_x10"], opt["dgain_x1000"], opt["ct_k"]), (X.SENTINEL,) * 3)

    def test_fixture_blobs_read_back(self):
        with open(os.path.join(FIX, "blobs.json")) as fh:
            meta = json.load(fh)
        self.assertEqual(len(meta["blobs"]), 3)
        for name, info in meta["blobs"].items():
            with open(os.path.join(FIX, name), "rb") as fh:
                blob = fh.read()
            self.assertEqual(hashlib.sha256(blob).hexdigest(), info["sha256"], name)
            head, payloads = X.unpack_container(blob)
            self.assertEqual((head["method"], head["flags"], head["cfa"], head["b"]),
                             (14, 4, "BGGR", 12))
            self.assertEqual((head["w"], head["h"]), (1600, 900))
            self.assertEqual(head["params"], info["params"])
            self.assertEqual(head["params"][20], X.scaled(info["distance"], 100))
            self.assertLessEqual(X.message_count(len(blob), 384), 195)
            self.assertTrue(all(p[:2] == b"\xff\x0a" for p in payloads))   # bare codestreams

    @unittest.skipIf(shutil.which("djxl") is None, "djxl not installed")
    def test_fixture_planes_decode_to_12_bit_codes(self):
        name = "blob_cool_stop_-1_r0.nrjxl"
        with open(os.path.join(FIX, name), "rb") as fh:
            _head, payloads = X.unpack_container(fh.read())
        with tempfile.TemporaryDirectory() as d:
            for i, p in enumerate(payloads):
                src, dst = os.path.join(d, f"{i}.jxl"), os.path.join(d, f"{i}.pgm")
                with open(src, "wb") as fh:
                    fh.write(p)
                subprocess.run(["djxl", src, dst, "--num_threads=0"], check=True,
                               capture_output=True)
                with open(dst, "rb") as fh:
                    data = fh.read()
                magic, wh, maxval, raster = data.split(b"\n", 3)
                self.assertEqual((magic, wh, maxval), (b"P5", b"800 450", b"4095"))
                codes = np.frombuffer(raster, dtype=">u2")
                self.assertEqual(codes.size, 800 * 450)
                self.assertLessEqual(int(codes.max()), 4095)


# ---------------------------------------------------------------------------
# rung walk + guards (SPEC §3.5) with a fake runner
# ---------------------------------------------------------------------------

def _runner(sizes=None, kinds=None, seconds=1.0, log=None):
    """sizes: {distance: bytes per plane}; kinds: {(distance, plane): kind}."""
    sizes, kinds = sizes or {}, kinds or {}

    def run(cmd, *, timeout_s, stdout_path, stderr_path):
        d = float(cmd[cmd.index("-d") + 1])
        plane = os.path.basename(cmd[1]).split(".")[0]
        if log is not None:
            log.append((d, plane, round(timeout_s, 3)))
        kind = kinds.get((d, plane), "ok")
        if seconds > timeout_s:
            kind = "time"
        if kind == "ok":
            with open(cmd[2], "wb") as fh:
                fh.write(b"\xff\x0a" + b"x" * int(sizes.get(d, 1000)))
        return {"rc": 0 if kind == "ok" else 1, "kind": kind, "seconds": min(seconds, timeout_s),
                "peak_rss_kb": 31000}
    return run


class RungWalk(unittest.TestCase):
    """still.raw.target_fill = 0: the fixed rungs only (the r4 rate control)."""
    CFG = dict(X.DEFAULT_CONFIG, format="nrjxl", distances=[3.8, 4.6, 5.95, 8.25],
               target_fill=0.0)

    def encode(self, runner, budget=None, cfg=None, **kw):
        with tempfile.TemporaryDirectory() as d:
            return X.encode_still(MINI, META, cfg or self.CFG, crop_xywh=[0, 0, 160, 96],
                                  budget=budget or Budget(), message_cap=kw.pop("cap", 195),
                                  chunk_b64_chars=384, work_dir=os.path.join(d, "w"),
                                  cjxl="/usr/bin/cjxl", runner=runner, log=lambda *_: None, **kw)

    def test_fits_at_rung_1(self):
        res = self.encode(_runner())
        self.assertEqual((res["distance"], res["attempts"]), (3.8, 1))
        X.unpack_container(res["blob"])

    def test_steps_down_until_a_rung_fits(self):
        # 16 kB per plane (~64 kB, 223 msgs) at d 3.8 and 4.6; 12 kB (~48 kB) at 5.95
        res = self.encode(_runner(sizes={3.8: 16000, 4.6: 16000, 5.95: 12000}))
        self.assertEqual((res["distance"], res["attempts"]), (5.95, 3))
        self.assertEqual([a["over_cap"] for a in res["attempt_log"]], [True, True, False])

    def test_heal_reserve_shrinks_the_budget(self):
        # 12 kB per plane = 168 msgs. Room for 200 msgs: fits without heals, not with 40.
        budget = Budget(remaining=200 * 1.3)
        res = self.encode(_runner(sizes={d: 12000 for d in self.CFG["distances"]}), budget=budget)
        self.assertEqual(res["attempts"], 1)
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_runner(sizes={d: 12000 for d in self.CFG["distances"]}), budget=budget,
                        reserve_msgs=40)
        self.assertEqual(cm.exception.code, "fit")
        self.assertEqual(len(cm.exception.attempt_log), 4)

    def test_no_rung_fits(self):
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_runner(sizes={d: 30000 for d in self.CFG["distances"]}))
        self.assertEqual(cm.exception.code, "fit")

    def test_child_failures_map_to_rfb_codes(self):
        for kind in ("enc", "mem", "time"):
            with self.assertRaises(X.RawFallback) as cm:
                self.encode(_runner(kinds={(3.8, "G2"): kind}))
            self.assertEqual(cm.exception.code, kind)

    def test_missing_cjxl_is_enc(self):
        with mock.patch.object(X.shutil, "which", return_value=None):
            with tempfile.TemporaryDirectory() as d, self.assertRaises(X.RawFallback) as cm:
                X.encode_still(MINI, META, self.CFG, crop_xywh=[0, 0, 160, 96], budget=Budget(),
                               message_cap=195, chunk_b64_chars=384, work_dir=d,
                               runner=_runner(), log=lambda *_: None)
        self.assertEqual(cm.exception.code, "enc")

    def test_cjxl_that_cannot_start_is_enc_and_empty_output_is_enc(self):
        def boom(cmd, **_):
            raise FileNotFoundError(cmd[0])
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(boom)
        self.assertEqual(cm.exception.code, "enc")

        def silent(cmd, **_):
            return {"rc": 0, "kind": "ok", "seconds": 0.1, "peak_rss_kb": 1}
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(silent)
        self.assertEqual(cm.exception.code, "enc")

    def test_encode_cap_spans_all_rungs(self):
        # 4 planes x 3 s = 12 s per rung; cap 30 s: rung 1 (12 s), rung 2 (24 s), rung 3 is
        # cut at 6 s left -> time.
        log = []
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_runner(sizes={d: 30000 for d in self.CFG["distances"]}, seconds=3.0,
                                log=log), cfg=dict(self.CFG, encode_max_s=30),
                        clock=_StepClock(log))
        self.assertEqual(cm.exception.code, "time")
        self.assertEqual(len(cm.exception.attempt_log), 2)

    def test_the_fallback_send_time_is_never_spent(self):
        # 100 s left, the pjpg fallback needs (60 + 2) x 1.3 = 80.6 s: the encoders get 19.4 s.
        log = []
        self.encode(_runner(log=log), budget=Budget(remaining=100.0), fallback_msgs=60)
        self.assertAlmostEqual(log[0][2], 19.4, places=3)
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_runner(), budget=Budget(remaining=80.0), fallback_msgs=60)
        self.assertEqual(cm.exception.code, "time")

    def test_bad_dng_is_dng_and_anything_else_is_err(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.dng")
            with open(bad, "wb") as fh:
                fh.write(b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00")
            with self.assertRaises(X.RawFallback) as cm:
                X.encode_still(bad, META, self.CFG, crop_xywh=[0, 0, 160, 96], budget=Budget(),
                               message_cap=195, chunk_b64_chars=384, work_dir=d,
                               cjxl="/usr/bin/cjxl", runner=_runner(), log=lambda *_: None)
            self.assertEqual(cm.exception.code, "dng")
        with mock.patch.object(X, "code_planes", side_effect=MemoryError("numpy")):
            with self.assertRaises(X.RawFallback) as cm:
                self.encode(_runner())
        self.assertEqual(cm.exception.code, "err")

    def test_crop_over_raw_max_px_is_refused(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(X.RawFallback) as cm:
            X.encode_still(MINI, META, self.CFG, crop_xywh=[0, 0, 1602, 900], budget=Budget(),
                           message_cap=195, chunk_b64_chars=384, work_dir=d, cjxl="/x",
                           runner=_runner(), log=lambda *_: None)
        self.assertEqual(cm.exception.code, "err")

    def test_keep_crop_writes_the_mosaic(self):
        with tempfile.TemporaryDirectory() as d:
            keep = os.path.join(d, "s_raw_crop.pgm")
            X.encode_still(MINI, META, self.CFG, crop_xywh=[0, 0, 160, 96], budget=Budget(),
                           message_cap=195, chunk_b64_chars=384, work_dir=os.path.join(d, "w"),
                           cjxl="/x", runner=_runner(), keep_crop_path=keep, log=lambda *_: None)
            with open(keep, "rb") as fh:
                self.assertTrue(fh.read().startswith(b"P5\n160 96\n1023\n"))
            self.assertEqual(os.path.getsize(keep), len(b"P5\n160 96\n1023\n") + 160 * 96 * 2)

    def test_message_count_is_the_pjpg_arithmetic(self):
        self.assertEqual(X.message_count(288, 384), 1)
        self.assertEqual(X.message_count(289, 384), 2)
        self.assertEqual(X.message_count(50_000, 384), 174)
        self.assertEqual(X.message_count(56_100, 384), 195)
        self.assertEqual(X.message_count(50_000, 300), 223)


def _law(a, k, seconds=1.0, log=None, override=None):
    """Fake cjxl whose plane bytes follow a power law a * d ** -k (override(d) -> bytes
    replaces it when it returns a number)."""
    def run(cmd, *, timeout_s, stdout_path, stderr_path):
        d = float(cmd[cmd.index("-d") + 1])
        if log is not None:
            log.append(d)
        n = override(d) if override else None
        n = int(a * d ** -k) if n is None else int(n)
        with open(cmd[2], "wb") as fh:
            fh.write(b"\xff\x0a" + b"x" * n)
        return {"rc": 0, "kind": "ok", "seconds": seconds, "peak_rss_kb": 31000}
    return run


class Search(unittest.TestCase):
    """The byte-target search (Nick 2026-10-03; rig PR #89 PROPOSAL_byte_target.md)."""
    CFG = dict(X.DEFAULT_CONFIG, format="nrjxl")
    ROOM_195 = 3 * ((195 * 384) // 4)                    # 56 160 B: the 195-chunk room

    def encode(self, runner, budget=None, cfg=None, **kw):
        with tempfile.TemporaryDirectory() as d:
            return X.encode_still(MINI, META, cfg or self.CFG, crop_xywh=[0, 0, 160, 96],
                                  budget=budget or Budget(), message_cap=kw.pop("cap", 195),
                                  chunk_b64_chars=384, work_dir=os.path.join(d, "w"),
                                  cjxl="/usr/bin/cjxl", runner=runner, log=lambda *_: None, **kw)

    def test_defaults(self):
        self.assertEqual((X.DEFAULT_CONFIG["target_fill"], X.DEFAULT_CONFIG["d_max"]), (0.97, 10.4))
        self.assertEqual(R.BY_PATH["still.raw.target_fill"].range, (0.0, 1.0))
        self.assertEqual(R.BY_PATH["still.raw.d_max"].range, (0.1, 15.0))

    def test_fills_the_cap_in_at_most_3_encodes_for_any_slope(self):
        for a, k in ((3000, 0.8), (3000, 0.5), (3000, 1.0), (3000, 1.5), (40000, 0.8), (500, 1.2),
                     (40000, 0.5), (90000, 1.5), (12000, 1.0)):
            res = self.encode(_law(a, k))
            fill = res["message_count"] / 195
            self.assertLessEqual(res["message_count"], 195, (a, k))
            self.assertLessEqual(res["attempts"], X.SEARCH_MAX_ENCODES, (a, k))
            if res["distance"] > X.DIST_RANGE[0]:     # else: best quality allowed already fits
                self.assertGreaterEqual(len(res["blob"]), 0.93 * self.ROOM_195, (a, k, fill))
            self.assertEqual(res["rate_mode"], "search")
            X.unpack_container(res["blob"])

    def test_the_target_is_this_wakes_room_not_the_cap(self):
        # 100 s left at 1.3 s/msg = 76 msgs - START/END = 74 < cap 195 (condition 1)
        res = self.encode(_law(3000, 0.8), budget=Budget(remaining=100.0))
        self.assertLessEqual(res["message_count"], 74)
        self.assertGreaterEqual(res["message_count"], 66)
        # ... and the heal reserve comes off it too
        res = self.encode(_law(3000, 0.8), budget=Budget(remaining=100.0), reserve_msgs=30)
        self.assertLessEqual(res["message_count"], 44)
        self.assertGreaterEqual(res["message_count"], 38)

    def test_quality_floor_after_one_overshoot(self):
        log = []
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_law(2_000_000, 0.8, log=log), cfg=dict(self.CFG, d_max=0.5))
        self.assertEqual(cm.exception.code, "floor")
        self.assertEqual(len(log), 2 * 4)        # d 0.1 overshoots, d_max 0.5 measured: floor
        self.assertEqual(log[-1], 0.5)
        self.assertIn("d_max 0.5", cm.exception.detail)

    def test_a_plan_past_d_max_is_measured_before_the_floor(self):
        # steep scene (k 1.5): the one-point correction overshoots to d 15, but d 3.5 fits
        log = []
        res = self.encode(_law(90000, 1.5, log=log))
        self.assertEqual(res["rate_mode"], "search")
        self.assertIn(10.4, log)                 # d_max was measured, not assumed
        self.assertLess(res["distance"], 10.4)

    def test_a_crop_too_large_for_the_room_floors_after_one_encode_at_d_max(self):
        crop = X.read_dng_crop(MINI, [0, 0, 160, 96])
        log = []
        with tempfile.TemporaryDirectory() as d:
            walk = X._Walk(crop, X.code_planes(crop), X.colour_params(META), self.CFG,
                           crop_xywh=[0, 0, 3072, 1728], budget=Budget(), message_cap=195,
                           chunk_b64_chars=384, reserve_msgs=0, fallback_msgs=0, work_dir=d,
                           cjxl="/x", runner=_law(200_000, 0.8, log=log), clock=lambda: 0.0,
                           log=lambda *_: None)
            with self.assertRaises(X.RawFallback) as cm:
                X.target_search(walk)
        self.assertEqual((cm.exception.code, log), ("floor", [10.4] * 4))

    def test_no_fit_in_the_search_walks_the_fixed_rungs_above_it(self):
        log = []
        too_big = 0.5 * self.ROOM_195            # per plane: x4 = 2x the room, whatever d < 3
        res = self.encode(_law(1, 1, log=log, override=lambda d: too_big if d < 3 else 2000))
        self.assertEqual(res["rate_mode"], "rungs")
        self.assertEqual(res["distance"], 3.8)
        self.assertEqual(len(log), 4 * 4)        # (3 search encodes + the first rung) x 4 planes
        self.assertTrue(all(d < 3 for d in log[:12]))

    def test_target_fill_0_is_the_rungs_only(self):
        log = []
        res = self.encode(_law(3000, 0.8, log=log), cfg=dict(self.CFG, target_fill=0.0))
        self.assertEqual((res["rate_mode"], log[0]), ("rungs", 3.8))

    def test_no_room_is_fit(self):
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_law(3000, 0.8), budget=Budget(remaining=2.0))
        self.assertEqual(cm.exception.code, "fit")

    def test_a_fit_that_no_longer_fits_is_dropped(self):
        crop = X.read_dng_crop(MINI, [0, 0, 160, 96])
        budget = Budget(remaining=480.0)
        with tempfile.TemporaryDirectory() as d:
            walk = X._Walk(crop, X.code_planes(crop), X.colour_params(META), self.CFG,
                           crop_xywh=[0, 0, 160, 96], budget=budget, message_cap=195,
                           chunk_b64_chars=384, reserve_msgs=0, fallback_msgs=0, work_dir=d,
                           cjxl="/x", runner=_law(10000, 1.0), clock=lambda: 0.0,
                           log=lambda *_: None)
            entry, ok = walk.attempt(1.0, "t")
            self.assertTrue(ok)
            budget.left = (entry["message_count"] + 1) * 1.3       # no longer room for it
            self.assertEqual(walk.valid_fits(), [])
            self.assertIsNone(walk.result("search"))

    def test_room_bytes_inverts_message_count(self):
        crop = X.read_dng_crop(MINI, [0, 0, 160, 96])
        walk = X._Walk(crop, {}, {}, self.CFG, crop_xywh=[0, 0, 160, 96], budget=Budget(),
                       message_cap=195, chunk_b64_chars=384, reserve_msgs=0, fallback_msgs=0,
                       work_dir="/x", cjxl="/x", runner=None, clock=lambda: 0.0,
                       log=lambda *_: None)
        for m in range(1, 400):
            self.assertLessEqual(X.message_count(walk.room_bytes(m), 384), m)
            self.assertGreater(X.message_count(walk.room_bytes(m) + 3, 384), m)

    def test_island_and_render_carry_the_search_keys(self):
        text = config_v2.render_v1_text(_still(**{"still.format": "nrjxl",
                                                  "still.raw.d_max": 9.5}))
        self.assertIn("  target_fill: 0.97\n  d_max: 9.5\n", text)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.yaml")
            with open(path, "w") as fh:
                fh.write(text)
            cfg = X.load_raw_config(path)
            self.assertEqual((cfg["target_fill"], cfg["d_max"]), (0.97, 9.5))
            for bad in ("target_fill: 1.5", "d_max: 0.05", "d_max: x"):
                with open(path, "w") as fh:
                    fh.write(f"still_raw:\n  {bad}\n")
                with self.assertRaises(ValueError, msg=bad):
                    X.load_raw_config(path)
        self.assertNotIn("still_raw:", config_v2.render_v1_text(_still()))

    def test_floor_is_a_wire_code(self):
        self.assertIn("floor", U.RAW_FALLBACK_CODES)
        self.assertIn("floor", X.RFB_CODES)
        msg = U.build_rc_start_message("a_compressed.jpg", "t", 12, quality=13, enc_attempts=4,
                                       complete=True, rfb="floor")
        self.assertIn("cmp=1, rfb=floor", msg)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _scripted(plan, clock=None, log=None):
    """Fake cjxl driven per ENCODE (4 planes = one encode): plan[i] = (bytes_per_plane, kind,
    seconds_per_plane) for encode i (the last entry repeats). Advances `clock`."""
    calls = {"n": 0}

    def run(cmd, *, timeout_s, stdout_path, stderr_path):
        i = calls["n"] // 4
        calls["n"] += 1
        n, kind, sec = plan[min(i, len(plan) - 1)]
        d = float(cmd[cmd.index("-d") + 1])
        if log is not None and calls["n"] % 4 == 1:
            log.append(d)
        if sec > timeout_s:
            kind, sec = "time", timeout_s
        if clock is not None:
            clock.t += sec
        if kind == "ok":
            with open(cmd[2], "wb") as fh:
                fh.write(b"\xff\x0a" + b"x" * int(n))
        return {"rc": 0 if kind == "ok" else 1, "kind": kind, "seconds": sec,
                "peak_rss_kb": 31000}
    return run


class ReviewFixes(unittest.TestCase):
    """Independent review of #120 at 8070e64 (via the EM, 2026-10-03)."""
    CFG = dict(X.DEFAULT_CONFIG, format="nrjxl")

    def encode(self, runner, cfg=None, budget=None, clock=None):
        with tempfile.TemporaryDirectory() as d:
            kw = {"clock": clock} if clock is not None else {}
            return X.encode_still(MINI, META, cfg or self.CFG, crop_xywh=[0, 0, 160, 96],
                                  budget=budget or Budget(), message_cap=195,
                                  chunk_b64_chars=384, work_dir=os.path.join(d, "w"),
                                  cjxl="/usr/bin/cjxl", runner=runner, log=lambda *_: None, **kw)

    def test_1_a_later_failure_keeps_the_fit_in_hand(self):
        # encode 1 overshoots, encode 2 fits at low fill (the search goes on), encode 3
        # fails: the fit in hand is sent (the reviewer's repro: fit, then a timeout)
        for kind in ("time", "mem", "enc"):
            res = self.encode(_scripted([(400000, "ok", 1.0), (5000, "ok", 1.0),
                                         (0, kind, 1.0)]))
            self.assertEqual((res["attempts"], res["rate_mode"]), (3, "search"), kind)
            self.assertEqual(res["attempt_log"][-1]["failed"], kind)
            self.assertEqual(len(res["blob"]) // 1000, 20, kind)          # the 4 x 5 kB fit
        # the same failure with nothing in hand is still a fallback
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_scripted([(400000, "ok", 1.0), (0, "mem", 1.0)]))
        self.assertEqual(cm.exception.code, "mem")

    def test_1_an_encode_that_cannot_finish_is_not_started(self):
        # each encode takes 4 x 10 s = 40 s; encode 1 overshoots, encode 2 fits at low fill;
        # encode_max_s 90 leaves 10 s, less than the last encode took: no 3rd encode
        clock, log = _Clock(), []
        res = self.encode(_scripted([(400000, "ok", 10.0), (5000, "ok", 10.0)], clock=clock,
                                    log=log), cfg=dict(self.CFG, encode_max_s=90), clock=clock)
        self.assertEqual((len(log), res["attempts"]), (2, 2))
        # with nothing in hand it is rfb=time, without starting the encode
        clock, log = _Clock(), []
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_scripted([(400000, "ok", 10.0)], clock=clock, log=log),
                        cfg=dict(self.CFG, encode_max_s=60), clock=clock)
        self.assertEqual((cm.exception.code, len(log)), ("time", 1))
        self.assertIn("last encode 40.0 s", cm.exception.detail)

    def test_2_nrjxl_needs_cjxl_and_numpy_on_the_unit(self):
        v = _still(**{"still.format": "nrjxl"})
        env_ok = {"ffmpeg": True, "timezones_ok": {"America/Los_Angeles"}, "cjxl": True,
                  "numpy": True}
        self.assertEqual([x for x in V.validate(v, "effective", env=env_ok) if x.code == "xk"], [])
        for tool in ("cjxl", "numpy"):
            errs = [x for x in V.validate(v, "effective", env=dict(env_ok, **{tool: False}))
                    if x.code == "xk"]
            self.assertEqual(len(errs), 1, tool)
            self.assertEqual(errs[0].paths[0], "still.format")
            self.assertIn(tool, errs[0].message)
        # pjpg units and video units do not care
        self.assertEqual([x for x in V.validate(_still(), "effective",
                                                env=dict(env_ok, cjxl=False, numpy=False))
                          if x.code == "xk"], [])
        probe = V.probe_env([])
        self.assertIn("cjxl", probe)
        self.assertIn("numpy", probe)

    def test_2_no_encoder_means_no_raw_capture(self):
        import rc_progressive_jpeg as rc
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camera_schedule.yaml")
            with open(path, "w") as fh:
                fh.write('still_raw:\n  format: "nrjxl"\n')
            settings = {"config_path": path, "crop_native_xywh": (1504, 846, 1600, 900)}
            real_which = rc.shutil.which
            with mock.patch.object(rc.shutil, "which",
                                   side_effect=lambda n: None if n == "cjxl" else real_which(n)):
                raw = rc._raw_begin(settings, None, d)
        self.assertEqual((raw["rfb"], raw["dng"]), ("enc", None))
        self.assertIn("cjxl", raw["detail"])

    def test_3_a_raising_raw_capture_runs_todays_capture(self):
        import rc_capture
        import rc_progressive_jpeg as rc
        today = mock.Mock(return_value=("n.jpg", {}, "stem"))
        settings = {"capture_backend": "rpicam", "camera_controls_override": {},
                    "config_path": "/nonexistent", "source_width": 4608, "source_height": 2592,
                    "source_jpeg_quality": 95}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(rc, "_select_camera_command",
                                  return_value=("/usr/bin/rpicam-still", "rpicam")), \
                mock.patch.object(rc_capture, "run_raw_capture_once",
                                  side_effect=OSError("disk gone")):
            out = rc._default_raw_capture(settings, d, today)
        today.assert_called_once()
        self.assertEqual(out, ("n.jpg", {}, "stem", None, "OSError"))
        # today's capture failing is today's failure (not swallowed, not retried)
        boom = mock.Mock(side_effect=RuntimeError("Native capture failed after 4 attempts"))
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(rc, "_select_camera_command",
                                  return_value=("/usr/bin/rpicam-still", "rpicam")), \
                mock.patch.object(rc_capture, "run_raw_capture_once",
                                  return_value=(None, None, "timeout")), \
                self.assertRaisesRegex(RuntimeError, "after 4 attempts"):
            rc._default_raw_capture(settings, d, boom)
        boom.assert_called_once()

    def test_5_search_and_rungs_miss_then_d_max_is_measured(self):
        log = []
        big = 0.5 * 3 * ((195 * 384) // 4)          # x4 planes = 2x the room
        # misses everywhere below 10, fits at d_max 10.4
        res = self.encode(_law(1, 1, log=log, override=lambda d: big if d < 10 else 2000))
        self.assertEqual((res["distance"], res["rate_mode"]), (10.4, "rungs"))
        self.assertEqual(log[-1], 10.4)
        # misses at d_max too: floor, not fit
        log = []
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_law(1, 1, log=log, override=lambda d: big))
        self.assertEqual(cm.exception.code, "floor")
        self.assertEqual(log[-1], 10.4)
        # the rungs-only mode keeps its r4 answer (fit)
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_law(1, 1, override=lambda d: big), cfg=dict(self.CFG, target_fill=0.0))
        self.assertEqual(cm.exception.code, "fit")


class Memory(unittest.TestCase):
    """bmcam004 2026-10-05: wait4 reported 136 MB (the supervisor's RSS at fork), cjxl ~31-39."""

    def fake_proc(self, files):
        real_open = open

        def fake(path, *a, **k):
            if isinstance(path, str) and path in files:
                import io
                return io.StringIO(files[path])
            if isinstance(path, str) and path.startswith("/proc/"):
                raise FileNotFoundError(path)
            return real_open(path, *a, **k)
        return mock.patch("builtins.open", side_effect=fake)

    def test_hwm_counts_only_after_the_exec(self):
        status = "Name:\tx\nVmHWM:\t   31744 kB\nVmRSS:\t 30000 kB\n"
        with self.fake_proc({"/proc/7/comm": "sh\n", "/proc/7/status": status}):
            self.assertIsNone(X._own_hwm_kb(7, "cjxl"))      # still the forked shell
        with self.fake_proc({"/proc/7/comm": "cjxl\n", "/proc/7/status": status}):
            self.assertEqual(X._own_hwm_kb(7, "cjxl"), 31744)
        with self.fake_proc({}):
            self.assertIsNone(X._own_hwm_kb(7, "cjxl"))      # gone / not Linux
        with self.fake_proc({"/proc/self/status": status}):
            self.assertEqual(X.self_rss_kb(), 30000)

    def test_both_figures_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            r = X.run_capped([sys.executable, "-c", "import time; time.sleep(0.3)"],
                             timeout_s=10, stdout_path=os.path.join(d, "o"),
                             stderr_path=os.path.join(d, "e"))
        self.assertIn("maxrss_incl_parent_kb", r)
        self.assertGreater(r["maxrss_incl_parent_kb"], 0)
        if not sys.platform.startswith("linux"):
            self.assertIsNone(r["peak_rss_kb"])               # no /proc: unknown, not 0

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc + fork semantics")
    def test_a_big_parent_does_not_inflate_the_tools_own_peak(self):
        ballast = bytearray(150 * 2 ** 20)                     # the supervisor's RSS
        for i in range(0, len(ballast), 4096):
            ballast[i] = 1
        with tempfile.TemporaryDirectory() as d:
            r = X.run_capped([sys.executable, "-c", "import time; time.sleep(0.5)"],
                             timeout_s=10, stdout_path=os.path.join(d, "o"),
                             stderr_path=os.path.join(d, "e"))
        self.assertGreater(r["maxrss_incl_parent_kb"], 140 * 1024)
        self.assertLess(r["peak_rss_kb"], 60 * 1024)
        del ballast

    def test_planes_are_written_once_and_the_arrays_dropped(self):
        seen = []

        def runner(cmd, *, timeout_s, stdout_path, stderr_path):
            work = os.path.dirname(cmd[1])
            seen.append(sorted(f for f in os.listdir(work) if f.endswith(".pgm")))
            with open(cmd[2], "wb") as fh:
                fh.write(b"\xff\x0a" + b"x" * 500)
            return {"rc": 0, "kind": "ok", "seconds": 0.1, "peak_rss_kb": None}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(X, "rung_walk_list", wraps=X.rung_walk_list) as rw:
            X.encode_still(MINI, META, dict(X.DEFAULT_CONFIG, target_fill=0.0),
                           crop_xywh=[0, 0, 160, 96], budget=Budget(), message_cap=195,
                           chunk_b64_chars=384, work_dir=os.path.join(d, "w"), cjxl="/x",
                           runner=runner, log=lambda *_: None)
            walk = rw.call_args[0][0]
        self.assertEqual(seen[0], ["B.pgm", "G1.pgm", "G2.pgm", "R.pgm"])   # before plane 1
        self.assertIsNone(walk.codes)
        self.assertNotIn("mosaic", walk.crop)
        self.assertEqual((walk.crop["w"], walk.crop["h"]), (160, 96))

    def test_a_missing_plane_without_codes_is_err(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(X.RawFallback) as cm:
            X.encode_rung(None, 3.8, 5, d, cjxl="/x", runner=None, timeout_s=10)
        self.assertEqual(cm.exception.code, "err")


class R33VideoUnitStills(unittest.TestCase):
    """LADDER R3.3 FAIL on bmcam004 (2026-10-05): a video unit with still.format nrjxl ACCEPTED
    still.crop [1505, 846, 1600, 900] (ack ok) because the nrjxl rules required mode.media
    still. The unit's own v9 dispatcher must refuse it (e:xk), and so must a trg kv."""

    def rig(self):
        sys.path.insert(0, HERE)
        from test_s4_dispatch import Rig
        return Rig(self, base_over={"mode.media": "video", "still.format": "nrjxl",
                                    "uplink.media_key.enabled": True})

    def test_set_of_an_odd_crop_on_a_video_unit_is_refused(self):
        r = self.rig()
        r.send({"id": 1_000_001, "c": "set", "kv": {"still.crop": [1505, 846, 1600, 900]}})
        ack = r.acks()[0]
        self.assertEqual((ack["ok"], ack.get("e")), (0, "xk"))
        r.send({"id": 1_000_002, "c": "set", "kv": {"still.crop": [1504, 846, 1600, 900]}})
        self.assertEqual(r.acks()[0]["ok"], 1)               # an even crop is fine

    def test_a_trg_kv_with_an_odd_crop_is_refused(self):
        r = self.rig()
        r.send({"id": 1_000_003, "c": "trg", "v": 2,
                "kv": {"med": "still", "r": [1505, 846, 1600, 900]}})
        ack = r.acks()[0]
        self.assertEqual((ack["ok"], ack.get("e")), (0, "xk"))
        self.assertIsNone(r.state.pending_trigger)           # nothing armed
        r.send({"id": 1_000_004, "c": "trg", "v": 2,
                "kv": {"med": "still", "r": [1504, 846, 1600, 900]}})
        self.assertEqual(r.acks()[0]["ok"], 1)

    def test_too_large_a_crop_on_a_video_unit_is_refused(self):
        r = self.rig()
        r.send({"id": 1_000_005, "c": "trg", "v": 2,
                "kv": {"med": "still", "r": [0, 0, 4608, 2592]}})
        self.assertEqual(r.acks()[0].get("e"), "xk")

    def test_a_bad_crop_that_reaches_the_action_is_never_captured_raw(self):
        import rc_progressive_jpeg as rc
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camera_schedule.yaml")
            with open(path, "w") as fh:
                fh.write('still_raw:\n  format: "nrjxl"\n')
            for crop in ((1505, 846, 1600, 900), (0, 0, 4608, 2592)):
                raw = rc._raw_begin({"config_path": path, "crop_native_xywh": crop}, None, d)
                self.assertEqual((raw["rfb"], raw["dng"]), ("err", None), crop)
                self.assertIn("invalid RAW crop", raw["detail"])


class _StepClock:
    """A clock that advances by the seconds each fake cjxl run reports."""

    def __init__(self, log):
        self.log, self.t, self.seen = log, 0.0, 0

    def __call__(self):
        while self.seen < len(self.log):
            self.t += 3.0
            self.seen += 1
        return self.t


@unittest.skipIf(os.name != "posix", "POSIX process control")
class RunCapped(unittest.TestCase):
    def run_one(self, script, timeout_s=10.0):
        with tempfile.TemporaryDirectory() as d:
            return X.run_capped([sys.executable, "-c", script], timeout_s=timeout_s,
                                stdout_path=os.path.join(d, "o"), stderr_path=os.path.join(d, "e"))

    def test_classifies_real_children(self):
        self.assertEqual(self.run_one("pass")["kind"], "ok")
        self.assertEqual(self.run_one("import sys; sys.exit(3)")["kind"], "enc")
        r = self.run_one("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
        self.assertEqual((r["kind"], r["rc"]), ("mem", -signal.SIGKILL))
        self.assertEqual(self.run_one("import sys; sys.stderr.write('std::bad_alloc'); "
                                      "sys.exit(1)")["kind"], "mem")
        r = self.run_one("import time; time.sleep(30)", timeout_s=0.5)
        self.assertEqual(r["kind"], "time")
        self.assertLess(r["seconds"], 5.0)

    def test_a_binary_that_does_not_exist_is_enc(self):
        with tempfile.TemporaryDirectory() as d:
            r = X.run_capped(["/nonexistent/cjxl"], timeout_s=5, stdout_path=os.path.join(d, "o"),
                             stderr_path=os.path.join(d, "e"))
        self.assertEqual(r["kind"], "enc")
        self.assertIn(r["rc"], (126, 127))          # sh: not found (dash 127, macOS sh 126)

    def test_the_guard_wraps_without_preexec_and_execs_in_place(self):
        cmd = X.guarded(["/usr/bin/cjxl", "a", "b"])
        self.assertEqual(cmd[:2], ["/bin/sh", "-c"])
        self.assertIn("oom_score_adj", cmd[2])
        self.assertIn(f"ulimit -v {250 * 1024}", cmd[2])
        self.assertEqual(cmd[3:], ["/usr/bin/cjxl", "a", "b"])
        # exec keeps the pid: the child IS the tool (its exit code comes straight back)
        r = self.run_one("import sys; sys.exit(7)")
        self.assertEqual(r["rc"], 7)

    @unittest.skipUnless(sys.platform.startswith("linux"), "RLIMIT_AS is enforced on Linux only")
    def test_the_memory_cap_kills_an_overrun(self):
        r = self.run_one("x = bytearray(400 * 2 ** 20)")
        self.assertEqual(r["kind"], "mem")

    def test_the_measured_invocation(self):
        self.assertEqual(X.cjxl_command("/usr/bin/cjxl", "a.pgm", "a.jxl", 3.8, 5),
                         ["/usr/bin/cjxl", "a.pgm", "a.jxl", "-m", "1", "-e", "5", "-d", "3.8000",
                          "--num_threads=0"])


# ---------------------------------------------------------------------------
# config: registry v8 keys, the still_raw island, rules (SPEC §3.7)
# ---------------------------------------------------------------------------

def _still(**kv):
    v = R.defaults()
    v.update({"mode.media": "still", "uplink.media_key.enabled": True})
    v.update(kv)
    return v


class Config(unittest.TestCase):
    def test_registry_v8_keys(self):
        self.assertGreaterEqual(R.REGISTRY_VERSION, 8)      # 9 = low-gain exposure keys
        self.assertEqual(R.BY_PATH["still.format"].default, "pjpg")
        self.assertEqual(R.BY_PATH["still.format"].enum, ("pjpg", "nrjxl"))
        self.assertEqual(R.BY_PATH["still.raw.distances"].type, R.DLADDER)
        self.assertEqual(R.BY_PATH["still.raw.encode_max_s"].range, (5, 120))
        self.assertEqual(R.BY_PATH["still.raw.effort"].default, 5)
        self.assertEqual(X.RAW_MAX_PX, V.RAW_MAX_PX)
        self.assertEqual(V.RAW_MAX_PX, 1600 * 900)

    def test_s0_rungs_are_the_registry_default(self):
        with open(os.path.join(REPO, "runs", "s28_s0_calibration_20261002", "rungs.json")) as fh:
            picked = json.load(fh)["picked"]["1600x900"]
        self.assertEqual(R.BY_PATH["still.raw.distances"].default,
                         [picked[n]["rung"] for n in ("rung1_cap195", "rung2_heal174", "rung3_150",
                                                      "rung4_complex")])
        self.assertEqual(X.DEFAULT_CONFIG["distances"], R.BY_PATH["still.raw.distances"].default)

    def test_distance_ladder_values(self):
        key = R.BY_PATH["still.raw.distances"]
        for ok in ([3.8], [3, 4.5], [0.1, 15.0], [1.0, 2.0, 3.0, 4.0]):
            self.assertIsNone(R.check_value(key, ok), ok)
        for bad in ([], [4.0, 3.0], [3.0, 3.0], [0.05], [15.5], [1, 2, 3, 4, 5], [2.5, "x"],
                    [2.5, True], 3.8, "3.8", [float("nan")]):
            self.assertIsNotNone(R.check_value(key, bad), bad)

    def test_hash_reads_a_dladder_as_floats(self):
        self.assertEqual(config_v2.config_hash(_still(**{"still.raw.distances": [3, 4]})),
                         config_v2.config_hash(_still(**{"still.raw.distances": [3.0, 4.0]})))

    def test_rules(self):
        def errs(**kv):
            return [v for v in V.validate(_still(**kv), "effective") if v.code == "xk"]
        self.assertEqual(errs(**{"still.format": "nrjxl"}), [])
        self.assertEqual(errs(**{"still.format": "pjpg", "still.crop": [1505, 846, 1600, 900]}), [])
        e = errs(**{"still.format": "nrjxl", "still.crop": [1505, 846, 1600, 900]})
        self.assertEqual(e[0].paths[0], "still.crop")
        self.assertIn("even", e[0].message)
        e = errs(**{"still.format": "nrjxl", "still.crop": [1104, 620, 2400, 1350]})
        self.assertIn("R0.3", e[0].message)
        e = errs(**{"still.format": "nrjxl", "uplink.media_key.enabled": False})
        self.assertEqual(e[0].paths[:2], ("still.format", "uplink.media_key.enabled"))
        e = errs(**{"still.format": "nrjxl", "uplink.network_type": 1})
        self.assertEqual(e[0].paths[:2], ("still.format", "uplink.network_type"))
        # R3.3 FAIL (bmcam004 2026-10-05): a VIDEO unit with still.format nrjxl takes stills
        # through trg med:still, so the rules apply to it too
        e = errs(**{"mode.media": "video", "still.format": "nrjxl",
                    "still.crop": [1505, 846, 1600, 900]})
        self.assertEqual(e[0].paths[0], "still.crop")
        self.assertEqual(errs(**{"mode.media": "video", "still.format": "nrjxl",
                                 "uplink.media_key.enabled": False})[0].paths[1],
                         "uplink.media_key.enabled")
        # a pjpg video unit: no nrjxl rule
        self.assertEqual(errs(**{"mode.media": "video", "still.crop": [1505, 846, 1600, 900]}), [])
        # the base scope (a plain boot load) never runs them (S4 doctrine)
        self.assertEqual([v for v in V.validate(_still(**{"still.format": "nrjxl",
                                                          "uplink.network_type": 1}), "base")
                          if v.code == "xk"], [])

    def test_presets_are_even(self):
        for label, crop in R.BY_PATH["still.crop"].presets:
            self.assertTrue(all(c % 2 == 0 for c in crop), label)

    def test_render_island_only_when_not_default(self):
        self.assertNotIn("still_raw:", config_v2.render_v1_text(_still()))
        text = config_v2.render_v1_text(_still(**{"still.format": "nrjxl",
                                                  "still.raw.distances": [3, 4.5],
                                                  "still.raw.keep_crop": True}))
        self.assertIn('still_raw:\n  format: "nrjxl"\n  distances: "3.0,4.5"\n'
                      '  encode_max_s: 30\n  keep_crop: true\n  effort: 5\n', text)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camera_schedule.yaml")
            with open(path, "w") as fh:
                fh.write(text)
            cfg = X.load_raw_config(path)
        self.assertEqual((cfg["format"], cfg["distances"], cfg["keep_crop"], cfg["source"]),
                         ("nrjxl", [3.0, 4.5], True, "yaml"))

    def test_island_defaults_and_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.yaml")
            with open(path, "w") as fh:
                fh.write("capture_mode: progressive_jpeg\n")
            self.assertEqual(X.load_raw_config(path)["format"], "pjpg")
            self.assertEqual(X.load_raw_config(os.path.join(d, "absent.yaml"))["source"], "default")
            for bad in ('format: "jpeg"', 'distances: "4,3"', 'distances: "1,2,3,4,5"',
                        "encode_max_s: 4", "keep_crop: yes", "effort: 9"):
                with open(path, "w") as fh:
                    fh.write(f"still_raw:\n  {bad}\n")
                with self.assertRaises(ValueError, msg=bad):
                    X.load_raw_config(path)


# ---------------------------------------------------------------------------
# START wire (SPEC §3.6)
# ---------------------------------------------------------------------------

class Start(unittest.TestCase):
    def build(self, **kw):
        return U.build_rc_start_message("2026-10-05T17:00:41Z_image_compressed.nrjxl",
                                        "2026-10-05T17:00:41Z", 176, quality=kw.pop("q", 380),
                                        enc_attempts=kw.pop("att", 2), complete=True,
                                        key="0dpr00", **kw)

    def test_nrjxl_start(self):
        self.assertEqual(self.build(fmt="nrjxl"),
                         "<START IMG> filename: 2026-10-05T17:00:41Z_image_compressed.nrjxl, "
                         "timestamp: 2026-10-05T17:00:41Z, length: 176, key=0dpr00, fmt=nrjxl, "
                         "q=380, att=2, cmp=1\n")

    def test_every_rfb_code_is_a_core_field(self):
        meta = {"image_res_key": "1000x562", "timezone": "America/Los_Angeles",
                "window_start": "10:00", "window_end": "15:00", "software_sha": "abc123def456",
                "hostname": "bmcam003", "sd_total_bytes": 31_000_000_000,
                "sd_used_bytes": 9_000_000_000, "sd_free_bytes": 22_000_000_000,
                "sd_used_pct": 29.03}
        for code in U.RAW_FALLBACK_CODES:
            msg = self.build(fmt="pjpg", q=13, att=4, rfb=code, start_metadata=meta)
            self.assertIn(f"fmt=pjpg, q=13, att=4, cmp=1, rfb={code}", msg)
            self.assertLessEqual(len(msg.encode()), 285)
        self.assertIn("rfb=err", self.build(fmt="pjpg", q=13, rfb="bogus"))

    def test_pjpg_start_is_unchanged_by_default(self):
        self.assertEqual(U.build_rc_start_message("a.jpg", "t", 10, quality=13, enc_attempts=1,
                                                  complete=True),
                         "<START IMG> filename: a.jpg, timestamp: t, length: 10, fmt=pjpg, q=13, "
                         "att=1, cmp=1\n")
        with self.assertRaises(ValueError):
            self.build(fmt="heic")

    def test_an_nrjxl_is_never_sent_incomplete(self):
        import rc_transmit
        with self.assertRaises(ValueError):
            rc_transmit.transmit_progressive_image(
                lambda b: None, Budget(), jpeg_data=b"x", compressed_file_name="a.nrjxl",
                quality=380, enc_attempts=1, fits=False, chunk_b64_chars=384,
                delay_seconds=1.3, fmt="nrjxl")


# ---------------------------------------------------------------------------
# files: storage guard, orphan sweep (SPEC §3.1)
# ---------------------------------------------------------------------------

class Files(unittest.TestCase):
    def test_storage_guard_knows_the_nrjxl_files(self):
        with tempfile.TemporaryDirectory() as d:
            stem = "2026-10-05T17:00:41Z_image"
            names = [f"{stem}_native_full.jpg", f"{stem}_compressed.jpg",
                     f"{stem}_compressed.jpg.capture_metadata.json", f"{stem}_compressed.nrjxl",
                     f"{stem}_compressed.nrjxl.capture_metadata.json", f"{stem}_raw_crop.pgm"]
            for n in names:
                open(os.path.join(d, n), "w").close()
            g = rc_still_storage.stems(d)[stem]
            self.assertEqual([os.path.basename(p) for p in g["native"]],
                             [names[0], names[5]])
            self.assertEqual([os.path.basename(p) for p in g["compressed"]], names[1:5])
            # the .nrjxl named by a live sent record is a heal payload: never pruned
            sent = os.path.join(d, "sent")
            os.makedirs(sent)
            with open(os.path.join(sent, f"{stem}_compressed.sent.json"), "w") as fh:
                json.dump({"payload": os.path.join(d, names[3])}, fh)
            groups = rc_still_storage.plan_candidates(d, sent, 14, now_ts=os.path.getmtime(sent))
            self.assertEqual([t for t, _l, _f in groups], [1])

    def test_storage_guard_unchanged_for_pjpg(self):
        with tempfile.TemporaryDirectory() as d:
            stem = "s"
            for n in (f"{stem}_native_full.jpg", f"{stem}_compressed.jpg"):
                open(os.path.join(d, n), "w").close()
            g = rc_still_storage.stems(d)[stem]
            self.assertEqual(len(g["native"]), 1)
            self.assertEqual(len(g["compressed"]), 1)

    def test_orphan_sweep(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a_native_full.dng", "a_native_full.jpg", "a_raw_crop.pgm"):
                open(os.path.join(d, n), "w").close()
            os.makedirs(os.path.join(d, X.WORK_PREFIX + "a"))
            open(os.path.join(d, X.WORK_PREFIX + "a", "R.pgm"), "w").close()
            lines = []
            self.assertEqual(X.sweep_orphans(d, log=lines.append), 2)
            self.assertEqual(sorted(os.listdir(d)), ["a_native_full.jpg", "a_raw_crop.pgm"])
            self.assertEqual(len(lines), 1)
            self.assertEqual(X.sweep_orphans(d, log=lines.append), 0)
            self.assertEqual(len(lines), 1)              # silent when nothing to do


# ---------------------------------------------------------------------------
# capture (SPEC §3.1): one --raw attempt, then today's UNCHANGED capture
# ---------------------------------------------------------------------------

class Capture(unittest.TestCase):
    def setUp(self):
        import rc_capture
        self.cap = rc_capture
        self.tmp = tempfile.mkdtemp()
        self.native = os.path.join(self.tmp, "s_native_full.jpg")
        self.prefix = os.path.join(self.tmp, "s_native_full")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_run(self, behaviour):
        calls = []

        def run(cmd, **kw):
            calls.append(list(cmd))
            if behaviour == "hang":
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            out = cmd[cmd.index("-o") + 1]
            if behaviour != "rc1":
                with open(out, "wb") as fh:
                    fh.write(b"\xff\xd8jpeg")
                if behaviour != "no_dng":
                    with open(os.path.splitext(out)[0] + ".dng", "wb") as fh:
                        fh.write(b"II*\x00dng")
            return subprocess.CompletedProcess(cmd, 1 if behaviour == "rc1" else 0)
        return run, calls

    def once(self, behaviour):
        run, calls = self.fake_run(behaviour)
        with mock.patch.object(self.cap.subprocess, "run", run), \
                mock.patch.object(self.cap, "_send_capture_status") as ws:
            out = self.cap.run_raw_capture_once("/usr/bin/rpicam-still", self.native, 4608, 2592,
                                                95, self.prefix)
        return out, calls, ws

    def test_the_raw_command_is_the_production_command_plus_raw(self):
        (_info, dng, why), calls, ws = self.once("ok")
        prod, _c, _r = self.cap.native_capture_command("/usr/bin/rpicam-still", self.native, 4608,
                                                       2592, 95, self.prefix + ".metadata.json")
        self.assertEqual(calls, [prod[:-2] + ["--raw"] + prod[-2:]])
        self.assertEqual(calls[0][-3:], ["--raw", "-o", self.native])
        self.assertEqual((dng, why), (self.prefix + ".dng", None))
        ws.assert_not_called()

    def test_hang_failure_and_no_dng(self):
        for behaviour, want in (("hang", "timeout"), ("rc1", "rc=1")):
            (info, dng, why), calls, ws = self.once(behaviour)
            self.assertEqual((info, dng, why), (None, None, want))
            self.assertEqual(len(calls), 1)               # ONE attempt, no retry ladder
            ws.assert_not_called()                        # no WS for the RAW attempt
            self.assertFalse(os.path.exists(self.native))
        (info, dng, why), calls, ws = self.once("no_dng")
        self.assertIsNotNone(info)                        # the JPEG is kept: no 2nd capture
        self.assertEqual((dng, why), (None, "no_dng"))
        ws.assert_not_called()

    def test_a_failed_raw_attempt_runs_todays_capture_once(self):
        import rc_progressive_jpeg as rc
        run, calls = self.fake_run("hang")
        today = mock.Mock(return_value=("n.jpg", {"capture_command": ["today"]}, "stem"))
        settings = {"capture_backend": "rpicam", "camera_controls_override": {},
                    "config_path": "/nonexistent", "source_width": 4608, "source_height": 2592,
                    "source_jpeg_quality": 95}
        with mock.patch.object(self.cap.subprocess, "run", run), \
                mock.patch.object(rc, "_select_camera_command",
                                  return_value=("/usr/bin/rpicam-still", "rpicam")), \
                mock.patch.object(self.cap, "_send_capture_status") as ws:
            out = rc._default_raw_capture(settings, self.tmp, today)
        today.assert_called_once_with(settings, self.tmp)
        self.assertEqual(out, ("n.jpg", {"capture_command": ["today"]}, "stem", None, "timeout"))
        self.assertEqual(len(calls), 1)
        ws.assert_not_called()

    def test_the_production_command_is_unchanged(self):
        cmd, _c, _r = self.cap.native_capture_command("/usr/bin/rpicam-still", self.native, 4608,
                                                      2592, 95, "/m.json")
        self.assertEqual(cmd, ["/usr/bin/rpicam-still", "-n", "--timeout", "2000", "--width",
                               "4608", "--height", "2592", "--quality", "95", "--metadata",
                               "/m.json", "-o", self.native])


if __name__ == "__main__":
    unittest.main()
