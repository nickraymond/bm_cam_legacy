#!/usr/bin/env python3
# filename: test_s3a_runtime_switch.py
# description: Sprint26 S3a.4 — commands.runtime (legacy|supervisor) and the --runtime override.
"""
Sprint26 S3a.4 (PLAN_S3a.md G3).

Pins:
  - precedence: --runtime > commands.runtime in the active v2 file > legacy;
    v1-only units (no BootConfig) and safe fallbacks run legacy;
  - a migrated file spells commands.runtime: legacy;
  - a v2 file written before the key existed (S2) still loads at level v2 and
    means legacy (the key is additive; only the config hash changes);
  - a bad value never selects the supervisor.

Run (repo root):
  python3 -m unittest tests.test_s3a_runtime_switch -v
"""

import os
import sys
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402
from tests.test_config_v2 import Unit, quiet  # noqa: E402


class Boot:
    def __init__(self, values, level="v2"):
        self.values, self.level = values, level


class PrecedenceTests(unittest.TestCase):
    def test_cli_wins(self):
        self.assertEqual(rc.resolve_runtime("supervisor", Boot({"commands.runtime": "legacy"})),
                         ("supervisor", "cli"))
        self.assertEqual(rc.resolve_runtime("legacy", Boot({"commands.runtime": "supervisor"})),
                         ("legacy", "cli"))

    def test_config_then_default(self):
        self.assertEqual(rc.resolve_runtime(None, Boot({"commands.runtime": "supervisor"})),
                         ("supervisor", "config (v2)"))
        self.assertEqual(rc.resolve_runtime(None, None), ("legacy", "default"))
        self.assertEqual(rc.resolve_runtime(None, Boot(None, "safe_minimal")),
                         ("legacy", "default"))
        self.assertEqual(rc.resolve_runtime(None, Boot({"commands.runtime": "turbo"})),
                         ("legacy", "default"))

    def test_registry_key(self):
        k = R.BY_PATH["commands.runtime"]
        self.assertEqual((k.default, k.enum), ("legacy", ("legacy", "supervisor")))
        self.assertGreaterEqual(R.REGISTRY_VERSION, 2)


class ConfigFileTests(unittest.TestCase):
    def test_migrated_file_spells_legacy(self):
        u = Unit(self)
        with open(u.v2, encoding="utf-8") as fh:
            self.assertEqual(yaml.safe_load(fh)["commands"]["runtime"], "legacy")
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")
        self.assertEqual(rc.resolve_runtime(None, boot), ("legacy", "config (v2)"))

    def test_s2_file_without_the_key_still_loads_as_legacy(self):
        u = Unit(self)
        with open(u.v2, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        del doc["commands"]["runtime"]
        with open(u.v2, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh)
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")
        self.assertEqual(boot.values["commands.runtime"], "legacy")

    def test_supervisor_selected_from_the_file(self):
        u = Unit(self)
        u.edit_v2("commands.runtime", "supervisor")
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")
        self.assertEqual(rc.resolve_runtime(None, boot), ("supervisor", "config (v2)"))

    def test_bad_value_never_selects_the_supervisor(self):
        u = Unit(self)
        u.edit_v2("commands.runtime", "supervisr")
        boot = quiet(u.boot)
        self.assertNotEqual(boot.level, "v2")
        self.assertEqual(rc.resolve_runtime(None, boot)[0], "legacy")


if __name__ == "__main__":
    unittest.main()
