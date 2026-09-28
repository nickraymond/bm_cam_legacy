#!/usr/bin/env python3
# filename: test_s4_config_upgrade.py
# description: Sprint26 S4 — tools/config_v2_upgrade.py rewrites old key names, keeps every value and the hash, dry-run by default.
"""
Review S4a #5 / PLAN_S4.md G11: an S3c-era camera_config.yaml (video.storage.*,
no registry-v5 keys, v2-only edits like stay_on) is rewritten in today's
spelling with the same values and hash; dry-run writes nothing; a file that
does not load strictly is refused (exit 2).

Run (repo root):
  python3 -m unittest tests.test_s4_config_upgrade -v
"""

import contextlib
import io
import os
import sys
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import config_journal  # noqa: E402
import config_v2 as C  # noqa: E402
import config_v2_upgrade as T  # noqa: E402
from tests.test_config_v2 import Unit  # noqa: E402


def s3c_era(u):
    with open(u.v2, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    doc["video"]["storage"] = doc.pop("storage")
    del doc["power"]["bus_always_on"]
    for k in ("keepalive_s", "keepalive_max_s", "hold_max_min"):
        del doc["commands"][k]
    doc["mode"]["run"] = "stay_on"                  # a v2-only edit that must survive
    doc["commands"]["runtime"] = "supervisor"
    doc["commands"]["enabled"] = True
    with open(u.v2, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh)


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = T.main(list(argv))
    return code, out.getvalue()


class Upgrade(unittest.TestCase):
    def test_dry_run_then_write(self):
        u = Unit(self)
        s3c_era(u)
        before = C.load_config(u.v2, strict=True)
        raw = open(u.v2).read()
        code, out = run(u.v2)
        self.assertEqual(code, 0, out)
        self.assertIn("dry-run", out)
        self.assertEqual(open(u.v2).read(), raw)
        code, out = run(u.v2, "--write")
        self.assertEqual(code, 0, out)
        after = C.load_config(u.v2, strict=True)
        self.assertEqual((after.hash, after.base), (before.hash, before.base))
        self.assertEqual(after.warnings, [])
        doc = yaml.safe_load(open(u.v2))
        self.assertIn("storage", doc)
        self.assertEqual(doc["mode"]["run"], "stay_on")
        backups = [f for f in os.listdir(u.dir) if ".before_upgrade_" in f]
        self.assertEqual(len(backups), 1)
        self.assertEqual(open(os.path.join(u.dir, backups[0])).read(), raw)
        lines = config_journal.read(config_journal.path_beside(before.base["commands.state_path"]))
        self.assertEqual(lines[-1]["src"], "deploy")
        code, out = run(u.v2, "--write")
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)

    def test_not_strict_refused(self):
        u = Unit(self)
        u.edit_v2("camera.white_balance.mode", "manual")      # no gains: strict S4 rule
        raw = open(u.v2).read()
        code, out = run(u.v2, "--write")
        self.assertEqual(code, 2, out)
        self.assertEqual(open(u.v2).read(), raw)


if __name__ == "__main__":
    unittest.main()
