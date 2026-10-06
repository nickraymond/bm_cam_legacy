#!/usr/bin/env python3
# filename: test_s28_lowgain_sunrise.py
# description: Sprint28 sunrise low-gain test tools on the desk: the unit loop (hil/tools/s28_lowgain_pair_loop.py) with a fake production capture on the mini DNG, then the analysis (hil_s28_lowgain_analyze.py) on its output.
"""
The fake replaces rc_capture.run_raw_capture_once only (the loop's config, crop, settings,
files, CSV and manifest code run for real). It writes the mini DNG, a metadata JSON whose
ExposureTime / AnalogueGain follow the low_gain rule, rpicam-like stderr with libcamera's
"tuning file" line, and a small JPEG. The app dir is a copy of BM_Devel_Pi with still.crop set
to the mini DNG's 160x96.

Run: .venv-dev/bin/python -m pytest -q tests/test_s28_lowgain_sunrise.py
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MINI = os.path.join(HERE, "fixtures", "s28", "mini.dng")
TOOLS = os.path.join(REPO, "hil", "tools")


def make_app(tmp):
    app = os.path.join(tmp, "app")
    shutil.copytree(os.path.join(REPO, "BM_Devel_Pi"), app,
                    ignore=shutil.ignore_patterns("__pycache__"))
    p = os.path.join(app, "camera_schedule.yaml")
    text = open(p).read()
    text = text.replace("  crop:\n    x: 1504\n    y: 846\n    w: 1600\n    h: 900\n",
                        "  crop:\n    x: 0\n    y: 0\n    w: 160\n    h: 96\n")
    text = text.replace("  output_width: 1000 ", "  output_width: 160  ")
    assert "w: 160" in text
    open(p, "w").write(text)
    return app


def fake_capture(command, native, w, h, q, log_prefix, settings=None, **_kw):
    from PIL import Image
    low = bool((settings or {}).get("exposure_profile"))
    meta = {"ExposureTime": 30000 if low else 20000, "AnalogueGain": 1.6 if low else 2.4,
            "DigitalGain": 1.0, "Lux": 35.0, "ColourGains": [1.66, 2.41], "ColourTemperature": 3800,
            "ColourCorrectionMatrix": [1.6, -0.4, -0.2, -0.37, 1.75, -0.39, 0.0, -0.59, 1.58]}
    with open(log_prefix + ".metadata.json", "w") as fh:
        json.dump(meta, fh)
    tf = ("/home/pi/BM_Devel_Pi/exposure_profile/tuning/imx708_wide_lowgain_s30000_g16.json"
          if low else "/usr/share/libcamera/ipa/rpi/vc4/imx708_wide.json")
    with open(log_prefix + ".stderr.log", "w") as fh:
        fh.write(f"[0:00:01] INFO RPI vc4.cpp:440 Registered camera; using tuning file {tf}\n")
    shutil.copyfile(MINI, os.path.splitext(native)[0] + ".dng")
    Image.new("RGB", (200, 120), (40, 90, 120)).save(native)
    info = {"capture_command": ["rpicam-still"], "stderr_log": log_prefix + ".stderr.log",
            "stdout_log": log_prefix + ".stdout.log", "metadata_json": log_prefix + ".metadata.json"}
    if low:
        info.update(exposure_profile="low_gain", exposure_profile_applied=True,
                    exposure_tuning_file_used=True)
    return info, os.path.splitext(native)[0] + ".dng", None


class SunriseTools(unittest.TestCase):
    def test_loop_then_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(tmp)
            out = os.path.join(tmp, "run")
            sys.path.insert(0, app)
            sys.path.insert(0, TOOLS)
            try:
                import rc_capture
                import rc_progressive_jpeg as P
                import s28_lowgain_pair_loop as L
                saved = rc_capture.run_raw_capture_once
                saved_sel = P._select_camera_command
                rc_capture.run_raw_capture_once = fake_capture
                P._select_camera_command = lambda backend: ("/usr/bin/rpicam-still", backend)
                saved_busy = L.busy
                L.busy = lambda: []     # host processes are not the unit's (tested separately)
                cwd = os.getcwd()
                try:
                    rc = L.main(["--out", out, "--app-dir", app, "--interval-s", "0",
                                 "--max-pairs", "2", "--min-free-mb", "1"])
                finally:
                    rc_capture.run_raw_capture_once = saved
                    P._select_camera_command = saved_sel
                    L.busy = saved_busy
                    os.chdir(cwd)
            finally:
                sys.path.remove(app)
                sys.path.remove(TOOLS)
            self.assertEqual(rc, 0)
            with open(os.path.join(out, "pairs.csv")) as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual([(r["pair"], r["profile"], r["ok"]) for r in rows],
                             [("1", "auto", "True"), ("1", "low_gain", "True"),
                              ("2", "auto", "True"), ("2", "low_gain", "True")])
            self.assertIn("exposure_profile/tuning", rows[1]["tuning_file_logged"])
            d = os.path.join(out, "pairs", "001_low_gain")
            self.assertTrue({"raw_crop.pgm", "raw_crop.json", "metadata.json", "isp_crop.jpg",
                             "capture_info.json", "stderr.log"} <= set(os.listdir(d)))
            self.assertFalse(any(n.endswith(".dng") for n in os.listdir(d)))   # DNG dropped
            with open(os.path.join(out, "run_manifest.json")) as fh:
                man = json.load(fh)
            self.assertEqual((man["pairs_captured"], man["crontab_unchanged"], man["still_crop"]),
                             (2, True, [0, 0, 160, 96]))
            self.assertEqual(man["low_gain"], {"profile": "low_gain", "max_shutter_us": 30000,
                                               "max_gain": 16.0})
            r = subprocess.run([sys.executable, os.path.join(TOOLS, "hil_s28_lowgain_analyze.py"),
                                out, "--sheet-pairs", "2"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(out, "analysis", "lowgain_summary.json")) as fh:
                s = json.load(fh)
            self.assertEqual((s["complete_pairs"], s["LG1"], s["LG2"], s["LG4"], s["PASS"]),
                             (2, "2/2", "2/2", "2/2", True))
            self.assertEqual(s["median_g_noise_ratio"], 1.0)          # same DNG both members
            self.assertTrue(os.path.exists(os.path.join(out, "analysis", "lowgain_cutsheet.png")))

    def test_rules_catch_a_violation(self):
        sys.path.insert(0, TOOLS)
        try:
            import hil_s28_lowgain_analyze as A
        finally:
            sys.path.remove(TOOLS)
        # gain raised while the shutter is well below the cap: LG2 must fail
        cap = 30000.0
        el, gl = 12000.0, 3.0
        self.assertFalse(not (gl > A.FLOOR * A.TOL_FLOOR) or el >= cap * A.TOL_AT_CAP)

    def test_loop_refuses_when_the_camera_is_busy(self):
        sys.path.insert(0, TOOLS)
        try:
            import s28_lowgain_pair_loop as L
        finally:
            sys.path.remove(TOOLS)
        with tempfile.TemporaryDirectory() as tmp:
            saved = L.busy
            L.busy = lambda: ["1234 /usr/bin/rpicam-still -o x.jpg"]
            try:
                self.assertEqual(L.main(["--out", os.path.join(tmp, "o"), "--app-dir", tmp]), 2)
            finally:
                L.busy = saved
            self.assertIn("REFUSING", open(os.path.join(tmp, "o", "loop.log")).read())


if __name__ == "__main__":
    unittest.main()
