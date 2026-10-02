#!/usr/bin/env python3
# filename: test_s27_retired_encoder_knobs.py
# description: Sprint27 F-G3-4 — video.record.encoder.denoise / .sharpness are retired; old values move to camera.image_processing.* on load, nothing bricks, rpicam-vid never gets a duplicate option.
"""
Sprint27 F-G3-4 (Nick 2026-10-02, "one owner per camera option"): camera.image_processing.denoise
/ .sharpness own --denoise / --sharpness for stills AND video. video.record.encoder.denoise /
.sharpness are retired (registry v7, config_registry.RETIRED).

Why the care: the v2 loader treats an unknown key as FATAL for the boot (config_v2.load_for_boot),
and the LKG fallback parses the same way. The bench YAMLs on bmcam003/004 carry both encoder keys
(denoise '' and sharpness 1.0), so a plain deletion would make every boot fall back.

Pins:
  - a v2 file with the retired keys loads STRICTLY (deploy) and as the boot's v2 level (not a
    fallback); a value moves to the image_processing key only if that key is unset; an empty
    value is dropped; every case leaves a note in cfg.warnings;
  - the bench configs of bmcam003/004 (runs/s4a_soak_20260928) load strict and their
    sharpness 1.0 moves to camera.image_processing.sharpness;
  - an LKG file written by registry v6 (with the retired keys) still loads;
  - tools/config_v2_upgrade.py --write rewrites such a file without the retired keys (same
    hash under today's registry); a second run has nothing to do;
  - the v1 reader moves video.encoder.* from a v1 file the same way;
  - a remote `set` of a retired key is refused e:key;
  - the rpicam-vid argv never carries --denoise / --sharpness twice, and carries them once
    from image_processing when the gates are on.

Run (repo root):
  python3 -m unittest tests.test_s27_retired_encoder_knobs -v
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402

BENCH = [os.path.join(REPO_ROOT, "runs", "s4a_soak_20260928", "pulled", f"bmcam00{n}_camera_config.yaml")
         for n in (3, 4)]
OLD = ("video.record.encoder.denoise", "video.record.encoder.sharpness")


def _yaml_with(extra_video_encoder, ip=None):
    """A minimal valid v2 YAML (registry defaults, video media) plus the retired keys."""
    d = R.defaults()
    d["mode.media"] = "video"
    if ip:
        d.update(ip)
    doc = R.nest(d)
    doc["schema"] = R.SCHEMA_VERSION
    doc["video"]["record"].setdefault("encoder", {}).update(extra_video_encoder)
    import yaml
    return yaml.safe_dump(doc, sort_keys=False)


class Load(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s27retire_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, text, name="camera_config.yaml"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_moves_when_unset_drops_when_set_or_empty(self):
        cfg = config_v2.load_config(self._write(_yaml_with({"denoise": "cdn_fast", "sharpness": 2.0})))
        self.assertEqual(cfg.base["camera.image_processing.denoise"], "cdn_fast")
        self.assertEqual(cfg.base["camera.image_processing.sharpness"], 2.0)
        self.assertEqual(sum("is retired" in w for w in cfg.warnings), 2, cfg.warnings)
        for old in OLD:
            self.assertNotIn(old, cfg.base)
        cfg = config_v2.load_config(self._write(_yaml_with(
            {"denoise": "cdn_fast", "sharpness": ""},
            ip={"camera.image_processing.denoise": "cdn_hq"})))
        self.assertEqual(cfg.base["camera.image_processing.denoise"], "cdn_hq")      # the owner wins
        self.assertIsNone(cfg.base["camera.image_processing.sharpness"])              # empty dropped
        self.assertTrue(any("already governs" in w for w in cfg.warnings), cfg.warnings)

    def test_boot_uses_the_v2_file_not_a_fallback(self):
        v2 = self._write(_yaml_with({"denoise": "", "sharpness": 1.0}))
        v1 = self._write("capture_mode: video\n", "camera_schedule.yaml")
        with contextlib.redirect_stdout(io.StringIO()):
            b = config_v2.load_for_boot(v1, v2, os.path.join(self.tmp, "lkg.json"))
        self.assertEqual(b.level, "v2", b.lines)
        self.assertEqual(b.values["camera.image_processing.sharpness"], 1.0)

    def test_bench_configs_load_strict(self):
        for path in BENCH:
            if not os.path.exists(path):
                self.skipTest(f"{path} not in this checkout")
            cfg = config_v2.load_config(path, strict=True)
            self.assertEqual(cfg.base["camera.image_processing.sharpness"], 1.0, path)
            self.assertIsNone(cfg.base["camera.image_processing.denoise"], path)
            self.assertTrue(any("is retired" in w for w in cfg.warnings), path)

    def test_lkg_from_registry_v6_loads(self):
        values = R.defaults()
        values.update({"mode.media": "video", "video.record.encoder.denoise": "",
                       "video.record.encoder.sharpness": 1.0})
        lkg = os.path.join(self.tmp, "lkg.json")
        with open(lkg, "w") as fh:
            json.dump({"saved_utc": "2026-10-01T00:00:00Z", "hash": "deadbeef", "registry": 6,
                       "values": values}, fh)
        v2 = self._write("schema: 2\nnot: [valid\n")                 # force the LKG level
        v1 = self._write("capture_mode: video\n", "camera_schedule.yaml")
        with contextlib.redirect_stdout(io.StringIO()):
            b = config_v2.load_for_boot(v1, v2, lkg)
        self.assertEqual(b.level, "lkg", b.lines)
        self.assertEqual(b.values["camera.image_processing.sharpness"], 1.0)

    def test_upgrade_tool_rewrites_without_retired_keys(self):
        path = self._write(_yaml_with({"denoise": "", "sharpness": 1.0},
                                      ip={"commands.state_path": os.path.join(self.tmp, "state.json")}))
        before = config_v2.load_config(path)
        tool = os.path.join(REPO_ROOT, "tools", "config_v2_upgrade.py")
        r = subprocess.run([sys.executable, tool, path, "--write"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("sharpness: 1.0\n    denoise", text)
        import yaml
        flat = R.flatten(yaml.safe_load(text))
        for old in OLD:
            self.assertNotIn(old, flat)
        after = config_v2.load_config(path)
        self.assertEqual(after.hash, before.hash)
        self.assertEqual(after.warnings, [])
        r = subprocess.run([sys.executable, tool, path], capture_output=True, text=True, timeout=120)
        self.assertIn("nothing to do", r.stdout)


class V1Reader(unittest.TestCase):
    def test_v1_encoder_values_move(self):
        import config_v1_reader
        src = os.path.join(REPO_ROOT, "device_profiles", "bmcam004", "live_20260925", "camera_schedule.yaml")
        if not os.path.exists(src):
            self.skipTest("bmcam004 live v1 profile not in this checkout")
        tmp = tempfile.mkdtemp(prefix="s27v1_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "camera_schedule.yaml")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("encoder:", text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        with contextlib.redirect_stdout(io.StringIO()):
            out = config_v1_reader.read_v1(path)
        for old in OLD:
            self.assertNotIn(old, out.values)
        self.assertTrue(any("is retired" in n for n in out.notes), out.notes)
        self.assertFalse([p for p in out.problems if "encoder" in p], out.problems)


class Command(unittest.TestCase):
    def test_set_of_a_retired_key_is_refused(self):
        from tests.test_s4_dispatch import Rig
        r = Rig(self, base_over={"mode.media": "video"})
        r.send({"id": 1_000_500, "c": "set", "kv": {"video.record.encoder.denoise": "cdn_off"}})
        ack = r.acks()[0]
        self.assertEqual((ack["ok"], ack.get("e")), (0, "key"))


class Argv(unittest.TestCase):
    def test_one_owner_per_option(self):
        import rc_progressive_jpeg as rc
        import video_recorder
        values = R.defaults()
        values.update({"mode.media": "video", "video.record.framing": "wide_720p",
                       "camera.controls_enabled": True, "camera.image_processing.enabled": True,
                       "camera.image_processing.denoise": "cdn_fast",
                       "camera.image_processing.sharpness": 2.0})
        tmp = tempfile.mkdtemp(prefix="s27argv_")
        self.addCleanup(shutil.rmtree, tmp, True)
        render = os.path.join(tmp, "render.yaml")
        with open(render, "w", encoding="utf-8") as fh:
            fh.write(config_v2.render_v1_text(values))
        with contextlib.redirect_stdout(io.StringIO()):
            vcfg = video_recorder.load_video_config(render)
            controls = rc._load_camera_controls_island(render)
            argv, _ = video_recorder.build_encoder_command({"capture_backend": "rpicam"}, vcfg,
                                                           os.path.join(tmp, "x.h264.part"),
                                                           binary="rpicam-vid", controls=controls)
        self.assertEqual(argv.count("--denoise"), 1, argv)
        self.assertEqual(argv.count("--sharpness"), 1, argv)
        self.assertEqual(argv[argv.index("--denoise") + 1], "cdn_fast")
        self.assertNotIn("denoise", vcfg["encoder"])


if __name__ == "__main__":
    unittest.main()
