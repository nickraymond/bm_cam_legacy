#!/usr/bin/env python3
# filename: test_s3c_registry.py
# description: Sprint26 S3c.1 — save_local runnable (supervisor only), still.save.quality, resolve_output.
"""
Sprint26 S3c.1 (PLAN_S3c.md J1 as amended by §5).

Pins:
  - mode.output: save_local is runnable, but only with commands.runtime:
    supervisor (a strict load refuses otherwise; a boot falls back to the v1
    file, which transmits, with a loud [CFG][ERR]);
  - still.save.quality exists (INT 1..95, default 85); the stills storage guard
    has NO keys of its own (it shares video.storage.*, §5 C3);
  - a migrated file spells still.save.quality; a v2 file written before it
    (S3b) still loads at level v2 with the default;
  - resolve_output: save_local only under the supervisor; `--runtime legacy`
    on a save_local file transmits, loudly;
  - REGISTRY_VERSION is 4.

Run (repo root):
  python3 -m unittest tests.test_s3c_registry -v
"""

import contextlib
import io
import os
import sys
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2 as C  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402
from tests.test_config_v2 import Unit, quiet  # noqa: E402


def save_local_unit(case, runtime="supervisor"):
    u = Unit(case)
    u.edit_v2("commands.runtime", runtime)
    u.edit_v2("mode.output", "save_local")
    return u


class Keys(unittest.TestCase):
    def test_version_and_keys(self):
        self.assertEqual(R.REGISTRY_VERSION, 4)
        key = R.BY_PATH["still.save.quality"]
        self.assertEqual((key.default, key.range), (85, (1, 95)))
        self.assertIsNone(R.BY_PATH["mode.output"].runnable)
        self.assertEqual(R.BY_PATH["mode.output"].guard, R.GUARDED_REVERT)
        self.assertFalse([k.path for k in R.KEYS if k.path.startswith("still.storage")],
                         "the stills guard shares video.storage.* (PLAN_S3c §5 C3)")

    def test_migrated_file_spells_the_key(self):
        u = Unit(self)
        with open(u.v2, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        self.assertEqual(doc["still"]["save"]["quality"], 85)
        self.assertEqual(doc["mode"]["output"], "transmit")

    def test_s3b_file_without_the_key_still_loads(self):
        u = Unit(self)
        with open(u.v2, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        del doc["still"]["save"]
        with open(u.v2, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh)
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")
        self.assertEqual(boot.values["still.save.quality"], 85)

    def test_quality_range(self):
        for value, ok in ((1, True), (95, True), (0, False), (96, False), ("85", False)):
            u = Unit(self)
            u.edit_v2("still.save.quality", value)
            if ok:
                C.load_config(u.v2, strict=True)
            else:
                with self.assertRaises(C.ConfigError, msg=repr(value)):
                    C.load_config(u.v2, strict=True)


class SaveLocalCrossKey(unittest.TestCase):
    def test_save_local_with_supervisor_loads(self):
        u = save_local_unit(self)
        cfg = C.load_config(u.v2, strict=True)
        self.assertEqual(cfg.base["mode.output"], "save_local")
        boot = quiet(u.boot)
        self.assertEqual((boot.level, boot.values["mode.output"]), ("v2", "save_local"))

    def test_save_local_with_legacy_is_refused_and_boot_transmits(self):
        u = save_local_unit(self, runtime="legacy")
        with self.assertRaises(C.ConfigError) as ctx:
            C.load_config(u.v2, strict=True)
        self.assertIn("save_local needs commands.runtime: supervisor", str(ctx.exception))
        boot = quiet(u.boot)                 # loud fallback to the v1 file: transmit
        self.assertEqual(boot.level, "v1_migrated")
        self.assertEqual(boot.values["mode.output"], "transmit")
        self.assertTrue(any("[CFG][ERR]" in line for line in boot.lines))


class _Boot:
    def __init__(self, values):
        self.values = values


class ResolveOutput(unittest.TestCase):
    def resolve(self, values, runtime):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = rc.resolve_output(_Boot(values), runtime)
        return got, out.getvalue()

    def test_save_local_only_under_the_supervisor(self):
        self.assertEqual(self.resolve({"mode.output": "save_local"}, "supervisor"),
                         ("save_local", ""))
        got, text = self.resolve({"mode.output": "save_local"}, "legacy")
        self.assertEqual(got, "transmit")
        self.assertIn("TRANSMITTING this boot", text)

    def test_default_and_v1_units_transmit(self):
        self.assertEqual(self.resolve({"mode.output": "transmit"}, "supervisor")[0], "transmit")
        self.assertEqual(self.resolve({}, "supervisor")[0], "transmit")
        self.assertEqual(rc.resolve_output(None, "supervisor"), "transmit")


if __name__ == "__main__":
    unittest.main()
