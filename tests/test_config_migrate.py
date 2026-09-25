#!/usr/bin/env python3
# filename: test_config_migrate.py
# description: Sprint26 S2c — v1 -> v2 migrator: YAML round trip, v8 overlay == v1 goldens, state carry, refusals, CLI.
"""
Pins Sprint26 S2c (DESIGN_supervisor.md §5 Migration; PLAN_S2.md G1):

  yaml      every migratable repo profile -> camera_config.yaml that spells out
            EVERY registry key and parses back (PyYAML) to exactly the values read
  overlay   overlay_from_v8() applied to the migrated state reproduces what the v1
            command overlay did, for every v1 state fixture the S1 goldens pinned
            (roi 5, foc 0 over manual 1.82, foc 3, awb 1 + exp 4, hlt 3,
            win/txd/cap, twn/tmz)
  state     the v8 section re-loads through the v1 CommandState unchanged; heals,
            trigger and dedupe ids carried; src != 0 dropped + reported; high-water
            and result cache empty; tables != v8, corrupt state, loader resets
            refused; commands off -> no v2 state
  refusal   heic / missing capture_mode profiles produce no files
  cli       dry-run writes nothing; --write writes the three files atomically;
            an existing v2 file is refused without --force and kept with it;
            exit 3 on refusal

Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_config_migrate.py
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "BM_Devel_Pi")
if APP not in sys.path:
    sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(REPO, "tests", "golden"))

import yaml  # noqa: E402

import config_migrate as M  # noqa: E402
import config_registry as R  # noqa: E402
import config_v1_reader as V  # noqa: E402
import scenarios as S  # noqa: E402
from command_state import CommandState  # noqa: E402

BMCAM003 = os.path.join(REPO, "device_profiles", "bmcam003", "camera_schedule.yaml")
TOOL = os.path.join(REPO, "tools", "config_migrate_v1_v2.py")
MIGRATABLE = ["bmcam000", "bmcam001", "bmcam002", "bmcam003", "rc_field_template"]


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class TmpDir:
    def __init__(self, case):
        self._d = tempfile.TemporaryDirectory()
        case.addCleanup(self._d.cleanup)
        self.path = self._d.name

    def join(self, *p):
        return os.path.join(self.path, *p)


def v1_state_with(tmp, records):
    path = tmp.join("bm_command_state.json")
    state = quiet(CommandState, path=path)
    for cid, cmd, value in records:
        quiet(state.record, cid, cmd, value)
    return path


class TestYaml(unittest.TestCase):
    def test_every_profile_round_trips_and_spells_out_every_key(self):
        for unit in MIGRATABLE:
            tmp = TmpDir(self)
            path = os.path.join(REPO, "device_profiles", unit, "camera_schedule.yaml")
            m = quiet(M.migrate, path, tmp.join("none.json"))
            self.assertEqual(m.problems, [], unit)
            doc = yaml.safe_load(m.config_text)
            self.assertEqual(doc.pop("schema"), 2)
            flat = R.flatten(doc)
            self.assertEqual(sorted(flat), sorted(R.BY_PATH), unit)
            self.assertEqual(flat, m.values, unit)
            for key in R.KEYS:
                self.assertIsNone(R.check_value(key, flat[key.path]), f"{unit} {key.path}")

    def test_network_type_is_written_as_hex_and_reads_back_as_2(self):
        m = quiet(M.migrate, BMCAM003, "/nonexistent.json")
        self.assertIn("  network_type: 0x02  #", m.config_text)
        self.assertEqual(yaml.safe_load(m.config_text)["uplink"]["network_type"], 2)

    def test_strings_that_yaml_would_retype_stay_strings(self):
        m = quiet(M.migrate, BMCAM003, "/nonexistent.json")
        doc = yaml.safe_load(m.config_text)
        self.assertEqual(doc["schedule"]["window"]["start"], "10:00")   # not sexagesimal
        self.assertEqual(doc["video"]["record"]["encoder"]["level"], "")

    def test_rendering_is_deterministic(self):
        a = quiet(M.migrate, BMCAM003, "/nonexistent.json", generated_by="x").config_text
        b = quiet(M.migrate, BMCAM003, "/nonexistent.json", generated_by="x").config_text
        self.assertEqual(a, b)


class TestOverlayMatchesV1Goldens(unittest.TestCase):
    """The effective config (YAML values ⊕ overlay_from_v8) equals what the v1
    overlay produced, as recorded by the S1 settings goldens."""

    def golden(self, fixture):
        path = os.path.join(REPO, "tests", "golden", "settings", f"bmcam003+{fixture}",
                            "settings.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["overlaid"]

    def effective(self, fixture):
        tmp = TmpDir(self)
        state_path = v1_state_with(tmp, S.STATE_FIXTURES[fixture])
        m = quiet(M.migrate, BMCAM003, state_path)
        self.assertEqual(m.problems, [], fixture)
        eff = dict(m.values)
        eff.update(M.overlay_from_v8(m.state["v8"]))
        return eff

    def test_every_fixture(self):
        self.assertEqual(len(S.STATE_FIXTURES), 7)
        for fixture in S.STATE_FIXTURES:
            want, eff = self.golden(fixture), self.effective(fixture)
            self.assertEqual(eff["still.crop"], want["crop_native_xywh"], fixture)
            self.assertEqual(eff["still.budget_min"], want["max_run_time_min"], fixture)
            self.assertEqual(eff["video.send.budget_min"], want["max_run_time_min"], fixture)
            self.assertEqual(eff["uplink.msg_interval_s"], want["pacing_delay_seconds"], fixture)
            self.assertEqual(eff["still.message_cap"], want["message_cap"], fixture)
            self.assertEqual(eff["power.halt.enabled"], want["power_halt_enabled"], fixture)
            self.assertEqual(eff["power.halt.dry_run"], want["power_halt_dry_run"], fixture)
            self.assertEqual(eff["schedule.window.start"], want["window_start"], fixture)
            self.assertEqual(eff["schedule.window.end"], want["window_end"], fixture)
            self.assertEqual(eff["schedule.timezone"], want["timezone"], fixture)
            # Camera controls: read the v1 override dict with the v1 reader's own
            # control rules and compare every camera.* key.
            controls = want.get("camera_controls_override")
            if controls is None:
                continue
            got, probs = {}, []
            V._read_controls(controls, got, probs)
            self.assertEqual(probs, [], fixture)
            for path, value in got.items():
                self.assertEqual(eff[path], value, f"{fixture}: {path}")

    def test_foc0_over_manual_drops_the_lens_position(self):
        eff = self.effective("foc0_over_manual")
        self.assertEqual(eff["camera.focus.mode"], "auto")
        self.assertIsNone(eff["camera.focus.lens_position"])       # YAML had 1.82
        self.assertIsNone(eff["camera.focus.range"])               # whole group replaced

    def test_hlt0_and_twn0_carry_no_payload(self):
        v8 = {"settings": {"hlt": 0, "twn": 0, "tmz": 0}, "touched": ["hlt", "twn", "tmz"]}
        self.assertEqual(M.overlay_from_v8(v8), {})


class TestState(unittest.TestCase):
    def test_v8_section_reloads_through_the_v1_loader_unchanged(self):
        tmp = TmpDir(self)
        records = [(1, "roi", 5), (2, "hlt", 3), (3, "foc", 3), (4, "trg", 2)]
        src = v1_state_with(tmp, records)
        heal = {"key": "0drziv", "n": [3, 4], "id": 100001, "wakes_left": 3}
        state = quiet(CommandState, path=src)
        state.pending_heals = [heal]
        quiet(state.save)
        m = quiet(M.migrate, BMCAM003, src)
        self.assertEqual(m.problems, [])
        v8 = m.state["v8"]
        back = tmp.join("reloaded.json")
        with open(back, "w", encoding="utf-8") as fh:
            json.dump(dict(v8, schema="bm_command_state_v1", tables_version=8), fh)
        again = quiet(CommandState, path=back)
        orig = quiet(CommandState, path=src)
        self.assertEqual(again.settings, orig.settings)
        self.assertEqual(again.touched, orig.touched)
        self.assertEqual(again.applied_ids, orig.applied_ids)
        self.assertEqual(again.pending_trigger, {"id": 4, "value": 2})
        self.assertEqual(again.pending_heals, [heal])

    def test_v2_fields_start_empty(self):
        tmp = TmpDir(self)
        m = quiet(M.migrate, BMCAM003, v1_state_with(tmp, [(1, "hlt", 3)]))
        st = m.state
        self.assertEqual(st["schema"], "bm_command_state_v2")
        self.assertEqual(st["tables_version"], 8)
        self.assertEqual((st["boot_counter"], st["overlay"], st["guarded"],
                          st["result_cache"], st["high_water"]), (0, {}, {}, {}, {}))
        self.assertEqual(len(st["migrated_from"]["sha256"]), 64)

    def test_src_is_dropped_and_reported(self):
        tmp = TmpDir(self)
        m = quiet(M.migrate, BMCAM003, v1_state_with(tmp, [(1, "src", 9), (2, "roi", 2)]))
        self.assertEqual(m.problems, [])
        self.assertEqual(m.state["v8"]["settings"]["src"], 0)
        self.assertNotIn("src", m.state["v8"]["touched"])
        self.assertIn("roi", m.state["v8"]["touched"])
        self.assertTrue(any("DROPPED src=9" in n for n in m.notes))

    def test_refusals(self):
        tmp = TmpDir(self)
        old = tmp.join("old_tables.json")
        with open(old, "w") as fh:
            json.dump({"schema": "bm_command_state_v1", "tables_version": 7,
                       "settings": {}, "touched": []}, fh)
        self.assertTrue(any("tables_version=7" in p
                            for p in quiet(M.migrate, BMCAM003, old).problems))
        bad = tmp.join("corrupt.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        self.assertTrue(any("unreadable" in p for p in quiet(M.migrate, BMCAM003, bad).problems))
        reset = tmp.join("reset.json")
        with open(reset, "w") as fh:
            json.dump({"schema": "bm_command_state_v1", "tables_version": 8,
                       "settings": {"roi": 99}, "touched": ["roi"]}, fh)
        m = quiet(M.migrate, BMCAM003, reset)
        self.assertTrue(any("dropped or reset" in p for p in m.problems))
        self.assertIsNone(m.config_text)

    def test_commands_off_writes_no_state(self):
        tmp = TmpDir(self)
        with open(BMCAM003, encoding="utf-8") as fh:
            text = fh.read().replace("enabled: true   # Sprint10", "enabled: false  # Sprint10")
        doc = yaml.safe_load(text)
        doc["bm_commands"]["enabled"] = False
        path = tmp.join("camera_schedule.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False)
        m = quiet(M.migrate, path, v1_state_with(tmp, [(1, "roi", 5)]))
        self.assertEqual(m.problems, [])
        self.assertIsNone(m.state)
        self.assertFalse(yaml.safe_load(m.config_text)["commands"]["enabled"])


class TestRefusedProfiles(unittest.TestCase):
    def test_heic_and_missing_capture_mode_write_nothing(self):
        for path in (os.path.join(APP, "camera_schedule.yaml"),
                     os.path.join(REPO, "device_profiles",
                                  "camera_schedule_large_cellular_example.yaml")):
            m = quiet(M.migrate, path, "/nonexistent.json")
            self.assertTrue(m.problems, path)
            self.assertIsNone(m.config_text)
            self.assertIsNone(m.state)


class TestCli(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                              timeout=120)

    def setUp(self):
        self.tmp = TmpDir(self)
        with open(BMCAM003, encoding="utf-8") as fh:
            text = fh.read().replace('"/home/pi/BM_Devel_Pi/bm_command_state.json"',
                                     f'"{self.tmp.join("bm_command_state.json")}"')
        self.bmcam003_text = text
        self.cfg = self.tmp.join("camera_schedule.yaml")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.state = v1_state_with(self.tmp, [(1, "hlt", 3)])
        self.before = sorted(os.listdir(self.tmp.path))

    def test_dry_run_writes_nothing(self):
        r = self.run_tool("--config", self.cfg, "--state", self.state)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry-run OK", r.stdout)
        self.assertEqual(sorted(os.listdir(self.tmp.path)), self.before)

    def test_write_then_refuse_then_force(self):
        r = self.run_tool("--config", self.cfg, "--state", self.state, "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        made = set(os.listdir(self.tmp.path)) - set(self.before)
        self.assertEqual(made, {"camera_config.yaml", "bm_command_state_v2.json",
                                "config_migration_report.md", "config_journal.jsonl"})
        self.assertFalse([n for n in os.listdir(self.tmp.path) if n.endswith(".tmp")])
        with open(self.tmp.join("bm_command_state_v2.json")) as fh:
            self.assertEqual(json.load(fh)["v8"]["settings"]["hlt"], 3)
        with open(self.cfg, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.bmcam003_text)      # v1 untouched
        r = self.run_tool("--config", self.cfg, "--state", self.state, "--write")
        self.assertEqual(r.returncode, 2)
        self.assertIn("refusing to replace", r.stderr)
        r = self.run_tool("--config", self.cfg, "--state", self.state, "--write", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        kept = [n for n in os.listdir(self.tmp.path) if ".before_migrate_" in n]
        self.assertEqual(len(kept), 2)
        import config_journal
        entries = config_journal.read(self.tmp.join("config_journal.jsonl"))
        self.assertEqual([e["src"] for e in entries], ["migrate", "migrate"])
        self.assertRegex(entries[0]["h"], r"^[0-9a-f]{8}$")

    def test_out_dir_must_hold_the_state(self):
        other = TmpDir(self)
        r = self.run_tool("--config", self.cfg, "--state", self.state, "--write",
                          "--out-dir", other.path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("commands.state_path", r.stderr)
        self.assertEqual(os.listdir(other.path), [])

    def test_refusal_exits_3(self):
        with open(self.cfg, "a", encoding="utf-8") as fh:
            fh.write("\nmedia_gid:\n  enabled: true\n")
        r = self.run_tool("--config", self.cfg, "--state", self.state, "--write")
        self.assertEqual(r.returncode, 3)
        self.assertIn("media_gid", r.stdout)
        self.assertEqual(sorted(os.listdir(self.tmp.path)), self.before)


if __name__ == "__main__":
    unittest.main()
