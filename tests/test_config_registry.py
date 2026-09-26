#!/usr/bin/env python3
# filename: test_config_registry.py
# description: Sprint26 S2a — config v2 registry integrity, v1 key coverage, absent-key defaults, reader refusals.
"""
Pins Sprint26 S2a (DESIGN_supervisor.md §5.1, §8.3 S2a; PLAN_S2.md):

  registry     unique paths; every default passes its own check; file paths are
               LOCKED; network_type/chunk_chars are SERVICE; no key segment ends
               in len/length/chunks/buffer(s)/filename; short names unique;
               presets are valid values and match the v8 tables they replace
  coverage     every key in every YAML in the repo (unit profiles, live pulls,
               examples) is mapped to a registry key or explicitly removed; every
               registry key names its v1 source
  defaults     registry default == what the key's OWN v1 loader returns when the
               key/block is absent (a whole-file check and a block-by-block check
               on the bmcam003 profile); deliberate exceptions are listed
  reader       the stop conditions (heic/missing capture_mode, network_type 0x01 by
               absence, media_gid, unknown key, duplicate block, mirror flags that
               disagree, quoted bools, junk control values, missing PyYAML)

Needs PyYAML (the v1 loaders under test use it): run in .venv-dev.
Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_config_registry.py
"""

import glob
import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "BM_Devel_Pi")
if APP not in sys.path:
    sys.path.insert(0, APP)

import yaml  # noqa: E402  (hard requirement, like the goldens)

import command_tables as T  # noqa: E402
import config_registry as R  # noqa: E402
import config_v1_reader as V  # noqa: E402

BMCAM003 = os.path.join(REPO, "device_profiles", "bmcam003", "camera_schedule.yaml")


def all_repo_yamls():
    """Every v1 camera YAML in the repo, live pulls included."""
    paths = [os.path.join(APP, "camera_schedule.yaml"),
             os.path.join(APP, "camera_schedule_large_cellular_example.yaml")]
    paths += glob.glob(os.path.join(REPO, "device_profiles", "*.yaml"))
    paths += glob.glob(os.path.join(REPO, "device_profiles", "*", "camera_schedule.yaml"))
    paths += glob.glob(os.path.join(REPO, "device_profiles", "*", "live_*", "camera_schedule.yaml"))
    return sorted(p for p in paths if os.path.exists(p))


class Tmp:
    def __init__(self, case):
        self.dir = tempfile.TemporaryDirectory()
        case.addCleanup(self.dir.cleanup)

    def yaml(self, text, name="camera_schedule.yaml"):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(text))
        return path


class TestRegistryIntegrity(unittest.TestCase):
    def test_paths_unique_and_well_formed(self):
        paths = [k.path for k in R.KEYS]
        self.assertEqual(len(paths), len(set(paths)))
        for p in paths:
            self.assertRegex(p, r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", p)
            # A path is never also a group (a leaf can't have children).
            self.assertFalse(any(q.startswith(p + ".") for q in paths), p)

    def test_no_forbidden_segment_suffix(self):
        for k in R.KEYS:
            for seg in k.path.split("."):
                self.assertFalse(seg.endswith(R.FORBIDDEN_SEGMENT_SUFFIXES),
                                 f"{k.path}: segment {seg!r}")

    def test_every_default_is_valid(self):
        for k in R.KEYS:
            if k.required:
                self.assertIsNone(k.default, k.path)
                continue
            self.assertIsNone(R.check_value(k, k.default), k.path)

    def test_guards(self):
        for k in R.KEYS:
            self.assertIn(k.guard, (R.NONE, R.GUARDED_REVERT, R.GUARDED_STAGE, R.LOCKED,
                                    R.SERVICE), k.path)
            if k.type == R.PATH and k.path != "uplink.uart.port":
                self.assertEqual(k.guard, R.LOCKED, f"{k.path}: file paths are locked (N4)")
        self.assertEqual(R.BY_PATH["uplink.network_type"].guard, R.SERVICE)
        self.assertEqual(R.BY_PATH["uplink.chunk_chars"].guard, R.SERVICE)
        self.assertEqual(R.BY_PATH["power.halt.enabled"].guard, R.GUARDED_STAGE)
        for p in ("uplink.uart.port", "uplink.uart.baudrate", "commands.enabled",
                  "commands.topic", "mode.output"):
            self.assertEqual(R.BY_PATH[p].guard, R.GUARDED_REVERT, p)

    def test_short_names_unique_and_kickoff_letters(self):
        shorts = [k.short for k in R.KEYS if k.short]
        self.assertEqual(len(shorts), len(set(shorts)))
        for s in shorts:
            self.assertTrue(1 <= len(s) <= 3, s)
        # O7: KICKOFF §5 letters r m d f b e keep their meaning.
        by_short = {k.short: k.path for k in R.KEYS if k.short}
        self.assertEqual(by_short["m"], "mode.media")
        self.assertEqual(by_short["d"], "video.send.duration_s")
        self.assertEqual(by_short["f"], "camera.focus.mode")
        self.assertEqual(by_short["b"], "camera.white_balance.mode")
        self.assertEqual(by_short["e"], "camera.exposure.ev")

    def test_presets_are_valid_values(self):
        for k in R.KEYS:
            for label, value in k.presets:
                if value is None and k.nullable:
                    continue
                self.assertIsNone(R.check_value(k, value), f"{k.path} preset {label!r}")

    def test_presets_match_the_v8_tables_they_replace(self):
        crops = [v for _, v in R.BY_PATH["still.crop"].presets]
        self.assertEqual(crops, [list(T.ROI_TABLE[i]["crop"]) for i in sorted(T.ROI_TABLE)])
        lens = [v for _, v in R.BY_PATH["camera.focus.lens_position"].presets]
        self.assertEqual(lens, [T.FOC_TABLE[i]["lens_position"] for i in sorted(T.FOC_TABLE)
                                if T.FOC_TABLE[i]["lens_position"] is not None])
        evs = [v for _, v in R.BY_PATH["camera.exposure.ev"].presets]
        self.assertEqual(evs, [T.EXP_TABLE[i]["ev"] for i in sorted(T.EXP_TABLE)])
        caps = [v for _, v in R.BY_PATH["still.message_cap"].presets]
        self.assertEqual(caps, [T.CAP_TABLE[i]["messages"] for i in sorted(T.CAP_TABLE)])
        wins = [v for _, v in R.BY_PATH["still.budget_min"].presets]
        self.assertEqual(wins, [T.WIN_TABLE[i]["minutes"] for i in sorted(T.WIN_TABLE)])

    def test_nest_flatten_round_trip(self):
        d = R.defaults()
        self.assertEqual(R.flatten(R.nest(d)), d)
        self.assertEqual(list(R.nest(d)), R.groups())

    def test_check_value_is_strict(self):
        k = R.BY_PATH
        self.assertIsNotNone(R.check_value(k["time.timeout_s"], True))       # bool-as-int
        self.assertIsNotNone(R.check_value(k["time.timeout_s"], "60"))       # str-as-int
        self.assertIsNotNone(R.check_value(k["uplink.msg_interval_s"], float("nan")))
        self.assertIsNotNone(R.check_value(k["uplink.network_type"], 3))
        self.assertIsNotNone(R.check_value(k["uplink.network_type"], True))
        self.assertIsNotNone(R.check_value(k["schedule.window.start"], "8:00"))
        self.assertIsNotNone(R.check_value(k["still.quality_ladder"], [90, 90, 80]))
        self.assertIsNotNone(R.check_value(k["still.crop"], [0, 0, 100]))
        self.assertIsNotNone(R.check_value(k["camera.white_balance.gains"], [1.5]))
        self.assertIsNotNone(R.check_value(k["mode.media"], None))           # required
        self.assertIsNone(R.check_value(k["camera.focus.mode"], None))       # nullable
        self.assertIsNone(R.check_value(k["video.send.size"], "480x270"))


class TestCoverage(unittest.TestCase):
    def test_every_repo_yaml_key_is_mapped_or_removed(self):
        yamls = all_repo_yamls()
        self.assertGreaterEqual(len(yamls), 7)
        for path in yamls:
            got = V.read_v1(path)
            unknown = [p for p in got.problems if p.startswith("unknown v1 key")]
            self.assertEqual(unknown, [], os.path.relpath(path, REPO))

    def test_every_registry_key_names_a_v1_source(self):
        for k in R.KEYS:
            if k.path in ("mode.run", "mode.output"):
                continue          # new in v2: v1 had only per_boot + transmit
            self.assertTrue(k.v1_sources, k.path)

    def test_unit_profiles_read_without_problems(self):
        # bmcam000-003 + the template: the files a migration will actually meet.
        for unit in ("bmcam000", "bmcam001", "bmcam002", "bmcam003", "rc_field_template"):
            path = os.path.join(REPO, "device_profiles", unit, "camera_schedule.yaml")
            got = V.read_v1(path)
            self.assertEqual(got.problems, [], unit)
            self.assertEqual(sorted(got.values), sorted(R.BY_PATH), unit)

    def test_reader_agrees_with_the_s1_settings_goldens(self):
        # The goldens were recorded by the v1 loaders at S1; the reader must see
        # the same effective values (spot checks across every loader family).
        import json
        gold = os.path.join(REPO, "tests", "golden", "settings",
                            "device_profiles__bmcam003__camera_schedule.yaml", "settings.json")
        with open(gold, encoding="utf-8") as fh:
            g = json.load(fh)
        v = V.read_v1(BMCAM003).values
        res = g["resolved"]
        self.assertEqual(v["still.crop"], res["crop_native_xywh"])
        self.assertEqual(v["still.quality_ladder"], res["quality_ladder"])
        self.assertEqual(v["still.budget_min"], res["max_run_time_min"])
        self.assertEqual(v["uplink.chunk_chars"], res["pacing_chunk_b64_chars"])
        self.assertEqual(v["uplink.msg_interval_s"], res["pacing_delay_seconds"])
        self.assertEqual(v["power.halt.enabled"], res["power_halt_enabled"])
        self.assertEqual(v["schedule.timezone"], res["timezone"])
        self.assertEqual(v["uplink.network_type"], int(g["network_type"], 16))
        self.assertEqual([v["uplink.uart.port"], v["uplink.uart.baudrate"]], g["uart"])
        self.assertEqual(v["commands.listen_tail_s"], g["bm_commands"]["post_transmit_listen_s"])
        self.assertEqual(v["camera.focus.lens_position"],
                         g["camera_controls_island"]["focus"]["lens_position"])
        self.assertEqual(v["video.send.size"], g["video_tx"]["output"])
        self.assertEqual(v["uplink.lane.grid_s"], g["transmit_phase"]["grid_seconds"])


class TestAbsentKeyDefaults(unittest.TestCase):
    """Registry default == v1 absent-key behaviour, so a hand-written v2 file
    missing a key behaves like a v1 file missing it (§5.1 Defaults)."""

    def assert_defaults(self, values, paths, why):
        defaults = R.defaults()
        for p in paths:
            if p in R.DEFAULT_EXCEPTIONS or p == "mode.media":
                continue
            self.assertIn(p, values, f"{why}: {p} not read")
            self.assertEqual(values[p], defaults[p], f"{why}: {p}")

    def test_near_empty_file(self):
        tmp = Tmp(self)
        path = tmp.yaml('capture_mode: "progressive_jpeg"\n')
        got = V.read_v1(path)
        self.assert_defaults(got.values, R.BY_PATH, "near-empty file")
        # The exceptions really are exceptions (documented, not accidental).
        self.assertEqual(got.values["uplink.network_type"], 1)
        self.assertTrue(any("network_type is absent" in p for p in got.problems))

    def test_each_block_missing_from_bmcam003(self):
        with open(BMCAM003, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        tmp = Tmp(self)
        for block in doc:
            if block == "capture_mode":
                continue          # a stop condition, tested below
            trimmed = {k: v for k, v in doc.items() if k != block}
            path = tmp.yaml(yaml.safe_dump(trimmed, sort_keys=False))
            got = V.read_v1(path)
            owned = [k.path for k in R.KEYS
                     if k.v1_sources and all(s == block or s.startswith(block + ".")
                                             for s in k.v1_sources)]
            self.assert_defaults(got.values, owned, f"without {block}")

    def test_default_exceptions_are_the_documented_three(self):
        self.assertEqual(sorted(R.DEFAULT_EXCEPTIONS),
                         ["commands.state_path", "mode.media", "uplink.network_type"])


class TestReaderStops(unittest.TestCase):
    BASE = """\
        capture_mode: "progressive_jpeg"
        bm_serial:
          network_type: 0x02
        """

    def problems(self, text):
        return V.read_v1(Tmp(self).yaml(textwrap.dedent(self.BASE) + text)).problems

    def assert_stop(self, text, needle):
        probs = self.problems(text)
        self.assertTrue(any(needle in p for p in probs), f"{needle!r} not in {probs}")

    def test_base_is_clean(self):
        self.assertEqual(self.problems(""), [])

    def test_heic_and_missing_capture_mode(self):
        tmp = Tmp(self)
        heic = V.read_v1(tmp.yaml('capture_mode: "heic"\nbm_serial:\n  network_type: 0x02\n'))
        self.assertTrue(any("heic" in p for p in heic.problems))
        self.assertNotIn("mode.media", heic.values)
        missing = V.read_v1(tmp.yaml("bm_serial:\n  network_type: 0x02\n", "b.yaml"))
        self.assertTrue(any("capture_mode is missing" in p for p in missing.problems))

    def test_network_type_absent(self):
        got = V.read_v1(Tmp(self).yaml('capture_mode: "progressive_jpeg"\n'))
        self.assertTrue(any("network_type is absent" in p for p in got.problems))

    def test_media_gid_enabled(self):
        self.assert_stop("media_gid:\n  enabled: true\n", "media_gid")

    def test_unknown_key(self):
        self.assert_stop("progressive_jpeg:\n  crop_mode: fixed\n", "unknown v1 key")
        self.assert_stop("mystery: 1\n", "unknown v1 key 'mystery'")

    def test_duplicate_top_level_block(self):
        self.assert_stop("power_halt:\n  enabled: true\npower_halt:\n  dry_run: true\n",
                         "more than once")

    def test_mirror_flags_disagree(self):
        self.assert_stop("enforce_time_window: false\nenforce_spotter_time_window: true\n",
                         "disagree")

    def test_quoted_bool_in_a_pyyaml_island(self):
        self.assert_stop('bm_commands:\n  enabled: "false"\n', "not a YAML bool")

    def test_defer_acks_true(self):
        self.assert_stop("bm_commands:\n  defer_acks_during_transmit: true\n", "W2")

    def test_junk_control_values(self):
        self.assert_stop("image_pipeline:\n  camera_controls:\n    focus:\n      mode: fuzzy\n",
                         "focus.mode")
        self.assert_stop("image_pipeline:\n  camera_controls:\n    white_balance:\n"
                         "      red_gain: 1.5\n", "only one of red_gain")
        self.assert_stop("image_pipeline:\n  camera_controls:\n    exposure:\n"
                         "      ev: bright\n", "exposure.ev")

    def test_bad_window_string(self):
        self.assert_stop('transmit_window:\n  start: "8:00"\n  end: "15:00"\n', "HH:MM")

    def test_invalid_video_tx_on_a_stills_unit_is_refused_not_dropped(self):
        self.assert_stop("video_tx:\n  enabled: maybe\n", "load_video_tx_config")

    def test_missing_pyyaml_refuses(self):
        path = Tmp(self).yaml(self.BASE)
        with mock.patch.dict(sys.modules, {"yaml": None}):
            got = V.read_v1(path)
        self.assertTrue(any("PyYAML is not installed" in p for p in got.problems))
        self.assertEqual(got.values, {})

    def test_video_modes(self):
        tmp = Tmp(self)
        vid = V.read_v1(tmp.yaml('capture_mode: "video"\nbm_serial:\n  network_type: 0x02\n'
                                 "video_tx:\n  enabled: true\n"))
        self.assertEqual(vid.values["mode.media"], "video")
        rec = V.read_v1(tmp.yaml('capture_mode: "video"\nbm_serial:\n  network_type: 0x02\n',
                                 "b.yaml"))
        self.assertEqual(rec.values["mode.media"], "video_logger")


if __name__ == "__main__":
    unittest.main()
