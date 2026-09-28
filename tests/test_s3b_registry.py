#!/usr/bin/env python3
# filename: test_s3b_registry.py
# description: Sprint26 S3b.1 — stay_on registry keys (mode.interval_s, mode.heartbeat_s) + cross-keys.
"""
Sprint26 S3b.1 (PLAN_S3b.md H1).

Pins:
  - mode.interval_s (default 0 = trigger-only) and mode.heartbeat_s (default
    300) exist; each is 0 or 60..86400;
  - mode.run: stay_on is runnable, but only with commands.runtime: supervisor
    AND commands.enabled: true (a strict load refuses otherwise; a boot falls
    back to the v1 file, i.e. per_boot legacy, with a loud [CFG][ERR]);
  - a migrated file spells the new keys; a v2 file written before them (S3a)
    still loads at level v2 with the defaults;
  - REGISTRY_VERSION is at least 3.

Run (repo root):
  python3 -m unittest tests.test_s3b_registry -v
"""

import os
import sys
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2 as C  # noqa: E402
from tests.test_config_v2 import Unit, quiet  # noqa: E402


def stay_on_unit(case, **edits):
    """A migrated bmcam003 unit set up for stay_on (commands on, supervisor)."""
    u = Unit(case)
    u.edit_v2("commands.enabled", True)
    u.edit_v2("commands.runtime", "supervisor")
    u.edit_v2("mode.run", "stay_on")
    for path, value in edits.items():
        u.edit_v2(path.replace("__", "."), value)
    return u


class RegistryKeys(unittest.TestCase):
    def test_version_and_defaults(self):
        self.assertGreaterEqual(R.REGISTRY_VERSION, 3)
        self.assertEqual(R.BY_PATH["mode.interval_s"].default, 0)
        self.assertEqual(R.BY_PATH["mode.heartbeat_s"].default, 300)
        self.assertIsNone(R.BY_PATH["mode.run"].runnable)
        for path in ("mode.run", "mode.interval_s", "mode.heartbeat_s"):
            self.assertEqual(R.BY_PATH[path].apply, R.NEXT_BOOT, path)

    def test_migrated_file_spells_the_keys(self):
        u = Unit(self)
        with open(u.v2, encoding="utf-8") as fh:
            mode = yaml.safe_load(fh)["mode"]
        self.assertEqual((mode["run"], mode["interval_s"], mode["heartbeat_s"]),
                         ("per_boot", 0, 300))

    def test_s3a_file_without_the_keys_still_loads(self):
        u = Unit(self)
        with open(u.v2, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        del doc["mode"]["interval_s"], doc["mode"]["heartbeat_s"]
        with open(u.v2, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh)
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")
        self.assertEqual((boot.values["mode.interval_s"], boot.values["mode.heartbeat_s"]),
                         (0, 300))


class StayOnCrossKeys(unittest.TestCase):
    def test_stay_on_with_supervisor_and_commands_loads(self):
        u = stay_on_unit(self, mode__interval_s=120, mode__heartbeat_s=0)
        cfg = C.load_config(u.v2, strict=True)
        self.assertEqual(cfg.base["mode.run"], "stay_on")
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")
        self.assertEqual((boot.values["mode.interval_s"], boot.values["mode.heartbeat_s"]),
                         (120, 0))

    def test_stay_on_refused_without_supervisor_or_commands(self):
        for path, value, needle in (
                ("commands.runtime", "legacy", "stay_on needs commands.runtime: supervisor"),
                ("commands.enabled", False, "stay_on needs commands.enabled: true")):
            u = stay_on_unit(self)
            u.edit_v2(path, value)
            with self.assertRaises(C.ConfigError) as ctx:
                C.load_config(u.v2, strict=True)
            self.assertIn(needle, str(ctx.exception))
            boot = quiet(u.boot)            # boot: loud fallback to the v1 file (per_boot)
            self.assertEqual(boot.level, "v1_migrated", path)
            self.assertEqual(boot.values["mode.run"], "per_boot")
            self.assertTrue(any("[CFG][ERR]" in line for line in boot.lines))

    def test_interval_and_heartbeat_are_zero_or_at_least_60(self):
        for path in ("mode.interval_s", "mode.heartbeat_s"):
            for value, ok in ((0, True), (59, False), (1, False), (60, True),
                              (86400, True), (86401, False), (-1, False)):
                u = stay_on_unit(self)
                u.edit_v2(path, value)
                if ok:
                    C.load_config(u.v2, strict=True)
                else:
                    with self.assertRaises(C.ConfigError, msg=f"{path}={value}"):
                        C.load_config(u.v2, strict=True)


if __name__ == "__main__":
    unittest.main()
