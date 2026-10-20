#!/usr/bin/env python3
# filename: test_s3_crop_view.py
# description: Crop view S3 (docs/specs/crop_view_s3.md): B3a crops larger than 1600x900 (16:9 only) shrunk to exactly 1600x900 in a memory-capped child; the validator's 16:9 rule; params[26]; rfb=shrink; the JPEG fallback of a larger crop.
"""
Must dos 1-5 + 7 of docs/specs/crop_view_s3.md (the camera half):
  1  shrink: output dims, params[26] for every even 16:9 crop wider than 1600, the area
     weights against a dense float64 reference (and cv2 INTER_AREA when installed) within
     +-1 code, the container header keeping the native crop rect;
  2  crops <= 1600x900: rgb_codes byte-identical to S2's (a frozen copy of 2cc1afdb's code);
     no child, params[26] = 10000. (The 5-DNG blob regression is a run, not a unit test:
     ~/NereusData/bm_cam_legacy/2026-10-19_crop_view_s3/regression/.)
  3  the validator: <= 1600x900 as before, larger only at exactly 16:9, both sides; at set and
     trg (dispatch rig), effective + strict scopes, the pjpg capture gate, the encoder;
  4  the JPEG fallback of a larger crop = the same crop resized to still.output_width;
  5  both shrink guards (pre-check, RLIMIT_AS child) and every child failure end in rfb=shrink
     (or the child's own dng / err, time on a timeout); the real RLIMIT runs on Linux only
     (macOS does not enforce ulimit -v); the one-shot G1 (b) force flag.
Synthetic DNGs are written here (a minimal uncompressed 16-bit CFA TIFF, the reader's
required tags only); cjxl is a fake runner (no encodes).

Run (repo root): .venv-dev/bin/python -m pytest -q tests/test_s3_crop_view.py
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import config_registry as R  # noqa: E402
import config_validate as V  # noqa: E402
import rc_raw_jxl as X  # noqa: E402
import rc_uplink_messages as U  # noqa: E402

META = {"ExposureTime": 103005, "AnalogueGain": 1.122807, "ColourGains": [1.6634, 2.4142],
        "ColourCorrectionMatrix": [1.607722, -0.415966, -0.191762, -0.367844, 1.754623,
                                   -0.386781, 0.005148, -0.587414, 1.582275],
        "SensorTemperature": 31.0, "DigitalGain": 1.000063, "ColourTemperature": 3771}
RGB_CFG = dict(X.DEFAULT_CONFIG, format="nrjxl", layout="rgb")
BIG = (1664, 936)                  # the smallest 16:9 frame here that needs the shrink (w > 1600)
NATIVE_JPEG = os.path.join(REPO_ROOT, "reference_images", "prepared", "P7071008",
                           "synthetic_native_4608x2592.jpg")


def write_dng(path, mosaic, black=64, white=1023, cfa=(2, 1, 1, 0)):
    """A minimal little-endian DNG the reader accepts: one uncompressed 16-bit CFA strip
    (BGGR = codes 2,1,1,0), BlackLevel / WhiteLevel, every value inline."""
    h, w = mosaic.shape
    tags = [(254, 4, 1, 0), (256, 4, 1, w), (257, 4, 1, h), (258, 3, 1, 16), (259, 3, 1, 1),
            (262, 3, 1, 32803), (273, 4, 1, None), (277, 3, 1, 1), (278, 4, 1, h),
            (279, 4, 1, w * h * 2), (33421, 3, 2, (2, 2)), (33422, 1, 4, bytes(cfa)),
            (50713, 3, 2, (1, 1)), (50714, 4, 1, black), (50717, 4, 1, white)]
    data_off = 8 + 2 + 12 * len(tags) + 4
    out = bytearray(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(tags)))
    for tag, typ, count, val in tags:
        if val is None:
            val = data_off
        if typ == 1:
            raw = bytes(val).ljust(4, b"\0")
        elif typ == 3:
            vals = val if isinstance(val, tuple) else (val,)
            raw = struct.pack("<" + "H" * len(vals), *vals).ljust(4, b"\0")
        else:
            raw = struct.pack("<I", val)
        out += struct.pack("<HHI", tag, typ, count) + raw
    out += struct.pack("<I", 0)
    assert len(out) == data_off
    out += np.ascontiguousarray(mosaic, dtype="<u2").tobytes()
    with open(path, "wb") as fh:
        fh.write(bytes(out))


def _mosaic(h, w, seed=3, lo=64, hi=1023):
    rng = np.random.default_rng(seed)
    base = np.linspace(lo, hi, w)[None, :] * np.linspace(0.3, 1.0, h)[:, None]
    noise = rng.integers(-40, 41, (h, w))
    return np.clip(base + noise, lo, hi).astype(np.uint16)


def _s2_rgb_codes(mosaic, cfa, black, white, g, s_x10000, strip_rows=64):
    """rc_raw_jxl.rgb_codes as it is at S2 (2cc1afdb), frozen here: must do 2's guard that the
    S3 refactor (_rgb_strips) changes no byte at native density."""
    h, w = mosaic.shape
    s = s_x10000 / 10000.0
    mul = np.asarray(g, np.float32) / np.float32(s)
    out = np.empty((h, w, 3), np.uint16)
    xs = np.arange(-1, w + 1)
    for y0 in range(0, h, strip_rows):
        y1 = min(h, y0 + strip_rows)
        a, b = max(0, y0 - 1), min(h, y1 + 1)
        sub = np.clip((mosaic[a:b].astype(np.float32) - black) / float(white - black), 0, 1)
        top, bot = (1 if a == y0 else 0), (1 if b == y1 else 0)
        p = np.pad(sub, ((top, bot), (1, 1)), mode="reflect")
        ys = np.arange(a - top, a - top + p.shape[0])
        m = X._cfa_masks(cfa, ys, xs)
        r0, n = y0 - (a - top), y1 - y0
        chans = []
        for c, k in (("R", X._K_RB), ("G", X._K_G), ("B", X._K_RB)):
            chans.append(X._conv3(p * m[c], k, r0, n, w))
        v = np.stack(chans, -1) * mul
        np.clip(v, 0.0, 1.0, out=v)
        out[y0:y1] = np.floor(np.sqrt(v) * X.CODE_MAX + 0.5).astype(np.uint16)
    return out


def _dense_area(n_in, n_out):
    """The area-average matrix (n_out x n_in), float64, from interval overlaps directly."""
    k = n_in / n_out
    m = np.zeros((n_out, n_in))
    for i in range(n_out):
        a, b = i * k, (i + 1) * k
        for j in range(int(np.floor(a)), min(n_in, int(np.ceil(b)))):
            m[i, j] = max(0.0, min(b, j + 1) - max(a, j)) / k
    return m


def _linear(mosaic, g, hx):
    return np.concatenate([v for _, _, v in X._rgb_strips(mosaic, "BGGR", 64, 1023, g, hx)])


class Budget:
    def __init__(self, remaining=480.0, pace=1.3):
        self.left, self.seconds_per_message = remaining, pace

    def remaining_s(self):
        return self.left

    def messages_fit(self, n):
        return self.left >= n * self.seconds_per_message


def _rgb_runner(size=40000, log=None):
    """Fake cjxl: records (input PPM dims, distance) and writes `size` bytes."""
    def run(cmd, *, timeout_s, stdout_path, stderr_path):
        with open(cmd[1], "rb") as fh:
            magic, dims = fh.readline(), fh.readline().split()
        if log is not None:
            log.append(((int(dims[0]), int(dims[1])), float(cmd[cmd.index("-d") + 1])))
        with open(cmd[2], "wb") as fh:
            fh.write(b"\xff\x0a" + b"x" * int(size))
        return {"rc": 0, "kind": "ok", "seconds": 6.0, "peak_rss_kb": 110000}
    return run


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dng = os.path.join(self.tmp, "big.dng")
        self.mosaic = _mosaic(BIG[1], BIG[0])
        write_dng(self.dng, self.mosaic)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def encode(self, crop, cfg=None, **kw):
        kw.setdefault("runner", _rgb_runner())
        return X.encode_still(self.dng, META, cfg or RGB_CFG, crop_xywh=list(crop),
                              budget=Budget(), message_cap=195, chunk_b64_chars=384,
                              work_dir=os.path.join(self.tmp, "w"), cjxl="/usr/bin/cjxl",
                              log=lambda *_: None, **kw)


# ---------------------------------------------------------------------------
# must do 3: the rule
# ---------------------------------------------------------------------------

def _still(**kv):
    v = R.defaults()
    v.update({"mode.media": "still", "uplink.media_key.enabled": True, "still.format": "nrjxl"})
    v.update(kv)
    return v


ACCEPT = ([1504, 846, 1600, 900], [0, 0, 4608, 2592], [768, 432, 3072, 1728],
          [1152, 648, 2304, 1296], [1804, 1014, 1000, 562], [1904, 1070, 800, 450],
          [1984, 1116, 640, 360], [1344, 756, 1920, 1080], [1104, 620, 2400, 1350],
          [704, 396, 3200, 1800],
          [1000, 800, 2000, 700])      # <= 1600x900 px and not 16:9: accepted as at S2
REFUSE = ([1104, 620, 2400, 1352], [0, 0, 4608, 2590], [0, 0, 4606, 2592],
          [1344, 756, 1920, 1082], [0, 0, 1602, 900])


class Rule(unittest.TestCase):
    def test_output_wh(self):
        self.assertEqual(V.raw_crop_output_wh(1600, 900), (1600, 900))
        self.assertEqual(V.raw_crop_output_wh(2000, 700), (2000, 700))
        self.assertEqual(V.raw_crop_output_wh(4608, 2592), (1600, 900))
        self.assertIsNone(V.raw_crop_output_wh(4608, 2590))
        self.assertIsNone(V.raw_crop_output_wh(0, 900))
        self.assertEqual((V.RAW_MAX_PX, V.RAW_OUT_WH), (1600 * 900, (1600, 900)))
        self.assertIs(X.raw_crop_output_wh, V.raw_crop_output_wh)

    def test_every_even_16_9_crop_wider_than_1600_codes_exactly_1600x900(self):
        n = 0
        for w in range(1602, 4609, 2):
            if (w * 9) % 16 or ((w * 9) // 16) % 2:
                continue
            h = w * 9 // 16
            s = X.output_scale_x10000(w) / 10000.0
            for rnd in (lambda v: int(np.floor(v + 0.5)), round):    # half-up and Python's
                self.assertEqual((rnd(w * s), rnd(h * s)), (1600, 900), (w, h))
            self.assertEqual(V.raw_crop_output_wh(w, h), (1600, 900))
            n += 1
        self.assertEqual(n, 94)                                      # QE 009's count
        self.assertEqual([X.output_scale_x10000(w) for w in (4608, 3072, 2304, 1600)],
                         [3472, 5208, 6944, 10000])

    def test_validator_both_sides_in_strict_and_effective(self):
        for scope in ("strict", "effective"):
            for crop in ACCEPT:
                errs = [v for v in V.validate(_still(**{"still.crop": crop}), scope)
                        if v.code == "xk" and v.paths[0] == "still.crop"]
                self.assertEqual(errs, [], (scope, crop))
            for crop in REFUSE:
                errs = [v for v in V.validate(_still(**{"still.crop": crop}), scope)
                        if v.code == "xk" and v.paths[0] == "still.crop"]
                self.assertTrue(errs and "16:9" in errs[0].message, (scope, crop))
        # pjpg: no nrjxl rule (any crop the frame holds, as at S2)
        self.assertEqual([v for v in V.validate(_still(**{"still.format": "pjpg",
                                                          "still.crop": REFUSE[0]}), "effective")
                          if v.code == "xk"], [])
        # outside the sensor: _rule_crop (unchanged) still refuses a 16:9 crop that does not fit
        errs = [v for v in V.validate(_still(**{"still.crop": [32, 0, 4608, 2592]}), "effective")
                if v.code == "xk"]
        self.assertTrue(any("native frame" in v.message for v in errs))

    def test_every_registry_preset_passes(self):
        for label, crop in R.BY_PATH["still.crop"].presets:
            self.assertIsNotNone(V.raw_crop_output_wh(crop[2], crop[3]), label)

    def test_set_and_trg_on_the_dispatch_rig(self):
        from test_s4_dispatch import Rig
        r = Rig(self, base_over={"still.format": "nrjxl", "uplink.media_key.enabled": True})
        for i, (crop, ok) in enumerate([([0, 0, 4608, 2592], 1), ([1104, 620, 2400, 1352], 0),
                                        ([1152, 648, 2304, 1296], 1), ([0, 0, 4608, 2590], 0)]):
            r.send({"id": 1_100_000 + 2 * i, "c": "set", "kv": {"still.crop": crop}})
            ack = r.acks()[0]
            self.assertEqual((ack["ok"], ack.get("e")), (ok, None if ok else "xk"), crop)
            r.send({"id": 1_100_001 + 2 * i, "c": "trg", "v": 2, "kv": {"med": "still", "r": crop}})
            ack = r.acks()[0]
            self.assertEqual((ack["ok"], ack.get("e")), (ok, None if ok else "xk"), crop)

    def test_the_pjpg_capture_gate(self):
        import rc_progressive_jpeg as rc
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camera_schedule.yaml")
            with open(path, "w") as fh:
                fh.write('still_raw:\n  format: "nrjxl"\n  layout: "rgb"\n')
            with mock.patch.object(rc.shutil, "which", lambda n: "/usr/bin/" + n):
                ok = rc._raw_begin({"config_path": path, "crop_native_xywh": (0, 0, 4608, 2592)},
                                   None, d)
                bad = rc._raw_begin({"config_path": path,
                                     "crop_native_xywh": (0, 0, 4608, 2590)}, None, d)
        self.assertIsNone(ok["rfb"])
        self.assertEqual(bad["rfb"], "err")
        self.assertIn("invalid RAW crop", bad["detail"])

    def test_shrink_is_a_wire_code(self):
        self.assertIn("shrink", U.RAW_FALLBACK_CODES)
        self.assertIn("shrink", X.RFB_CODES)
        msg = U.build_rc_start_message("a_compressed.jpg", "t", 12, quality=13, enc_attempts=4,
                                       complete=True, rfb="shrink")
        self.assertIn("rfb=shrink", msg)


# ---------------------------------------------------------------------------
# must do 1: the area average
# ---------------------------------------------------------------------------

class Area(unittest.TestCase):
    def test_taps_are_exact_overlaps_summing_to_one(self):
        for n_in, n_out in ((4608, 1600), (2304, 1600), (1664, 1600), (37, 11), (16, 16)):
            idx, wts = X.area_taps(n_in, n_out)
            np.testing.assert_allclose(wts.sum(1), 1.0, atol=1e-12)
            dense = np.zeros((n_out, n_in))
            for i in range(n_out):
                for t in range(idx.shape[1]):
                    dense[i, idx[i, t]] += wts[i, t]
            np.testing.assert_allclose(dense, _dense_area(n_in, n_out), atol=1e-12)
        with self.assertRaises(ValueError):
            X.area_taps(10, 11)

    def test_shrunk_codes_match_a_dense_float64_reference(self):
        g = (1.66, 1.0, 2.41)
        for (h, w), (ow, oh), strip in (((58, 100), (37, 21), 7), ((936, 1664), (1600, 900), 64)):
            m = _mosaic(h, w, seed=w)
            hx = X.headroom_x10000(m, "BGGR", 64, 1023, g)
            got = X.rgb_codes_shrunk(m, "BGGR", 64, 1023, g, hx, out_wh=(ow, oh),
                                     strip_rows=strip).astype(np.int32)
            lin = _linear(m, g, hx).astype(np.float64)
            ry, rx = _dense_area(h, oh), _dense_area(w, ow)
            ref = np.stack([ry @ lin[:, :, c] @ rx.T for c in range(3)], -1)
            ref = np.floor(np.sqrt(np.clip(ref, 0, 1)) * X.CODE_MAX + 0.5).astype(np.int32)
            self.assertEqual(got.shape, (oh, ow, 3))
            self.assertLessEqual(int(np.abs(got - ref).max()), 1, (w, h))

    @unittest.skipUnless(__import__("importlib").util.find_spec("cv2"), "cv2 not installed")
    def test_cv2_inter_area_within_one_code(self):
        import cv2
        g = (1.66, 1.0, 2.41)
        m = _mosaic(936, 1664, seed=9)
        hx = X.headroom_x10000(m, "BGGR", 64, 1023, g)
        got = X.rgb_codes_shrunk(m, "BGGR", 64, 1023, g, hx).astype(np.int32)
        ref = cv2.resize(_linear(m, g, hx).astype(np.float64), (1600, 900),
                         interpolation=cv2.INTER_AREA)
        ref = np.floor(np.sqrt(np.clip(ref, 0, 1)) * X.CODE_MAX + 0.5).astype(np.int32)
        self.assertLessEqual(int(np.abs(got - ref).max()), 1)

    def test_strip_height_does_not_change_a_byte(self):
        g = (1.4, 1.0, 1.9)
        m = _mosaic(130, 226, seed=5)
        hx = X.headroom_x10000(m, "BGGR", 64, 1023, g)
        a = X.rgb_codes_shrunk(m, "BGGR", 64, 1023, g, hx, out_wh=(160, 90), strip_rows=64)
        b = X.rgb_codes_shrunk(m, "BGGR", 64, 1023, g, hx, out_wh=(160, 90), strip_rows=6)
        self.assertTrue(np.array_equal(a, b))


class NativeUnchanged(unittest.TestCase):
    """Must do 2 (unit half): rgb_codes == S2's, byte for byte."""

    def test_rgb_codes_equal_s2(self):
        for (h, w), strip, g, cfa in (((900, 1600), 64, (1.66, 1.0, 2.41), "BGGR"),
                                      ((96, 160), 7, (2.0, 1.0, 1.3), "RGGB"),
                                      ((562, 1000), 64, (1.2, 1.0, 1.1), "GRBG")):
            m = _mosaic(h, w, seed=h)
            hx = X.headroom_x10000(m, cfa, 64, 1023, g)
            self.assertTrue(np.array_equal(X.rgb_codes(m, cfa, 64, 1023, g, hx, strip),
                                           _s2_rgb_codes(m, cfa, 64, 1023, g, hx, strip)))


# ---------------------------------------------------------------------------
# must dos 1 + 2 through encode_still
# ---------------------------------------------------------------------------

class EncodeShrunk(Tmp):
    def test_a_larger_crop_codes_1600x900_and_keeps_the_native_rect(self):
        log = []
        res = self.encode([0, 0, *BIG], runner=_rgb_runner(log=log))
        head, payloads = X.unpack_container(res["blob"])
        self.assertEqual((head["w"], head["h"]), BIG)                  # the native crop rect
        self.assertEqual(head["params"][1:5], [0, 0, *BIG])            # crop x, y + native w, h
        self.assertEqual(head["params"][26], X.output_scale_x10000(BIG[0]))
        self.assertEqual(head["params"][24], res["timings"]["headroom_x10000"])
        self.assertTrue(log and all(dims == (1600, 900) for dims, _d in log))
        self.assertEqual(res["output_wh"], [1600, 900])
        for k in ("shrink_s", "shrink_vm_peak_kb", "shrink_vm_hwm_kb", "shrink_mem_limit_kb",
                  "shrink_mem_available_kb", "dng_read_s", "rgb_prep_s"):
            self.assertIn(k, res["timings"])
        # the PPM the encoder read is the shrink of exactly this crop
        work = os.path.join(self.tmp, "w2")
        slim, _t = X.shrink_crop(self.dng, [0, 0, *BIG], X.coding_gains(X.colour_params(META)),
                                 work_dir=work, timeout_s=60, log=lambda *_: None)
        g = X.coding_gains(X.colour_params(META))
        want = X.rgb_codes_shrunk(self.mosaic, "BGGR", 64, 1023, g, slim["headroom_x10000"])
        with open(os.path.join(work, X.RGB_PPM), "rb") as fh:
            data = fh.read()
        got = np.frombuffer(data[data.index(b"4095\n") + 5:], ">u2").reshape(900, 1600, 3)
        self.assertTrue(np.array_equal(got, want))

    def test_a_native_crop_runs_no_child_and_scale_is_10000(self):
        def no_child(*a, **k):
            raise AssertionError("no shrink child at <= 1600x900")
        log = []
        res = self.encode([32, 18, 1600, 900], runner=_rgb_runner(log=log), shrink_runner=no_child)
        head, _p = X.unpack_container(res["blob"])
        self.assertEqual((head["w"], head["h"], head["params"][26]), (1600, 900, 10000))
        self.assertNotIn("shrink_s", res["timings"])
        self.assertEqual(log[0][0], (1600, 900))

    def test_the_search_prior_uses_the_coded_area(self):
        a, b = [], []
        self.encode([0, 0, *BIG], runner=_rgb_runner(log=a))
        self.encode([32, 18, 1600, 900], runner=_rgb_runner(log=b))
        self.assertEqual(a[0][1], b[0][1])          # same first distance: both code 1600x900

    def test_refusals(self):
        with self.assertRaises(X.RawFallback) as cm:                   # not 16:9
            self.encode([0, 0, 1664, 930])
        self.assertEqual(cm.exception.code, "err")
        with self.assertRaises(X.RawFallback) as cm:                   # the shrink is B3a only
            self.encode([0, 0, *BIG], cfg=dict(RGB_CFG, layout="bayer4"))
        self.assertEqual(cm.exception.code, "err")
        self.assertIn("B3a", cm.exception.detail)


# ---------------------------------------------------------------------------
# must do 5: the guards
# ---------------------------------------------------------------------------

def _fake_child(kind="ok", rc=0, result=None, seconds=1.0):
    def run(cmd, *, timeout_s, stdout_path, stderr_path, mem_kib=None):
        if result is not None:
            job = json.load(open(cmd[-1]))
            with open(os.path.join(job["work_dir"], X.SHRINK_RESULT), "w") as fh:
                json.dump(result, fh)
        return {"rc": rc, "kind": kind, "seconds": seconds, "peak_rss_kb": None}
    return run


class Guards(Tmp):
    def shrink(self, **kw):
        return X.shrink_crop(self.dng, [0, 0, *BIG], (1.6, 1.0, 2.4),
                             work_dir=os.path.join(self.tmp, "w"), timeout_s=kw.pop("timeout_s", 60),
                             log=lambda *_: None, **kw)

    def code(self, **kw):
        with self.assertRaises(X.RawFallback) as cm:
            self.shrink(**kw)
        return cm.exception

    def test_precheck_never_starts_the_child(self):
        exc = self.code(mem_available=lambda: 50 * 2 ** 20, min_avail_bytes=100 * 2 ** 20,
                        runner=_fake_child())
        self.assertEqual(exc.code, "shrink")
        self.assertIn("did not start", exc.detail)
        self.assertNotIn("shrink_s", exc.timings)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "w", X.SHRINK_JOB)))
        # off Linux (no /proc/meminfo) the pre-check is skipped, the child runs
        slim, t = self.shrink(mem_available=lambda: None, min_avail_bytes=2 ** 62)
        self.assertEqual(t["shrink_mem_available_kb"], None)

    def test_every_child_failure_maps_to_an_rfb(self):
        cases = [(dict(kind="mem", rc=-9), "shrink"),                       # OOM / RLIMIT kill
                 (dict(kind="enc", rc=1), "shrink"),                        # died, no result
                 (dict(kind="enc", rc=3, result={"rfb": "shrink", "detail": "MemoryError"}),
                  "shrink"),
                 (dict(kind="enc", rc=3, result={"rfb": "dng", "detail": "bad"}), "dng"),
                 (dict(kind="enc", rc=3, result={"rfb": "err", "detail": "x"}), "err"),
                 (dict(kind="time", rc=-9), "time")]
        for kw, want in cases:
            self.assertEqual(self.code(runner=_fake_child(**kw)).code, want, kw)
        self.assertEqual(self.code(timeout_s=0).code, "time")

    def test_the_real_child_on_a_bad_dng_is_dng(self):
        with open(self.dng, "wb") as fh:
            fh.write(b"not a dng")
        self.assertEqual(self.code().code, "dng")

    @unittest.skipUnless(sys.platform.startswith("linux"), "RLIMIT_AS is enforced on Linux only")
    def test_the_real_limit_on_linux(self):
        exc = self.code(mem_limit_bytes=X.SHRINK_FORCE_LIMIT_BYTES)
        self.assertEqual(exc.code, "shrink")

    def test_the_force_flag_is_one_shot(self):
        flag = os.path.join(self.tmp, "FORCE_SHRINK_FALLBACK")
        with mock.patch.object(X, "SHRINK_FORCE_FLAG", flag):
            with open(flag, "w") as fh:
                fh.write("avail\n")
            exc = self.code(mem_available=lambda: 400 * 2 ** 20)
            self.assertEqual(exc.code, "shrink")
            self.assertFalse(os.path.exists(flag))
            self.shrink(mem_available=lambda: 400 * 2 ** 20)            # next shrink: normal
            with open(flag, "w") as fh:
                fh.write("limit")
            seen = {}

            def spy(cmd, **kw):
                seen["mem_kib"] = kw["mem_kib"]
                return {"rc": -9, "kind": "mem", "seconds": 0.1, "peak_rss_kb": None}
            self.assertEqual(self.code(runner=spy).code, "shrink")
            self.assertEqual(seen["mem_kib"], X.SHRINK_FORCE_LIMIT_BYTES // 1024)
            self.assertFalse(os.path.exists(flag))

    def test_the_guard_wraps_the_child_under_its_own_limit(self):
        argv = X.guarded(["python3", "x"], mem_kib=1234)
        self.assertIn("ulimit -v 1234 ", argv[2])
        self.assertIn("oom_score_adj", argv[2])
        self.assertEqual(X.guarded(["cjxl"]), ["/bin/sh", "-c", X.GUARD_SH, "cjxl"])  # S2's


# ---------------------------------------------------------------------------
# must dos 4 + 5 end to end: the still action (bench, no BM bus)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.path.exists(NATIVE_JPEG), "synthetic native JPEG missing")
class StillAction(Tmp):
    CROP = (0, 0, *BIG)

    def run_action(self, runner=None):
        import rc_progressive_jpeg as rc
        from PIL import Image
        from rc_time_budget import CycleBudget
        images = os.path.join(self.tmp, "images")
        os.makedirs(images)
        cfg_path = os.path.join(self.tmp, "camera_schedule.yaml")
        with open(cfg_path, "w") as fh:
            fh.write('still_raw:\n  format: "nrjxl"\n  layout: "rgb"\n')
        stem = "2026-10-19T20:00:00Z_image"

        def raw_capture(settings, output_dir, capture_fn):
            native = os.path.join(output_dir, f"{stem}_native_full.jpg")
            shutil.copyfile(NATIVE_JPEG, native)
            dng = os.path.join(output_dir, f"{stem}_native_full.dng")
            shutil.copyfile(self.dng, dng)
            meta = os.path.join(output_dir, f"{stem}_native_full.metadata.json")
            with open(meta, "w") as fh:
                json.dump(META, fh)
            return native, {"metadata_json": meta}, stem, dng, None
        settings = {"budget_seconds": 600, "pacing_delay_seconds": 1.3,
                    "pacing_chunk_b64_chars": 384, "config_path": cfg_path,
                    "enforce_time_window": False, "timezone": "UTC", "window_start": "00:00",
                    "window_end": "23:59", "crop_native_xywh": self.CROP, "output_width": 1000,
                    "output_size": (1000, 562), "q_max": 90, "quality_ladder": [15, 13, 11, 9],
                    "message_cap": 195, "power_halt_enabled": False,
                    "power_halt_dry_run": True, "power_halt_mode": "halt",
                    "power_halt_script_path": "/nonexistent_halt.sh"}
        summary = {"command_events": []}
        with mock.patch.object(rc.shutil, "which", lambda n: "/usr/bin/" + n), \
                mock.patch.object(rc, "collect_storage_health", lambda: {}), \
                mock.patch("builtins.print"):
            rc.still_action(settings, summary, None, CycleBudget(600, 1.3), transmit=False,
                            capture_only=False, native_path=None, skip_time_window=True,
                            output_dir=images, capture_fn=None, bm_open_fn=None,
                            wake_fn=lambda **kw: None, sleep_fn=lambda s: None,
                            clock=__import__("time").monotonic, bm_commands_cfg={},
                            bench_commands=False, grid_clock_fn=None,
                            raw_capture_fn=raw_capture, raw_runner=runner or _rgb_runner())
        jpg = os.path.join(images, f"{stem}_compressed.jpg")
        side = json.load(open(jpg + ".capture_metadata.json"))
        with Image.open(jpg) as im:
            dims = im.size
        return summary, side, dims

    def expected_jpeg_dims(self):
        from rc_jpeg_encoder import prepare_source
        return prepare_source(NATIVE_JPEG, self.CROP, 1000).size

    def test_forced_precheck_sends_a_jpeg_of_the_same_crop(self):
        flag = os.path.join(self.tmp, "FORCE_SHRINK_FALLBACK")
        with open(flag, "w") as fh:
            fh.write("avail")
        with mock.patch.object(X, "SHRINK_FORCE_FLAG", flag), \
                mock.patch.object(X, "mem_available_bytes", lambda: 400 * 2 ** 20):
            summary, side, dims = self.run_action()
        self.assertEqual((summary["raw"]["fmt"], summary["raw"]["rfb"]), ("pjpg", "shrink"))
        self.assertEqual(side["raw_rfb"], "shrink")
        self.assertEqual(side["crop_native_xywh"], list(self.CROP))
        self.assertEqual(dims, self.expected_jpeg_dims())
        self.assertEqual(dims[0], 1000)

    def test_a_killed_child_sends_a_jpeg_of_the_same_crop(self):
        def killed(cmd, **kw):
            if "--shrink-child" in cmd:
                return {"rc": -9, "kind": "mem", "seconds": 0.5, "peak_rss_kb": None}
            raise AssertionError("cjxl must not run after a failed shrink")
        with mock.patch.object(X, "run_capped", killed):
            summary, side, dims = self.run_action(runner=killed)
        self.assertEqual((summary["raw"]["rfb"], side["raw_rfb"]), ("shrink", "shrink"))
        self.assertEqual(dims, self.expected_jpeg_dims())

    def test_a_good_shrink_sends_the_nrjxl(self):
        summary, side, dims = self.run_action()
        self.assertEqual((summary["raw"]["fmt"], summary["raw"]["rfb"]), ("nrjxl", None))
        self.assertIn("shrink_vm_peak_kb", summary["raw"]["timings"])


if __name__ == "__main__":
    unittest.main()
