#!/usr/bin/env python3
# filename: test_s28_progressive.py
# description: Progressive JPEG XL previews (Nick 2026-10-09): still.raw.progressive puts cjxl -p in the B3a argv (never in bayer4's), the byte search still lands <= 56,160 B (195 x 288 B), the island / registry plumbing, and a real-cjxl check of the +bytes cost on the production-geometry fixture.
"""
Desk study (EM worktree runs/jxl_progressive_20261009, docs/wire/
NOTE_progressive_jxl_preview_2026-10-09.md): -p (= --qprogressive_ac) costs +1.6 % bytes
at equal distance (max +2.7 %) and makes the 75 %-prefix flush near-final; the byte
search absorbs the bytes, so the wake's chunk count is unchanged. The real-codec class is
skipped when cjxl / djxl are absent.

Run: .venv-dev/bin/python -m pytest -q tests/test_s28_progressive.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import rc_raw_jxl as X  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "s28")
MINI = os.path.join(FIX, "mini.dng")
V2_BLOB = os.path.join(FIX, "blob_v2_bmcam004_57521.nrjxl")
META = {"ExposureTime": 103005, "AnalogueGain": 1.122807, "ColourGains": [1.6634, 2.4142],
        "ColourCorrectionMatrix": [1.607722, -0.415966, -0.191762, -0.367844, 1.754623,
                                   -0.386781, 0.005148, -0.587414, 1.582275],
        "SensorTemperature": 31.0, "DigitalGain": 1.000063, "ColourTemperature": 3771}
RGB_CFG = dict(X.DEFAULT_CONFIG, format="nrjxl", layout="rgb")
CAP_MSGS, CHUNK_CHARS = 195, 384            # production: 195 chunks of 384 b64 chars
CAP_BYTES = CAP_MSGS * 3 * CHUNK_CHARS // 4  # = 56,160 B


class Budget:
    def __init__(self, remaining=480.0, pace=1.3):
        self.left, self.seconds_per_message = remaining, pace

    def remaining_s(self):
        return self.left

    def messages_fit(self, n):
        return self.left >= n * self.seconds_per_message


def _runner(scale, argv_log):
    """Fake cjxl: bytes = scale x 54 kB x (d / 2.6) ** -0.85, x 1.016 with -p (the desk
    study's median overhead at equal distance). Records every argv."""
    def run(cmd, *, timeout_s, stdout_path, stderr_path):
        argv_log.append(list(cmd))
        d = float(cmd[cmd.index("-d") + 1])
        n = scale * 54000 * (d / 2.6) ** -0.85 * (1.016 if "-p" in cmd else 1.0)
        with open(cmd[2], "wb") as fh:
            fh.write(b"\xff\x0a" + b"x" * int(n))
        return {"rc": 0, "kind": "ok", "seconds": 6.0, "peak_rss_kb": 110000}
    return run


def _encode(cfg, runner):
    with tempfile.TemporaryDirectory() as d:
        return X.encode_still(MINI, META, cfg, crop_xywh=[0, 0, 160, 96], budget=Budget(),
                              message_cap=CAP_MSGS, chunk_b64_chars=CHUNK_CHARS,
                              work_dir=os.path.join(d, "w"), cjxl="/usr/bin/cjxl",
                              runner=runner, log=lambda *_: None)


class Argv(unittest.TestCase):
    def test_rgb_command_carries_p_only_when_asked(self):
        on = X.cjxl_rgb_command("cjxl", "a.ppm", "b.jxl", 2.6, 5, progressive=True)
        off = X.cjxl_rgb_command("cjxl", "a.ppm", "b.jxl", 2.6, 5)
        self.assertEqual(on, ["cjxl", "a.ppm", "b.jxl", "-m", "0", "-e", "5", "-d", "2.6000",
                              "-p", "--num_threads=0"])
        self.assertEqual([a for a in on if a != "-p"], off)

    def test_default_is_on(self):
        self.assertIs(X.DEFAULT_CONFIG["progressive"], True)

    def test_every_rgb_attempt_is_progressive(self):
        argv = []
        res = _encode(RGB_CFG, _runner(1.0, argv))
        self.assertGreaterEqual(res["attempts"], 1)
        self.assertEqual(len(argv), res["attempts"])
        self.assertTrue(all("-p" in a and a[a.index("-m") + 1] == "0" for a in argv), argv)

    def test_off_drops_the_flag(self):
        argv = []
        _encode(dict(RGB_CFG, progressive=False), _runner(1.0, argv))
        self.assertTrue(argv and not any("-p" in a for a in argv), argv)

    def test_bayer4_never_gets_p(self):
        # -m 1 -p is the modular squeeze (first flush at 76 %, -4.9 s2): not wanted
        argv = []
        _encode(dict(RGB_CFG, layout="bayer4"), _runner(0.2, argv))
        self.assertTrue(argv and all(a[a.index("-m") + 1] == "1" and "-p" not in a
                                     for a in argv), argv)


class SearchStillFits(unittest.TestCase):
    def test_lands_at_or_under_56160_bytes_across_scene_scales(self):
        self.assertEqual(CAP_BYTES, 56160)
        for scale in (0.55, 0.8, 1.0, 1.3, 1.9):     # smooth .. busy scenes
            with self.subTest(scale=scale):
                argv = []
                res = _encode(RGB_CFG, _runner(scale, argv))
                self.assertLessEqual(len(res["blob"]), CAP_BYTES)
                self.assertLessEqual(res["message_count"], CAP_MSGS)
                self.assertGreaterEqual(res["message_count"], int(0.90 * CAP_MSGS))
                self.assertLessEqual(res["attempts"], X.SEARCH_MAX_ENCODES)
                self.assertEqual(X.message_count(len(res["blob"]), CHUNK_CHARS),
                                 res["message_count"])


class Config(unittest.TestCase):
    def test_registry_key(self):
        self.assertGreaterEqual(R.REGISTRY_VERSION, 13)
        k = R.BY_PATH["still.raw.progressive"]
        self.assertEqual((k.type, k.default), (R.BOOL, True))

    def test_island_writes_progressive_only_when_off(self):
        v = R.defaults()
        v.update({"mode.media": "still", "still.format": "nrjxl", "still.raw.layout": "rgb"})
        text = config_v2.render_v1_text(v)
        self.assertNotIn("  progressive:", text)
        v["still.raw.progressive"] = False
        text = config_v2.render_v1_text(v)
        self.assertIn("  progressive: false\n", text)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.yaml")
            open(path, "w").write(text)
            self.assertIs(X.load_raw_config(path)["progressive"], False)
            open(path, "w").write('still_raw:\n  format: "nrjxl"\n')
            self.assertIs(X.load_raw_config(path)["progressive"], True)
            open(path, "w").write('still_raw:\n  progressive: maybe\n')
            with self.assertRaises(ValueError):
                X.load_raw_config(path)

    def test_a_pjpg_unit_renders_no_island(self):
        # the v1 doctrine: an all-default still_raw renders nothing (byte-identical config)
        self.assertNotIn("still_raw", config_v2.render_v1_text(R.defaults()))


@unittest.skipUnless(shutil.which("cjxl") and shutil.which("djxl"), "cjxl/djxl not installed")
class RealCodec(unittest.TestCase):
    """The production-geometry fixture (bmcam004 57521, 1600x900 B3a codes) re-encoded with
    the real cjxl: the -p cost at equal distance, and the real search with -p fits."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with open(V2_BLOB, "rb") as fh:
            cls.head, (payload,) = X.unpack_container(fh.read())
        jxl = os.path.join(cls.tmp.name, "src.jxl")
        open(jxl, "wb").write(payload)
        cls.ppm = os.path.join(cls.tmp.name, X.RGB_PPM)
        subprocess.run(["djxl", jxl, cls.ppm, "--quiet"], check=True)
        with open(cls.ppm, "rb") as fh:
            assert fh.read(32).split(b"\n")[2] == b"4095", "djxl must keep the 12-bit codes"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _bytes_at(self, d, progressive):
        work = tempfile.mkdtemp(dir=self.tmp.name)
        shutil.copy(self.ppm, os.path.join(work, X.RGB_PPM))
        (data,), _ = X.encode_rgb(d, 5, work, cjxl=shutil.which("cjxl"), runner=X.run_capped,
                                  timeout_s=120, progressive=progressive)
        return len(data)

    def test_p_costs_a_few_percent_at_equal_distance(self):
        plain, prog = self._bytes_at(3.457, False), self._bytes_at(3.457, True)
        ratio = prog / plain
        print(f"\n[PROG] d=3.457 plain {plain} B, -p {prog} B, x{ratio:.4f}")
        self.assertGreater(ratio, 1.0)
        self.assertLess(ratio, 1.05)          # desk study: median +1.6 %, max +2.7 %

    def test_real_search_with_p_fits_the_195_chunk_room(self):
        h = self.head
        crop = {"cfa": h["cfa"], "black": h["black"], "white": h["white"],
                "native_w": h["params"][3], "native_h": h["params"][4],
                "w": h["w"], "h": h["h"], "headroom_x10000": h["params"][24]}
        work = tempfile.mkdtemp(dir=self.tmp.name)
        shutil.copy(self.ppm, os.path.join(work, X.RGB_PPM))
        argv = []

        def runner(cmd, **kw):
            argv.append(list(cmd))
            return X.run_capped(cmd, **kw)
        res = X.choose_rate(crop, None, X.colour_params(META), RGB_CFG,
                            crop_xywh=[1504, 846, 1600, 900], budget=Budget(),
                            message_cap=CAP_MSGS, chunk_b64_chars=CHUNK_CHARS, reserve_msgs=0,
                            fallback_msgs=0, work_dir=work, cjxl=shutil.which("cjxl"),
                            runner=runner, log=lambda *_: None)
        print(f"\n[PROG] search: d={res['distance']} {len(res['blob'])} B "
              f"{res['message_count']} msgs in {res['attempts']} encodes")
        self.assertTrue(argv and all("-p" in a for a in argv))
        self.assertLessEqual(len(res["blob"]), CAP_BYTES)
        self.assertLessEqual(res["message_count"], CAP_MSGS)
        self.assertGreaterEqual(res["message_count"], int(0.90 * CAP_MSGS))
        self.assertLessEqual(res["attempts"], X.SEARCH_MAX_ENCODES)


if __name__ == "__main__":
    unittest.main()
