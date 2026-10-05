#!/usr/bin/env python3
# filename: test_s28_low_gain.py
# description: Sprint28 low-gain exposure profile: the tuning-file patch (both rpi.agc shapes), sensor detection + cache, the fallback to today's capture, and the capture argv.
"""
Two kinds of tuning file: SYNTHETIC shapes (libcamera's rpi.agc layout: an "algorithms" list
of one-key dicts; rpi.agc either holds exposure_modes itself or a "channels" list of blocks
that do) and the REAL files of bmcam003 (tests/fixtures/s28/tuning/, read-only copies of
/usr/share/libcamera/ipa/rpi/vc4/imx708*.json, TE runs/s28_ladder_20261004, sha256
imx708_wide 400ca808...188b). Whether the AGC honours the patched mode is NOT provable here:
the bench (Tue) reads ExposureTime / AnalogueGain back from --metadata.
Also: the exposure_profile island (render <-> reader), the validate rule, the catalog, and
the rpicam argv (auto = today's command, byte for byte).

Run: .venv-dev/bin/python -m pytest -q tests/test_s28_low_gain.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import config_validate as V  # noqa: E402
import rc_capture  # noqa: E402
import rc_exposure_profile as E  # noqa: E402

FIXTURE_TUNING = os.path.join(HERE, "fixtures", "s28", "tuning")

NORMAL = {"shutter": [100, 15000, 30000, 60000, 66666], "gain": [1.0, 2.0, 4.0, 6.0, 8.0]}
SPORT = {"shutter": [100, 5000, 10000, 20000, 66666], "gain": [1.0, 2.0, 4.0, 6.0, 8.0]}


def tuning(channels):
    agc = {"metering_modes": {"centre-weighted": {"weights": [3, 3, 2, 2, 1]}},
           "constraint_modes": {"normal": [{"bound": "LOWER", "q_lo": 0.98, "q_hi": 1.0,
                                            "y_target": [0, 0.5, 1000, 0.5]}]}}
    block = {"exposure_modes": {"normal": dict(NORMAL), "short": dict(SPORT)}, **agc}
    rpi_agc = {"channels": [json.loads(json.dumps(block)) for _ in range(channels)]} \
        if channels else block
    return {"version": 2.0, "target": "bcm2835",
            "algorithms": [{"rpi.black_level": {"black_level": 4096}}, {"rpi.agc": rpi_agc},
                           {"rpi.awb": {"bayes": 1}}]}


def lister(model="imx708_wide"):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = (f"Available cameras\n-----------------\n0 : {model} [4608x2592 10-bit RGGB] "
               "(/base/soc/i2c0mux/i2c@1/imx708@1a)\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    return run, calls


class Patch(unittest.TestCase):
    def test_every_agc_channel_gets_the_low_gain_stages(self):
        for channels in (0, 1, 4):
            doc = tuning(channels)
            n = E.patch_low_gain(doc, 16667, 4.0)
            self.assertEqual(n, max(1, channels))
            agc = doc["algorithms"][1]["rpi.agc"]
            blocks = agc["channels"] if channels else [agc]
            for b in blocks:
                self.assertEqual(b["exposure_modes"]["normal"],
                                 {"shutter": [100, 16667], "gain": [1.0, 4.0]})
                self.assertEqual(b["exposure_modes"]["short"], SPORT)        # untouched
                self.assertIn("constraint_modes", b)
            self.assertEqual(doc["algorithms"][0], {"rpi.black_level": {"black_level": 4096}})

    def test_refuses_what_it_does_not_understand(self):
        for doc in ({"algorithms": []}, {"version": 2.0}, {"algorithms": [{"rpi.agc": {}}]},
                    {"algorithms": [{"rpi.agc": {"exposure_modes": {"short": SPORT}}}]}):
            with self.assertRaises(E.ProfileUnavailable):
                E.patch_low_gain(doc, 16667, 16.0)


class StillArgs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        self.sys = os.path.join(self.tmp.name, "sys")
        os.makedirs(self.sys)
        with open(os.path.join(self.sys, "imx708_wide.json"), "w") as fh:
            json.dump(tuning(1), fh)
        with open(os.path.join(self.sys, "imx708.json"), "w") as fh:
            json.dump(tuning(1), fh)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, exposure, run=None):
        run = run or lister()[0]
        return E.still_args(exposure, state_dir=self.state, run=run, tuning_dirs=(self.sys,),
                            log=lambda *_: None)

    def test_auto_is_todays_command(self):
        for exp in (None, {}, {"profile": "auto"}, {"profile": "AUTO", "max_shutter_us": 1000}):
            self.assertEqual(self.args(exp), ([], {}))

    def test_low_gain_adds_the_patched_tuning_file_for_the_detected_sensor(self):
        args, info = self.args({"profile": "low_gain", "max_shutter_us": 16667, "max_gain": 4})
        self.assertEqual(args[0], "--tuning-file")
        self.assertEqual(os.path.basename(args[1]), "imx708_wide_lowgain_s16667_g4.json")
        with open(args[1]) as fh:
            doc = json.load(fh)
        normal = doc["algorithms"][1]["rpi.agc"]["channels"][0]["exposure_modes"]["normal"]
        self.assertEqual(normal, {"shutter": [100, 16667], "gain": [1.0, 4.0]})
        self.assertTrue(info["exposure_profile_applied"])
        self.assertEqual(info["exposure_sensor_model"], "imx708_wide")

    def test_the_model_is_detected_once_and_cached(self):
        run, calls = lister("imx708")
        self.args({"profile": "low_gain"}, run)
        args, _ = self.args({"profile": "low_gain"}, run)
        self.assertEqual(len(calls), 1)
        self.assertEqual(os.path.basename(args[1]), "imx708_lowgain_s30000_g16.json")  # defaults

    def test_every_failure_is_todays_command_with_the_reason(self):
        def broken(cmd, **kw):
            raise OSError("no rpicam-hello")
        args, info = self.args({"profile": "low_gain"}, broken)
        self.assertEqual(args, [])
        self.assertFalse(info["exposure_profile_applied"])
        self.assertIn("rpicam-hello", info["exposure_profile_why"])
        run, _ = lister("imx999")                         # no such tuning file
        args, info = E.still_args({"profile": "low_gain"}, state_dir=self.state + "2", run=run,
                                  tuning_dirs=(self.sys,), log=lambda *_: None)
        self.assertEqual((args, info["exposure_profile_applied"]), ([], False))
        with open(os.path.join(self.sys, "imx708_wide.json"), "w") as fh:
            fh.write("{not json")
        args, info = self.args({"profile": "low_gain", "max_gain": 2.0})
        self.assertEqual((args, info["exposure_profile_applied"]), ([], False))
        self.assertIn("unreadable", info["exposure_profile_why"])

    def test_no_rpicam_hello_falls_back_to_rpicam_still_list(self):
        inner, calls = lister("imx708_wide")

        def run(cmd, **kw):
            if cmd[0] == "rpicam-hello":
                raise FileNotFoundError("rpicam-hello")
            return inner(cmd, **kw)
        args, info = self.args({"profile": "low_gain"}, run)
        self.assertTrue(info["exposure_profile_applied"])
        self.assertEqual(calls, [["rpicam-still", "--list-cameras"]])

    def test_the_copy_is_rebuilt_when_the_system_file_changes(self):
        args, _ = self.args({"profile": "low_gain"})
        first = os.path.getmtime(args[1])
        path = os.path.join(self.sys, "imx708_wide.json")
        os.utime(path, (first + 10, first + 10))
        args2, _ = self.args({"profile": "low_gain"})
        self.assertEqual(args, args2)
        self.assertGreater(os.path.getmtime(args2[1]), first)


class RealTuningFile(unittest.TestCase):
    """bmcam003's imx708_wide.json: rpi.agc with 3 channels, modes normal / short / long."""

    def load(self, name="imx708_wide"):
        with open(os.path.join(FIXTURE_TUNING, name + ".json")) as fh:
            return json.load(fh)

    def test_stock_normal_mode_is_what_the_finding_says(self):
        # the EM finding (2026-10-05): stock AGC already holds gain 1.0 until 30 ms
        ch0 = self.load()["algorithms"]
        agc = next(a["rpi.agc"] for a in ch0 if "rpi.agc" in a)
        self.assertEqual(len(agc["channels"]), 3)
        self.assertEqual(agc["channels"][0]["exposure_modes"]["normal"],
                         {"shutter": [100, 15000, 30000, 60000, 66666],
                          "gain": [1.0, 1.0, 2.0, 4.0, 16.0]})

    def test_patch_changes_only_the_normal_mode_of_every_channel(self):
        for name in ("imx708_wide", "imx708"):
            before, doc = self.load(name), self.load(name)
            self.assertEqual(E.patch_low_gain(doc, 16667, 16.0), 3, name)
            for a, b in zip(before["algorithms"], doc["algorithms"]):
                if "rpi.agc" not in a:
                    self.assertEqual(a, b)                       # every other algorithm
                    continue
                for ca, cb in zip(a["rpi.agc"]["channels"], b["rpi.agc"]["channels"]):
                    self.assertEqual(cb["exposure_modes"]["normal"],
                                     {"shutter": [100, 16667], "gain": [1.0, 16.0]})
                    for mode in ("short", "long"):
                        self.assertEqual(ca["exposure_modes"][mode], cb["exposure_modes"][mode])
                    rest = lambda c: {k: v for k, v in c.items() if k != "exposure_modes"}
                    self.assertEqual(rest(ca), rest(cb))
            self.assertEqual({k: v for k, v in before.items() if k != "algorithms"},
                             {k: v for k, v in doc.items() if k != "algorithms"})

    def test_built_copy_is_valid_json_with_the_stages(self):
        with tempfile.TemporaryDirectory() as d:
            path = E.build_tuning("imx708_wide", os.path.join(FIXTURE_TUNING, "imx708_wide.json"),
                                  d, 33333, 4.0)
            self.assertEqual(os.path.basename(path), "imx708_wide_lowgain_s33333_g4.json")
            with open(path) as fh:
                doc = json.load(fh)
        agc = next(a["rpi.agc"] for a in doc["algorithms"] if "rpi.agc" in a)
        self.assertEqual({json.dumps(c["exposure_modes"]["normal"]) for c in agc["channels"]},
                         {json.dumps({"shutter": [100, 33333], "gain": [1.0, 4.0]})})


def _values(**kv):
    v = R.defaults()
    v.update({"mode.media": "still"})
    v.update(kv)
    return v


class Config(unittest.TestCase):
    def test_registry_keys(self):
        self.assertGreaterEqual(R.REGISTRY_VERSION, 9)
        self.assertEqual(R.BY_PATH["camera.exposure.profile"].enum, ("auto", "low_gain"))
        self.assertEqual(R.BY_PATH["camera.exposure.profile"].default, "auto")
        self.assertEqual(R.BY_PATH["camera.exposure.max_shutter_us"].default, 30000)
        self.assertEqual(R.BY_PATH["camera.exposure.max_shutter_us"].range, (100, 66666))
        self.assertEqual(R.BY_PATH["camera.exposure.max_gain"].default, 16.0)
        self.assertEqual(R.BY_PATH["camera.exposure.max_gain"].range, (1.0, 16.0))
        self.assertEqual(E.DEFAULT_CONFIG, {"profile": "auto", "max_shutter_us": 30000,
                                            "max_gain": 16.0})

    def test_auto_renders_no_island_and_ignores_the_caps(self):
        base = config_v2.render_v1_text(_values())
        self.assertNotIn("exposure_profile", base)
        self.assertEqual(config_v2.render_v1_text(_values(**{
            "camera.exposure.max_shutter_us": 1000, "camera.exposure.max_gain": 2.0})), base)

    def test_island_round_trip(self):
        text = config_v2.render_v1_text(_values(**{
            "camera.exposure.profile": "low_gain", "camera.exposure.max_shutter_us": 33333,
            "camera.exposure.max_gain": 4}))
        self.assertIn('exposure_profile:\n  profile: "low_gain"\n  max_shutter_us: 33333\n'
                      '  max_gain: 4.0\n', text)
        # a top-level island, outside image_pipeline.camera_controls: the v8 exp command
        # (which replaces that block) and the switches cannot drop it
        self.assertIn("\nexposure_profile:\n", text)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camera_schedule.yaml")
            with open(path, "w") as fh:
                fh.write(text)
            self.assertEqual(E.load_profile_config(path),
                             {"profile": "low_gain", "max_shutter_us": 33333, "max_gain": 4.0})
            import config_v1_reader
            got = config_v1_reader.read_v1(path)
        self.assertEqual((got.values["camera.exposure.profile"],
                          got.values["camera.exposure.max_shutter_us"],
                          got.values["camera.exposure.max_gain"]), ("low_gain", 33333, 4.0))

    def test_island_refusals_name_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.yaml")
            for body, key in (('  profile: "dim"\n', "profile"),
                              ("  max_shutter_us: 99\n", "max_shutter_us"),
                              ("  max_shutter_us: 70000\n", "max_shutter_us"),
                              ("  max_gain: 0.5\n", "max_gain"),
                              ("  max_gain: nan\n", "max_gain"),
                              ("  max_gain: lots\n", "max_gain")):
                with open(path, "w") as fh:
                    fh.write("exposure_profile:\n" + body)
                with self.assertRaises(ValueError) as cm:
                    E.load_profile_config(path)
                self.assertIn(key, str(cm.exception))
            self.assertEqual(E.load_profile_config(os.path.join(d, "missing.yaml"))["profile"],
                             "auto")

    def test_low_gain_refuses_a_fixed_shutter_or_gain_only_when_it_applies(self):
        def errs(**kv):
            return [v for v in V.validate(_values(**kv), "effective") if v.code == "xk"]
        on = {"camera.controls_enabled": True, "camera.exposure.enabled": True}
        self.assertEqual(errs(**{"camera.exposure.profile": "low_gain"}), [])
        self.assertEqual(errs(**on, **{"camera.exposure.profile": "low_gain",
                                       "camera.exposure.ev": -1.0}), [])     # EV composes
        e = errs(**on, **{"camera.exposure.profile": "low_gain",
                          "camera.exposure.shutter_us": 8000})
        self.assertEqual(e[0].paths, ("camera.exposure.profile", "camera.exposure.shutter_us"))
        e = errs(**on, **{"camera.exposure.profile": "low_gain", "camera.exposure.shutter_us": 1,
                          "camera.exposure.analogue_gain": 2.0})
        self.assertIn("shutter_us / analogue_gain", e[0].message)
        # switches off: the fixed values build no flag, nothing to refuse; auto: no rule
        self.assertEqual(errs(**{"camera.exposure.profile": "low_gain",
                                 "camera.exposure.shutter_us": 8000}), [])
        self.assertEqual(errs(**on, **{"camera.exposure.shutter_us": 8000}), [])

    def test_catalog_marks_them_writable_and_ungated(self):
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
        import gen_config_catalog as C
        for p in ("camera.exposure.profile", "camera.exposure.max_shutter_us",
                  "camera.exposure.max_gain"):
            self.assertIn(p, C.CONTROL_KEYS)
            self.assertEqual(C._requires(p), [])
        self.assertEqual(C._requires("camera.exposure.ev"),
                         ["camera.controls_enabled", "camera.exposure.enabled"])


class StillSettings(unittest.TestCase):
    """rc_progressive_jpeg._capture_settings: what both still captures hand rc_capture."""

    def run_with(self, body):
        import rc_progressive_jpeg as P
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.yaml")
            with open(path, "w") as fh:
                fh.write(body)
            return P._capture_settings({"config_path": path, "camera_controls_override": None})

    def test_no_island_is_todays_none(self):
        self.assertIsNone(self.run_with("capture_mode: progressive_jpeg\n"))
        self.assertIsNone(self.run_with('exposure_profile:\n  profile: "auto"\n'))

    def test_low_gain_joins_the_controls(self):
        got = self.run_with('exposure_profile:\n  profile: "low_gain"\n  max_gain: 4.0\n')
        self.assertEqual(got, {"exposure_profile": {"profile": "low_gain",
                                                    "max_shutter_us": 30000, "max_gain": 4.0}})

    def test_an_unreadable_island_captures_as_today(self):
        self.assertIsNone(self.run_with('exposure_profile:\n  profile: "dim"\n'))


class CaptureArgv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (E.SYSTEM_TUNING_DIRS, E.DEFAULT_STATE_DIR, E.subprocess.run)
        E.SYSTEM_TUNING_DIRS = (FIXTURE_TUNING,)
        E.DEFAULT_STATE_DIR = os.path.join(self.tmp.name, "exposure_profile")
        E.subprocess.run = lister()[0]

    def tearDown(self):
        E.SYSTEM_TUNING_DIRS, E.DEFAULT_STATE_DIR, E.subprocess.run = self.saved
        self.tmp.cleanup()

    def argv(self, settings):
        return rc_capture.native_capture_command("rpicam-still", "/i/n.jpg", 4608, 2592, 95,
                                                 "/i/n.json", settings)

    def test_auto_is_todays_argv(self):
        controls = {"enabled": True, "focus": {"enabled": True, "mode": "manual",
                                               "lens_position": 1.82}}
        for settings in (None, {"camera_controls": controls}):
            cmd, args, req = self.argv(settings)
            self.assertNotIn("--tuning-file", cmd)
            self.assertFalse(any(k.startswith("exposure_") for k in req))
        self.assertEqual(self.argv({"camera_controls": controls}),
                         self.argv({"camera_controls": controls, "exposure_profile": None}))

    def test_low_gain_adds_the_tuning_file_before_the_output(self):
        cmd, args, req = self.argv({"exposure_profile": {"profile": "low_gain",
                                                         "max_shutter_us": 16667,
                                                         "max_gain": 16.0}})
        i = cmd.index("--tuning-file")
        self.assertTrue(cmd[i + 1].endswith("tuning/imx708_wide_lowgain_s16667_g16.json"))
        self.assertEqual(cmd[-2:], ["-o", "/i/n.jpg"])
        self.assertEqual(args[-2:], cmd[i:i + 2])           # a camera control: retry drops it
        self.assertEqual(rc_capture._without_camera_control_args(cmd).count("--tuning-file"), 0)
        f = rc_capture.exposure_fields(req, cmd)
        self.assertTrue(f["exposure_profile_applied"] and f["exposure_tuning_file_used"])
        self.assertFalse(rc_capture.exposure_fields(
            req, rc_capture._without_camera_control_args(cmd))["exposure_tuning_file_used"])

    def test_a_broken_profile_is_todays_argv_with_the_reason(self):
        E.SYSTEM_TUNING_DIRS = (os.path.join(self.tmp.name, "none"),)
        cmd, args, req = self.argv({"exposure_profile": {"profile": "low_gain"}})
        self.assertNotIn("--tuning-file", cmd)
        self.assertFalse(req["exposure_profile_applied"])
        self.assertIn("imx708_wide.json", req["exposure_profile_why"])


if __name__ == "__main__":
    unittest.main()
