#!/usr/bin/env python3
# filename: test_s4_dev_mode.py
# description: Sprint26 S4 b.7 — tools/dev_mode.sh on a migrated unit: v8 hlt with a console-range id, folded into the v9 overlay (on) and back out (off).
"""
Sprint26 S4 commit b.7 (PLAN_S4.md b.7; consensus R17; review S4a #1).

Runs the REAL tools/dev_mode.sh against a temp migrated unit (crontab
untouched via BMCAM_DEV_MODE_NO_CRON). Pins: `on` records v8 hlt 3 with an id
in the console range; the v9 fold then turns the halt off in the overlay;
`off` records hlt 0 and the next fold returns the halt keys to the YAML;
`status` shows the v9 overlay line.

Run (repo root):
  python3 -m unittest tests.test_s4_dev_mode -v
"""

import json
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_state_v9 as S  # noqa: E402
import config_migrate  # noqa: E402
from tests.test_config_v2 import Unit  # noqa: E402

TOOL = os.path.join(REPO_ROOT, "tools", "dev_mode.sh")


class DevMode(unittest.TestCase):
    def run_tool(self, u, mode):
        env = dict(os.environ, BMCAM_DST=u.dir, BMCAM_DEV_MODE_NO_CRON="1",
                   BMCAM_CYCLE_LOCK=os.path.join(u.dir, "cycle.lock"),
                   PYTHONPATH=os.path.join(REPO_ROOT, "BM_Devel_Pi"))
        r = subprocess.run(["bash", TOOL, mode], env=env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_on_off_through_the_fold(self):
        u = Unit(self)
        path = u.values["commands.state_path"]
        self.assertEqual(os.path.dirname(path), u.dir)
        out = self.run_tool(u, "on")
        self.assertIn("recorded hlt=3", out)
        v8 = json.load(open(path))["v8"]
        self.assertEqual(v8["settings"]["hlt"], 3)
        self.assertTrue(all(1 <= i <= 99_999 for i in v8["applied_ids"]))
        st = S.V9State(path, log=lambda *_: None)
        st.fold_v8(config_migrate.overlay_from_v8)
        self.assertIs(st.overlay.get("power.halt.enabled"), False)
        self.assertIn("v9 overlay", self.run_tool(u, "status"))
        self.run_tool(u, "off")
        st = S.V9State(path, log=lambda *_: None)
        st.fold_v8(config_migrate.overlay_from_v8)
        self.assertNotIn("power.halt.enabled", st.overlay)
        self.assertNotIn("power.halt.dry_run", st.overlay)


if __name__ == "__main__":
    unittest.main()
