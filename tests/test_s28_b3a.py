#!/usr/bin/env python3
# filename: test_s28_b3a.py
# description: Sprint28 B3a (linear camera RGB -> JPEG XL VarDCT, container profile v2): the no-clip bound, the strip demosaic, the coding and its inverse, container v2, the encode through the byte search, config.
"""
DESIGN_B3a.md. The demosaic is checked against an independent per-pixel bilinear written
here (no OpenCV in the venv); the Mac e2e tool (tools/s28_b3a_e2e_check.py) compares with
OpenCV and scores against the sweep. Real cjxl / djxl tests are skipped when the tools are
absent.

Run: .venv-dev/bin/python -m pytest -q tests/test_s28_b3a.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "BM_Devel_Pi"))

import numpy as np  # noqa: E402

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import rc_raw_jxl as X  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "s28")
MINI = os.path.join(FIX, "mini.dng")
META = {"ExposureTime": 103005, "AnalogueGain": 1.122807, "ColourGains": [1.6634, 2.4142],
        "ColourCorrectionMatrix": [1.607722, -0.415966, -0.191762, -0.367844, 1.754623,
                                   -0.386781, 0.005148, -0.587414, 1.582275],
        "SensorTemperature": 31.0, "DigitalGain": 1.000063, "ColourTemperature": 3771}
RGB_CFG = dict(X.DEFAULT_CONFIG, format="nrjxl", layout="rgb")


def ref_bilinear(mosaic, cfa, black, white):
    """Per-pixel bilinear, written independently of rc_raw_jxl: at a site, a channel is the
    sample itself if the site has it, else the mean of the same-colour samples among the
    8 neighbours that are nearest (cross for G, row/column pair or diagonals for R/B),
    with mirror (reflect) borders."""
    h, w = mosaic.shape
    m = np.clip((mosaic.astype(np.float64) - black) / (white - black), 0, 1)

    def col(y, x):
        return cfa[(y % 2) * 2 + (x % 2)]

    def at(y, x):
        y = -y if y < 0 else (2 * (h - 1) - y if y >= h else y)
        x = -x if x < 0 else (2 * (w - 1) - x if x >= w else x)
        return m[y, x]
    out = np.zeros((h, w, 3))
    for y in range(h):
        for x in range(w):
            for ci, c in enumerate("RGB"):
                if col(y, x) == c:
                    out[y, x, ci] = m[y, x]
                    continue
                for ring in (((0, 1), (0, -1), (1, 0), (-1, 0)),
                             ((1, 1), (1, -1), (-1, 1), (-1, -1))):
                    vals = [at(y + dy, x + dx) for dy, dx in ring
                            if col(y + dy, x + dx) == c]
                    if vals:
                        out[y, x, ci] = np.mean(vals)
                        break
    return out


def _mosaic(h=24, w=32, seed=1, hi=1023):
    rng = np.random.default_rng(seed)
    return rng.integers(64, hi + 1, (h, w)).astype(np.uint16)


class Demosaic(unittest.TestCase):
    def test_matches_the_independent_bilinear(self):
        for cfa in ("RGGB", "BGGR", "GRBG", "GBRG"):
            mos = _mosaic(seed=hash(cfa) % 100)
            ref = ref_bilinear(mos, cfa, 64, 1023)
            codes = X.rgb_codes(mos, cfa, 64, 1023, (1.0, 1.0, 1.0), 10000)
            lin = X.rgb_linear_from_codes(codes, (1.0, 1.0, 1.0), 10000)
            # the only difference is the 12-bit sqrt quantisation (<= half a code)
            step = 2 * np.sqrt(np.maximum(ref, 1e-9)) / 4095 + (1 / 4095) ** 2
            self.assertTrue(np.all(np.abs(lin - ref) <= step + 1e-7), cfa)

    def test_strips_equal_one_full_frame(self):
        mos = _mosaic(h=70, w=40, seed=3)
        full = X.rgb_codes(mos, "BGGR", 64, 1023, (1.6, 1.0, 2.4), 25000, strip_rows=70)
        for rows in (1, 2, 7, 64):
            self.assertTrue(np.array_equal(
                X.rgb_codes(mos, "BGGR", 64, 1023, (1.6, 1.0, 2.4), 25000, strip_rows=rows),
                full), rows)

    def test_known_sites_keep_their_sample(self):
        mos = _mosaic(seed=5)
        codes = X.rgb_codes(mos, "BGGR", 64, 1023, (1.0, 1.0, 1.0), 10000)
        lut = X.sqrt_lut(64, 1023)
        self.assertTrue(np.array_equal(codes[0::2, 0::2, 2], lut[mos[0::2, 0::2]]))   # B
        self.assertTrue(np.array_equal(codes[1::2, 1::2, 0], lut[mos[1::2, 1::2]]))   # R
        self.assertTrue(np.array_equal(codes[0::2, 1::2, 1], lut[mos[0::2, 1::2]]))   # G


class NoClip(unittest.TestCase):
    def test_the_bound_is_never_exceeded_and_is_tight(self):
        for seed in range(12):
            mos = _mosaic(seed=seed)
            mos[seed % 24, (3 * seed) % 32] = 1023                     # a saturated site
            g = (2.9, 1.0, 1.7)
            hx = X.headroom_x10000(mos, "GRBG", 64, 1023, g)
            lin = ref_bilinear(mos, "GRBG", 64, 1023)
            peak = float((lin * np.asarray(g)).max())
            self.assertLessEqual(peak, hx / 10000 + 1e-12)
            self.assertGreaterEqual(peak, hx / 10000 - 1e-3)   # tight: a sample IS the max

    def test_nothing_clips_and_the_inverse_is_camera_linear(self):
        mos = _mosaic(seed=7)
        mos[::2, ::2] = 1023                                    # every R site saturated
        g = X.coding_gains(X.colour_params(META))
        hx = X.headroom_x10000(mos, "RGGB", 64, 1023, g)
        self.assertGreater(hx, 10000)
        codes = X.rgb_codes(mos, "RGGB", 64, 1023, g, hx)
        self.assertLessEqual(int(codes.max()), 4095)
        lin = X.rgb_linear_from_codes(codes, g, hx)
        ref = ref_bilinear(mos, "RGGB", 64, 1023)
        self.assertLess(float(np.abs(lin - ref).max()), 2e-3)   # camera-native, no WB, no clip
        self.assertAlmostEqual(float(lin[0::2, 0::2, 0].max()), 1.0, places=3)

    def test_a_dim_frame_is_plain_wb(self):
        mos = np.full((8, 8), 100, np.uint16)
        self.assertEqual(X.headroom_x10000(mos, "BGGR", 64, 1023, (3.0, 1.0, 2.0)), 10000)

    def test_gains_are_the_rounded_header_values(self):
        c = X.colour_params(META)
        g = X.coding_gains(c)
        dg = c["dgain_x1000"] / 1000                       # 1.000 (rounded from 1.000063)
        self.assertEqual(g, (dg * c["gains_x10000"][0] / 10000, dg,
                             dg * c["gains_x10000"][1] / 10000))
        no_dg = dict(c, dgain_x1000=X.SENTINEL)
        self.assertEqual(X.coding_gains(no_dg)[1], 1.0)


class ContainerV2(unittest.TestCase):
    def params(self, hx=14321):
        return X.build_params_v2(crop_xywh=[1504, 846, 1600, 900], native_wh=(4608, 2592),
                                 crc=0, colour=X.colour_params(META), distance=2.6, effort=5,
                                 headroom_x10000=hx)

    def test_params_v2_append_three(self):
        p = self.params()
        self.assertEqual((len(p), p[0], p[24], p[25], p[26]), (27, 2, 14321, 1, 10000))
        v1 = X.build_params(crop_xywh=[1504, 846, 1600, 900], native_wh=(4608, 2592), crc=0,
                            colour=X.colour_params(META), distance=2.6, effort=5)
        self.assertEqual(p[1:24], v1[1:])

    def test_round_trip_method_flags_one_payload(self):
        blob, crc = X.seal_container(w=1600, h=900, cfa="BGGR", black=64, white=1023,
                                     params=self.params(), payloads=[b"\xff\x0aRGB"],
                                     method=X.METHOD_B3A)
        self.assertEqual(blob[2:4], bytes([20, 0x06]))
        head, payloads = X.unpack_container(blob)
        self.assertEqual((head["method"], head["flags"], head["lengths"], payloads),
                         (20, 0x06, [5], [b"\xff\x0aRGB"]))
        self.assertEqual(head["params"][5], crc)

    def test_refusals(self):
        blob, _ = X.seal_container(w=1600, h=900, cfa="BGGR", black=64, white=1023,
                                   params=self.params(), payloads=[b"\xff\x0aRGB"],
                                   method=X.METHOD_B3A)
        for i in (8, 30, len(blob) - 1):                 # header, params (headroom), payload
            bad = bytearray(blob)
            bad[i] ^= 0x01
            with self.assertRaises(ValueError):
                X.unpack_container(bytes(bad))
        with self.assertRaises(ValueError):              # v2 with v1 flags
            X.unpack_container(blob[:3] + bytes([0x04]) + blob[4:])
        with self.assertRaises(ValueError):              # method 20 must carry 1 payload
            X.pack_container(w=1600, h=900, cfa="BGGR", black=64, white=1023,
                             params=self.params(), payloads=[b"a"] * 4, method=X.METHOD_B3A)

    def test_v1_bytes_are_unchanged(self):
        import json
        with open(os.path.join(FIX, "blobs.json")) as fh:
            blobs = json.load(fh)
        self.assertEqual(len(blobs["blobs"]), 3)
        for name in blobs["blobs"]:
            path = os.path.join(FIX, name)
            with open(path, "rb") as fh:
                blob = fh.read()
            head, payloads = X.unpack_container(blob)
            again = X.pack_container(w=head["w"], h=head["h"], cfa=head["cfa"],
                                     black=head["black"], white=head["white"],
                                     params=head["params"], payloads=payloads)
            self.assertEqual(again, blob, name)


def _rgb_runner(size=40000, seconds=6.0, log=None, kinds=None):
    def run(cmd, *, timeout_s, stdout_path, stderr_path):
        d = float(cmd[cmd.index("-d") + 1])
        if log is not None:
            log.append((os.path.basename(cmd[1]), cmd[cmd.index("-m") + 1], d))
        kind = (kinds or {}).get(d, "ok")
        if seconds > timeout_s:
            kind = "time"
        if kind == "ok":
            n = size(d) if callable(size) else size
            with open(cmd[2], "wb") as fh:
                fh.write(b"\xff\x0a" + b"x" * int(n))
        return {"rc": 0 if kind == "ok" else 1, "kind": kind,
                "seconds": min(seconds, timeout_s), "peak_rss_kb": 110000}
    return run


class Budget:
    def __init__(self, remaining=480.0, pace=1.3):
        self.left, self.seconds_per_message = remaining, pace

    def remaining_s(self):
        return self.left

    def messages_fit(self, n):
        return self.left >= n * self.seconds_per_message


class EncodeStill(unittest.TestCase):
    def encode(self, runner, cfg=None):
        with tempfile.TemporaryDirectory() as d:
            work = os.path.join(d, "w")
            res = X.encode_still(MINI, META, cfg or RGB_CFG, crop_xywh=[0, 0, 160, 96],
                                 budget=Budget(), message_cap=195, chunk_b64_chars=384,
                                 work_dir=work, cjxl="/usr/bin/cjxl", runner=runner,
                                 log=lambda *_: None)
            listing = sorted(os.listdir(work))
        return res, listing

    def test_one_vardct_encode_per_attempt_and_a_v2_blob(self):
        log = []
        # bytes ~ 54 kB x (d / 2.6) ** -0.85: the search fills the 195-chunk room
        res, listing = self.encode(_rgb_runner(size=lambda d: 54000 * (d / 2.6) ** -0.85 - 2,
                                               log=log))
        self.assertTrue(all(src == "rgb.ppm" and m == "0" for src, m, _ in log))
        self.assertLessEqual(res["attempts"], 3)
        head, payloads = X.unpack_container(res["blob"])
        self.assertEqual((head["method"], head["flags"], len(payloads)), (20, 0x06, 1))
        self.assertEqual((head["params"][0], head["params"][25], head["params"][26]), (2, 1, 10000))
        self.assertEqual(head["params"][24], res["timings"]["headroom_x10000"])
        self.assertIn("rgb.ppm", listing)
        self.assertNotIn("R.pgm", listing)
        self.assertGreaterEqual(res["message_count"], int(0.90 * 195))

    def test_the_prior_starts_near_d_2_6_for_the_full_crop(self):
        log = []
        walk_cfg = dict(RGB_CFG)
        crop = {"cfa": "BGGR", "black": 64, "white": 1023, "native_w": 4608, "native_h": 2592,
                "w": 1600, "h": 900, "headroom_x10000": 10000}
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, X.RGB_PPM), "wb").write(b"P6\n1 1\n4095\n\x00\x00\x00\x00\x00\x00")
            X.choose_rate(crop, None, X.colour_params(META), walk_cfg,
                          crop_xywh=[1504, 846, 1600, 900], budget=Budget(), message_cap=195,
                          chunk_b64_chars=384, reserve_msgs=0, fallback_msgs=0, work_dir=d,
                          cjxl="/usr/bin/cjxl", runner=_rgb_runner(size=54000, log=log),
                          log=lambda *_: None)
        self.assertAlmostEqual(log[0][2], 2.6, delta=0.15)

    def test_failures_are_the_usual_rfb_codes(self):
        for kind in ("enc", "mem"):
            with self.assertRaises(X.RawFallback) as cm:
                self.encode(_rgb_runner(kinds={d / 1000: kind for d in range(100, 15001)}))
            self.assertEqual(cm.exception.code, kind)
        with self.assertRaises(X.RawFallback) as cm:                 # over the 45 s rgb cap
            self.encode(_rgb_runner(seconds=50))
        self.assertEqual(cm.exception.code, "time")

    def test_rgb_cap_is_its_own_key(self):
        res, _ = self.encode(_rgb_runner(size=54000, seconds=40))  # 40 s > 30 (bayer4 cap)
        self.assertEqual(res["attempts"], 1)
        with self.assertRaises(X.RawFallback) as cm:
            self.encode(_rgb_runner(size=54000, seconds=40), cfg=dict(RGB_CFG, rgb_encode_max_s=30))
        self.assertEqual(cm.exception.code, "time")

    def test_e4_last_attempt_when_no_e5_fits_in_the_time_left(self):
        t = [0.0]

        def run(cmd, *, timeout_s, stdout_path, stderr_path):
            e = int(cmd[cmd.index("-e") + 1])
            secs = 2.7 if e == 4 else 18.0
            t[0] += min(secs, timeout_s)
            if secs > timeout_s:
                return {"rc": -9, "kind": "time", "seconds": timeout_s, "peak_rss_kb": None}
            with open(cmd[2], "wb") as fh:            # e5 always over the cap, e4 fits
                fh.write(b"\xff\x0a" + b"x" * (52000 if e == 4 else 80000))
            return {"rc": 0, "kind": "ok", "seconds": secs, "peak_rss_kb": None}
        with tempfile.TemporaryDirectory() as d:
            res = X.encode_still(MINI, META, RGB_CFG, crop_xywh=[0, 0, 160, 96], budget=Budget(),
                                 message_cap=195, chunk_b64_chars=384,
                                 work_dir=os.path.join(d, "w"), cjxl="/usr/bin/cjxl",
                                 runner=run, clock=lambda: t[0], log=lambda *_: None)
        efforts = [a.get("effort") for a in res["attempt_log"]]
        self.assertEqual(efforts, [5, 5, 4])
        self.assertEqual(X.unpack_container(res["blob"])[0]["params"][21], 4)   # effort param

    def test_v2_carries_the_sensor_crop_origin_and_native_size(self):
        # production reads the FULL DNG at still.crop: params[1..4] = crop x, y, native w, h
        with tempfile.TemporaryDirectory() as d:
            res = X.encode_still(MINI, META, RGB_CFG, crop_xywh=[16, 8, 128, 80], budget=Budget(),
                                 message_cap=195, chunk_b64_chars=384,
                                 work_dir=os.path.join(d, "w"), cjxl="/usr/bin/cjxl",
                                 runner=_rgb_runner(size=40000), log=lambda *_: None)
        head = X.unpack_container(res["blob"])[0]
        self.assertEqual(head["params"][1:5], [16, 8, 160, 96])
        self.assertEqual((head["w"], head["h"]), (128, 80))

    def test_the_v2_fixture_has_production_geometry(self):
        import hashlib
        import json
        with open(os.path.join(FIX, "blobs_v2.json")) as fh:
            meta = json.load(fh)["blobs"]["blob_v2_bmcam004_57521.nrjxl"]
        with open(os.path.join(FIX, "blob_v2_bmcam004_57521.nrjxl"), "rb") as fh:
            blob = fh.read()
        self.assertEqual(hashlib.sha256(blob).hexdigest(), meta["sha256"])
        head, payloads = X.unpack_container(blob)
        self.assertEqual((head["method"], head["params"][0], len(payloads)), (20, 2, 1))
        self.assertEqual(head["params"][1:5], [1504, 846, 4608, 2592])

    def test_bayer4_is_untouched(self):
        log = []
        res, listing = self.encode(_rgb_runner(size=10000, log=log),
                                   cfg=dict(RGB_CFG, layout="bayer4"))
        self.assertEqual({src for src, _m, _d in log}, {"R.pgm", "G1.pgm", "G2.pgm", "B.pgm"})
        self.assertEqual(X.unpack_container(res["blob"])[0]["method"], 14)


@unittest.skipUnless(shutil.which("cjxl") and shutil.which("djxl"), "cjxl/djxl not installed")
class RealCodec(unittest.TestCase):
    def test_encode_decode_inverts_to_camera_linear(self):
        with tempfile.TemporaryDirectory() as d:
            res = X.encode_still(MINI, META, dict(RGB_CFG, target_fill=0.0, distances=[0.5]),
                                 crop_xywh=[0, 0, 160, 96], budget=Budget(), message_cap=500,
                                 chunk_b64_chars=384, work_dir=os.path.join(d, "w"),
                                 log=lambda *_: None)
            head, (payload,) = X.unpack_container(res["blob"])
            jxl, png = os.path.join(d, "x.jxl"), os.path.join(d, "x.ppm")
            open(jxl, "wb").write(payload)
            subprocess.run(["djxl", jxl, png, "--bits_per_sample=16", "--quiet"], check=True)
            with open(png, "rb") as fh:
                data = fh.read()
        _p6, _wh, maxval, body = data.split(b"\n", 3)
        # djxl keeps the stream's 12-bit depth in a PPM (maxval 4095) even with
        # --bits_per_sample=16; a 16-bit PNG is scaled to 65535. Honour maxval either way.
        dec = np.frombuffer(body, ">u2").reshape(96, 160, 3).astype(np.float64)
        codes = np.rint(dec * 4095 / int(maxval))
        c = X.colour_params(META)
        lin = X.rgb_linear_from_codes(codes, X.coding_gains(c), head["params"][24])
        crop = X.read_dng_crop(MINI, [0, 0, 160, 96])
        ref = ref_bilinear(crop["mosaic"], crop["cfa"], crop["black"], crop["white"])
        self.assertLess(float(np.abs(lin - ref).mean()), 0.01)    # d 0.5: near-lossless


class Config(unittest.TestCase):
    def test_registry_key(self):
        self.assertGreaterEqual(R.REGISTRY_VERSION, 10)
        k = R.BY_PATH["still.raw.layout"]
        self.assertEqual((k.default, k.enum), ("bayer4", ("bayer4", "rgb")))
        self.assertEqual(X.DEFAULT_CONFIG["layout"], "bayer4")

    def test_island_only_carries_layout_when_rgb(self):
        v = R.defaults()
        v.update({"mode.media": "still", "still.format": "nrjxl"})
        self.assertNotIn("layout", config_v2.render_v1_text(v))
        v["still.raw.layout"] = "rgb"
        text = config_v2.render_v1_text(v)
        self.assertIn('  layout: "rgb"\n', text)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.yaml")
            open(path, "w").write(text)
            self.assertEqual(X.load_raw_config(path)["layout"], "rgb")
            open(path, "w").write('still_raw:\n  layout: "rgb4"\n')
            with self.assertRaises(ValueError):
                X.load_raw_config(path)


if __name__ == "__main__":
    unittest.main()
