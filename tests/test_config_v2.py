#!/usr/bin/env python3
# filename: test_config_v2.py
# description: Sprint26 S2d — v2 loader (strict / boot), hash, v1 render round trip, never-brick fallback chain, v2 state file.
"""
Pins Sprint26 S2d (DESIGN_supervisor.md §5.1; PLAN_S2.md G1/G2):

  strict     unknown key (incl. a media_gid block), bad type, out of range,
             missing mode.media, wrong schema, non-runnable mode -> ConfigError
             naming the key
  hash       8 hex; same effective config -> same hash; any change -> new hash;
             5 and 5.0 hash alike for a float key; the v8 overlay changes it
  render     for every migratable profile: v1 YAML -> migrate -> render -> the v1
             reader gives back exactly the migrated values (per key, through every
             v1 loader family)
  boot       v2 ok -> v2; a bad value on a key the active mode does not use ->
             still v2 (warned); corrupt / invalid-active / non-runnable v2 ->
             v1 migrated in memory; v1 unusable too -> last-known-good; nothing ->
             safe-minimal; no PyYAML -> last-known-good; load_for_boot never raises
  main       safe-minimal exits 0 with nothing done; --config-format v1 ignores a
             v2 file; --config-format v2 without one exits 2; --print-config never
             writes last-known-good; a normal v2 boot writes it once
  state      CommandState on a v2 file reads/writes only the v8 section and keeps
             every other field; a v1 file keeps its exact v1 bytes

Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_config_v2.py
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "BM_Devel_Pi")
if APP not in sys.path:
    sys.path.insert(0, APP)

import yaml  # noqa: E402

import config_migrate as M  # noqa: E402
import config_registry as R  # noqa: E402
import config_v1_reader as V  # noqa: E402
import config_v2 as C  # noqa: E402
from command_state import CommandState  # noqa: E402

BMCAM003 = os.path.join(REPO, "device_profiles", "bmcam003", "camera_schedule.yaml")
MIGRATABLE = ["bmcam000", "bmcam001", "bmcam002", "bmcam003", "rc_field_template",
              "bmcam003/live_20260925", "bmcam004/live_20260925"]   # live = pulled 2026-09-25


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class Unit:
    """A temp runtime dir holding a v1 YAML (bmcam003), optionally migrated."""

    def __init__(self, case, migrate=True, state_records=()):
        self.dir = tempfile.mkdtemp(prefix="cfgv2_")
        case.addCleanup(shutil.rmtree, self.dir, True)
        with open(BMCAM003, encoding="utf-8") as fh:
            text = fh.read().replace('"/home/pi/BM_Devel_Pi/bm_command_state.json"',
                                     f'"{self.p("bm_command_state.json")}"')
        self.v1 = self.p("camera_schedule.yaml")
        with open(self.v1, "w", encoding="utf-8") as fh:
            fh.write(text)
        if state_records:
            st = quiet(CommandState, path=self.p("bm_command_state.json"))
            for rec in state_records:
                quiet(st.record, *rec)
        self.v2 = self.p("camera_config.yaml")
        self.lkg = self.p("camera_config.lkg.json")
        if migrate:
            m = quiet(M.migrate, self.v1, None)
            assert not m.problems, m.problems
            with open(self.v2, "w", encoding="utf-8") as fh:
                fh.write(m.config_text)
            if m.state is not None:
                with open(m.values["commands.state_path"], "w", encoding="utf-8") as fh:
                    json.dump(m.state, fh)
            self.values = m.values

    def p(self, name):
        return os.path.join(self.dir, name)

    def edit_v2(self, path, value):
        with open(self.v2, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        node = doc
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
        with open(self.v2, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh)

    def boot(self):
        return C.load_for_boot(self.v1, self.v2, self.lkg)


class TestStrict(unittest.TestCase):
    def assert_refused(self, unit, needle):
        with self.assertRaises(C.ConfigError) as ctx:
            C.load_config(unit.v2, strict=True)
        self.assertIn(needle, str(ctx.exception))

    def test_clean_file_loads(self):
        u = Unit(self)
        cfg = C.load_config(u.v2, strict=True)
        self.assertEqual(cfg.base, u.values)
        self.assertEqual(set(cfg.source.values()), {"yaml"})
        self.assertRegex(cfg.hash, r"^[0-9a-f]{8}$")

    def test_refusals_name_the_key(self):
        cases = [("still.crop", [0, 0, 5000, 100], "still.crop"),
                 ("uplink.network_type", 3, "uplink.network_type"),
                 ("time.timeout_s", "60", "time.timeout_s"),
                 ("mode.media", None, "mode.media"),
                 ("mode.run", "stay_on", "not runnable before S3"),
                 ("schema", 1, "schema must be 2")]
        for path, value, needle in cases:
            u = Unit(self)
            u.edit_v2(path, value)
            self.assert_refused(u, needle)

    def test_unknown_keys_and_media_gid_are_refused(self):
        u = Unit(self)
        with open(u.v2, "a", encoding="utf-8") as fh:
            fh.write("media_gid:\n  enabled: true\n")
        self.assert_refused(u, "unknown key 'media_gid.enabled'")


class TestHash(unittest.TestCase):
    def test_hash_is_a_fingerprint(self):
        base = Unit(self).values
        h = C.config_hash(base)
        self.assertEqual(C.config_hash(dict(base)), h)
        changed = dict(base, **{"still.message_cap": 196})
        self.assertNotEqual(C.config_hash(changed), h)
        as_int = dict(base, **{"uplink.msg_interval_s": 5})
        as_float = dict(base, **{"uplink.msg_interval_s": 5.0})
        self.assertEqual(C.config_hash(as_int), C.config_hash(as_float))

    def test_v8_overlay_is_in_the_effective_config_and_hash(self):
        plain = Unit(self)
        roi = Unit(self, state_records=[(1, "roi", 5)])
        a = C.load_config(plain.v2, strict=True)
        state = C.read_state(roi.values["commands.state_path"])
        b = C.load_config(roi.v2, state=state, strict=True)
        strip = lambda d: {k: v for k, v in d.items() if k != "commands.state_path"}
        self.assertEqual(strip(a.base), strip(b.base))
        self.assertEqual(b.effective["still.crop"], [1904, 1071, 800, 450])
        self.assertEqual(b.source["still.crop"], "cmd")
        self.assertNotEqual(a.hash, b.hash)


class TestRender(unittest.TestCase):
    def test_render_reads_back_through_every_v1_loader(self):
        for unit in MIGRATABLE:
            src = os.path.join(REPO, "device_profiles", unit, "camera_schedule.yaml")
            m = quiet(M.migrate, src, "/nonexistent.json")
            self.assertEqual(m.problems, [], unit)
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "camera_schedule.yaml")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(C.render_v1_text(m.values))
                back = V.read_v1(path)
            self.assertEqual(back.problems, [], unit)
            self.assertEqual(back.values, m.values, unit)

    def test_render_of_every_media_mode(self):
        for media in ("still", "video", "video_logger"):
            values = dict(Unit(self).values, **{"mode.media": media})
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "camera_schedule.yaml")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(C.render_v1_text(values))
                back = V.read_v1(path)
            self.assertEqual(back.problems, [], media)
            self.assertEqual(back.values["mode.media"], media)

    def test_unwritable_value_is_refused(self):
        values = dict(Unit(self).values, **{"commands.topic": "bm#cmd"})
        with self.assertRaises(C.ConfigError):
            C.render_v1_text(values)


class TestBootChain(unittest.TestCase):
    def test_clean_v2(self):
        b = Unit(self).boot()
        self.assertEqual(b.level, "v2")
        self.assertTrue(any(line.startswith("[CFG] config v2") for line in b.lines))

    def test_bad_inactive_key_is_warned_not_fatal(self):
        u = Unit(self)                          # a stills unit
        u.edit_v2("video.send.fps", 999)
        b = u.boot()
        self.assertEqual(b.level, "v2")
        self.assertEqual(b.values["video.send.fps"], 10)          # default used
        self.assertTrue(any("[CFG][WARN]" in line and "video.send.fps" in line
                            for line in b.lines))

    def test_bad_v2_falls_back_to_v1(self):
        for path, value in (("still.crop", [-1, 0, 10, 10]), ("mode.run", "stay_on"),
                            ("mode.media", None)):
            u = Unit(self)
            u.edit_v2(path, value)
            b = u.boot()
            self.assertEqual(b.level, "v1_migrated", path)
            self.assertEqual(b.values, u.values, path)
            self.assertTrue(any("[CFG][ERR]" in line for line in b.lines))
        u = Unit(self)
        with open(u.v2, "w") as fh:
            fh.write("mode: [unclosed\n")
        self.assertEqual(u.boot().level, "v1_migrated")

    def test_then_last_known_good_then_safe_minimal(self):
        u = Unit(self)
        C.save_lkg(u.lkg, u.values, "2026-09-25T00:00:00Z")
        with open(u.v2, "w") as fh:
            fh.write("garbage: [\n")
        with open(u.v1, "w") as fh:
            fh.write('capture_mode: "heic"\n')
        b = u.boot()
        self.assertEqual(b.level, "lkg")
        self.assertEqual(b.values, u.values)
        os.unlink(u.lkg)
        b = u.boot()
        self.assertEqual(b.level, "safe_minimal")
        self.assertIsNone(b.values)
        self.assertTrue(any("SAFE-MINIMAL" in line for line in b.lines))

    def test_no_pyyaml_reaches_last_known_good(self):
        u = Unit(self)
        C.save_lkg(u.lkg, u.values, "2026-09-25T00:00:00Z")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            b = u.boot()
        self.assertEqual(b.level, "lkg")

    def test_never_raises(self):
        for args in (("/nope/a.yaml", "/nope/b.yaml", "/nope/c.json"),
                     (None, None, None), (APP, APP, APP)):
            b = C.load_for_boot(*args)
            self.assertEqual(b.level, "safe_minimal", args)


class TestMainIntegration(unittest.TestCase):
    def main(self, argv):
        import rc_progressive_jpeg as rc
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = rc.main(argv)
        return code, buf.getvalue()

    def setUp(self):
        self.render = tempfile.mkdtemp(prefix="render_")
        self.addCleanup(shutil.rmtree, self.render, True)
        env = mock.patch.dict(os.environ, {"BMCAM_RENDER_DIR": self.render})
        env.start()
        self.addCleanup(env.stop)
        import bm_serial
        import rc_command_hooks
        for mod, attr in ((bm_serial, "BM_CAMERA_CONFIG_PATH"), (rc_command_hooks, "CONFIG_PATH")):
            p = mock.patch.object(mod, attr, getattr(mod, attr))
            p.start()
            self.addCleanup(p.stop)

    def test_print_config_via_v2_writes_no_lkg(self):
        u = Unit(self)
        code, out = self.main(["--config-path", u.v1, "--print-config"])
        self.assertEqual(code, 0)
        self.assertIn("[CFG] config v2", out)
        self.assertIn(os.path.join(self.render, "camera_schedule.yaml"), out)
        self.assertFalse(os.path.exists(u.lkg))

    def test_format_flags(self):
        u = Unit(self)
        code, out = self.main(["--config-path", u.v1, "--print-config", "--config-format", "v1"])
        self.assertEqual(code, 0)
        self.assertNotIn("[CFG]", out)
        bare = Unit(self, migrate=False)
        code, _out = self.main(["--config-path", bare.v1, "--print-config",
                                "--config-format", "v2"])
        self.assertEqual(code, 2)

    def test_safe_minimal_does_nothing(self):
        u = Unit(self)
        for path in (u.v2, u.v1):
            with open(path, "w") as fh:
                fh.write("garbage: [\n")
        code, out = self.main(["--config-path", u.v1, "--transmit"])
        self.assertEqual(code, 0)
        self.assertIn("SAFE-MINIMAL", out)

    def test_lkg_written_once_on_a_real_boot_path(self):
        u = Unit(self)
        import config_v2
        quiet(config_v2.select_for_legacy_runtime, u.v1)
        self.assertTrue(os.path.exists(u.lkg))
        before = os.stat(u.lkg).st_mtime_ns
        lines = []
        config_v2.select_for_legacy_runtime(u.v1, announce=lines.append)
        self.assertEqual(os.stat(u.lkg).st_mtime_ns, before)        # unchanged: not rewritten
        self.assertFalse(any("last-known-good saved" in line for line in lines))


class TestCommandStateV2(unittest.TestCase):
    def test_v2_file_keeps_its_other_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bm_command_state_v2.json")
            with open(path, "w") as fh:
                json.dump({"schema": "bm_command_state_v2", "tables_version": 8,
                           "boot_counter": 7, "overlay": {"x": 1}, "guarded": {},
                           "result_cache": {"5": {"ok": 1}}, "high_water": {"r": 9},
                           "migrated_from": {"path": "/a", "sha256": "b"},
                           "v8": {"settings": {"roi": 2}, "touched": ["roi"],
                                  "applied_ids": [5], "pending_trigger": None,
                                  "pending_heals": []}}, fh)
            st = quiet(CommandState, path=path)
            self.assertTrue(st.is_v2)
            self.assertEqual(st.settings["roi"], 2)
            quiet(st.record, 6, "hlt", 3)
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(data["boot_counter"], 7)
            self.assertEqual(data["overlay"], {"x": 1})
            self.assertEqual(data["high_water"], {"r": 9})
            self.assertEqual(data["migrated_from"], {"path": "/a", "sha256": "b"})
            self.assertEqual(data["v8"]["settings"]["hlt"], 3)
            self.assertEqual(data["v8"]["applied_ids"], [5, 6])
            self.assertNotIn("settings", data)

    def test_new_v2_file_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            st = quiet(CommandState, path=os.path.join(d, "bm_command_state_v2.json"))
            quiet(st.record, 1, "roi", 1)
            with open(os.path.join(d, "bm_command_state_v2.json")) as fh:
                data = json.load(fh)
            self.assertEqual(data["schema"], "bm_command_state_v2")
            self.assertEqual(data["boot_counter"], 0)

    def test_v1_file_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bm_command_state.json")
            st = quiet(CommandState, path=path)
            quiet(st.record, 1, "roi", 1)
            with open(path) as fh:
                text = fh.read()
            self.assertTrue(text.startswith('{"schema":"bm_command_state_v1","tables_version":8,'
                                            '"settings":{'), text)


if __name__ == "__main__":
    unittest.main()
