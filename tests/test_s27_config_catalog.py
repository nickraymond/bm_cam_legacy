#!/usr/bin/env python3
# filename: test_s27_config_catalog.py
# description: Sprint27 — docs/bmcam_config_catalog.json is exactly the generator's output and agrees with the registry the unit runs.
"""
Sprint27 SPEC §3.2: the catalog the backend vendors is generated from
config_registry / command_wire / video_geometry. These tests pin:
  - the committed file equals tools/gen_config_catalog.py's output (regenerate
    with `--write` after any registry, wire or geometry change);
  - its sha256 is the hash of its own body (the backend checks the same);
  - every registry key is present once, with the registry's type / default /
    range / enum / guard / apply;
  - LOCKED and SERVICE keys are never writable (tier blocked); every still.*
    and video.send.* key is a control;
  - each selftest vector matches config_registry.check_value.

Run (repo root):
  python3 -m unittest tests.test_s27_config_catalog -v
"""

import hashlib
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import config_registry as R  # noqa: E402
import gen_config_catalog as G  # noqa: E402


def _load():
    with open(G.OUT, encoding="utf-8") as fh:
        return fh.read()


class Catalog(unittest.TestCase):
    def test_committed_catalog_is_fresh(self):
        self.assertEqual(_load(), G.render(),
                         "docs/bmcam_config_catalog.json is stale: run "
                         "tools/gen_config_catalog.py --write")

    def test_sha256_is_the_body_hash(self):
        cat = json.loads(_load())
        claimed = cat.pop("sha256")
        canon = json.dumps(cat, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.assertEqual(claimed, hashlib.sha256(canon.encode("ascii")).hexdigest())

    def test_every_key_once_with_registry_fields(self):
        cat = json.loads(_load())
        paths = [k["path"] for k in cat["keys"]]
        self.assertEqual(paths, [k.path for k in R.KEYS])
        for doc in cat["keys"]:
            key = R.BY_PATH[doc["path"]]
            self.assertEqual(doc["type"], key.type)
            self.assertEqual(doc["default"], key.default)
            self.assertEqual(doc["range"], list(key.range) if key.range else None)
            self.assertEqual(doc["enum"], list(key.enum))
            self.assertEqual(doc["guard"], key.guard)
            self.assertEqual(doc["apply"], key.apply)
            self.assertEqual(doc["nullable"], key.nullable)

    def test_locked_and_service_keys_are_blocked(self):
        cat = json.loads(_load())
        for doc in cat["keys"]:
            if doc["guard"] in (R.LOCKED, R.SERVICE):
                self.assertEqual(doc["tier"], "blocked", doc["path"])

    def test_still_and_video_keys_are_controls(self):
        """Nick's goal: every still and video control is writable (still.save.quality
        only serves save_local, which is not writable yet)."""
        cat = json.loads(_load())
        for doc in cat["keys"]:
            if doc["path"].startswith(("still.", "video.send.", "video.record.")) and \
                    doc["guard"] != R.LOCKED and doc["path"] != "still.save.quality":
                self.assertEqual(doc["tier"], "control", doc["path"])

    def test_control_allowlist_is_explicit(self):
        """Only allowlisted keys are writable; every allowlisted key exists."""
        cat = json.loads(_load())
        self.assertEqual(sorted(k["path"] for k in cat["keys"] if k["tier"] == "control"),
                         sorted(G.CONTROL_KEYS))
        for path in G.CONTROL_KEYS:
            self.assertIn(path, R.BY_PATH)

    def test_level_basic_keys_are_writable_controls(self):
        """UI level (Nick 2026-10-01): every basic key exists and is a writable control;
        every key has a level."""
        cat = json.loads(_load())
        for doc in cat["keys"]:
            self.assertIn(doc["level"], ("basic", "advanced"), doc["path"])
        basic = [d for d in cat["keys"] if d["level"] == "basic"]
        self.assertEqual(sorted(d["path"] for d in basic), sorted(G.BASIC_KEYS))
        for d in basic:
            self.assertEqual(d["tier"], "control", d["path"])

    def test_registry_range_is_the_only_hard_limit(self):
        """F-G3-8 (Nick 2026-10-02): one source of truth. The catalog never adds its own max;
        every warning threshold sits inside the key's registry range."""
        cat = json.loads(_load())
        for doc in cat["keys"]:
            lim = doc["limits"] or {}
            self.assertFalse({"max", "max_each"} & set(lim), doc["path"])
            if "warn_above" in lim:
                self.assertLess(lim["warn_above"], doc["range"][1], doc["path"])
        by = {d["path"]: d for d in cat["keys"]}
        self.assertEqual(by["still.message_cap"]["range"], [1, 500])
        self.assertEqual(by["video.send.message_cap"]["range"], [8, 500])
        self.assertEqual(by["still.budget_min"]["range"], [1, 30])
        self.assertEqual(by["video.send.budget_min"]["range"], [1, 30])
        self.assertEqual(by["camera.white_balance.gains"]["range"], [0.0, 8.0])
        gains = R.BY_PATH["camera.white_balance.gains"]
        self.assertIsNone(R.check_value(gains, [8.0, 1.5]))
        self.assertIsNotNone(R.check_value(gains, [8.5, 1.5]))

    def test_refresh_gets_cover_every_key_within_two_parts(self):
        """F-G3-10: the backend's full refresh asks for EVERY key (it needs the whole config to
        compute the expected post-change hash); each get answers in <= 2 <CF> parts (unit cap 3)."""
        import command_wire as W
        cat = json.loads(_load())
        v = G._sizing_values()
        covered = set()
        for names in cat["refresh_gets"]:
            self.assertLessEqual(len(names), W.MAX_LIST)
            keys = [p for n in names for p in ([n] if n in R.BY_PATH else [k.path for k in R.keys_in(n)])]
            covered |= set(keys)
            self.assertLessEqual(len(W.build_cf("0123abcd", [(k, v[k], "c1000000") for k in keys])), 2, names)
        self.assertEqual(covered, {k.path for k in R.KEYS})

    def test_hash_vectors_are_the_units_hash(self):
        import config_v2
        cat = json.loads(_load())
        self.assertEqual(cat["hash"]["version"], config_v2.HASH_VERSION)
        self.assertGreaterEqual(len(cat["hash"]["selftest"]), 4)
        for values, want in cat["hash"]["selftest"]:
            self.assertEqual(sorted(values), sorted(k.path for k in R.KEYS))
            self.assertEqual(config_v2.config_hash(values), want)

    def test_geometry_vectors_match_the_unit_rule(self):
        import config_validate as V
        cat = json.loads(_load())
        self.assertGreater(len(cat["geometry_selftest"]), 100)
        for (framing, crop, output, mode, fps), want in cat["geometry_selftest"]:
            geo, _why = V._video_geometry({"video.record.framing": framing, "video.record.crop": crop,
                                           "video.record.output": output,
                                           "video.record.sensor_mode": mode, "video.record.fps": fps})
            got = None if geo is None else [geo["output_wh"][0], geo["output_wh"][1],
                                            geo["sensor_mode"], geo["fps"]]
            self.assertEqual(got, want, (framing, crop, output, mode, fps))

    def test_selftest_vectors_match_check_value(self):
        cat = json.loads(_load())
        self.assertEqual(sorted(cat["selftest"]), sorted(k.path for k in R.KEYS))
        for path, vectors in cat["selftest"].items():
            key = R.BY_PATH[path]
            for value, ok in vectors:
                self.assertEqual(R.check_value(key, value) is None, ok, f"{path} {value!r}")


if __name__ == "__main__":
    unittest.main()
