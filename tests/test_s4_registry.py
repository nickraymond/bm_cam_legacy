#!/usr/bin/env python3
# filename: test_s4_registry.py
# description: Sprint26 S4 a.1 — registry v5: keep-alive/hold keys, bus_always_on, storage.* rename + aliases, short names, ONE_SHOT.
"""
Sprint26 S4 commit a.1 (PLAN_S4.md G5, G6, G10c/e, G11).

Pins:
  - REGISTRY_VERSION 5; the four new keys with their defaults and ranges;
  - video.storage.* is renamed storage.*; an S3c-era camera_config.yaml that
    still spells video.storage.* loads at level v2 to the SAME values, with one
    warning per old name; both spellings at once is an error;
  - an S3c-era file without the new keys loads with their defaults;
  - the ONE_SHOT list (trg kv) holds no path, locked, service or guarded_stage
    key and no NEXT_BOOT key other than mode.media / mode.output;
  - migrate spells the new names.

Run (repo root):
  python3 -m unittest tests.test_s4_registry -v
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


def _rewrite(u, fn):
    with open(u.v2, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    fn(doc)
    with open(u.v2, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh)


def _to_s3c_names(doc):
    """What an S3c-era (registry v4) migrated file looked like."""
    doc["video"]["storage"] = doc.pop("storage")


class NewKeys(unittest.TestCase):
    def test_version_and_new_keys(self):
        self.assertGreaterEqual(R.REGISTRY_VERSION, 5)      # 6 = Sprint27 image-processing limits
        want = {"commands.keepalive_s": (300, (0, 1800)),
                "commands.keepalive_max_s": (1800, (0, 7200)),
                "commands.hold_max_min": (120, (0, 240))}
        for path, (default, rng) in want.items():
            key = R.BY_PATH[path]
            self.assertEqual((key.type, key.default, key.range), (R.INT, default, rng), path)
        key = R.BY_PATH["power.bus_always_on"]
        self.assertEqual((key.type, key.default), (R.BOOL, False))

    def test_storage_renamed(self):
        self.assertFalse([k for k in R.BY_PATH if k.startswith("video.storage.")])
        for old, new in R.ALIASES.items():
            self.assertIn(new, R.BY_PATH)
            self.assertNotIn(old, R.BY_PATH)
            self.assertEqual(R.BY_PATH[new].v1_sources, (old,), "v1 path is unchanged")

    def test_migrate_spells_the_new_names(self):
        u = Unit(self)
        with open(u.v2, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        self.assertIn("storage", doc)
        self.assertNotIn("storage", doc["video"])
        self.assertEqual(doc["commands"]["keepalive_s"], 300)
        self.assertIs(doc["power"]["bus_always_on"], False)


class OldFiles(unittest.TestCase):
    def test_s3c_names_load_to_the_same_values_with_a_warning(self):
        u = Unit(self)
        want = C.load_config(u.v2, strict=True)
        _rewrite(u, _to_s3c_names)
        got = C.load_config(u.v2, strict=True)
        self.assertEqual(got.base, want.base)
        self.assertEqual(got.hash, want.hash)
        self.assertEqual(len([w for w in got.warnings if "old name" in w]), 3)
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")

    def test_both_spellings_is_an_error(self):
        u = Unit(self)
        _rewrite(u, lambda d: d["video"].update(storage={"max_used_pct": 60.0}))
        with self.assertRaises(C.ConfigError):
            C.load_config(u.v2, strict=True)

    def test_s3c_file_without_the_new_keys_loads(self):
        u = Unit(self)

        def strip(doc):
            _to_s3c_names(doc)
            del doc["power"]["bus_always_on"]
            for k in ("keepalive_s", "keepalive_max_s", "hold_max_min"):
                del doc["commands"][k]
        _rewrite(u, strip)
        boot = quiet(u.boot)
        self.assertEqual(boot.level, "v2")
        self.assertEqual(boot.values["commands.keepalive_s"], 300)
        self.assertIs(boot.values["power.bus_always_on"], False)

    def test_old_lkg_names_load(self):
        vals = R.defaults()
        vals["mode.media"] = "still"
        for old, new in R.ALIASES.items():
            vals[old] = vals.pop(new)
        values, _src, errors = C.parse_values(R.nest(vals) | {"schema": 2}, False)
        self.assertEqual(errors, [])
        self.assertEqual(values["storage.max_used_pct"], 75.0)


class OneShot(unittest.TestCase):
    def test_allow_list_is_action_scoped(self):
        for path in R.ONE_SHOT:
            key = R.BY_PATH[path]
            self.assertNotEqual(key.type, R.PATH, path)
            self.assertNotIn(key.guard, (R.LOCKED, R.SERVICE, R.GUARDED_STAGE), path)
            if key.apply == R.NEXT_BOOT:
                self.assertIn(path, ("mode.media", "mode.output"), path)
        for path in ("mode.media", "mode.output", "still.crop", "video.send.duration_s",
                     "still.message_cap", "video.send.message_cap", "camera.focus.mode",
                     "camera.white_balance.gains", "camera.exposure.ev"):
            self.assertIn(path, R.ONE_SHOT)
        for path in ("mode.run", "power.halt.enabled", "uplink.network_type",
                     "commands.enabled", "still.quality_ladder", "camera.native.width"):
            self.assertNotIn(path, R.ONE_SHOT)


if __name__ == "__main__":
    unittest.main()
