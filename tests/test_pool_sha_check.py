#!/usr/bin/env python3
# filename: test_pool_sha_check.py
# description: Pool day 2026-10-13 — tools/pool_sha_check.sh: deployed pool files vs a git ref (fake ssh, local files).
"""
tools/pool_sha_check.sh gate, no hardware. A fake `ssh` runs the remote command locally (refuses
pi@deadhost); POOL_PI_HOME points the /home/pi paths at a temp dir filled with the files at HEAD.

  1. every file = HEAD -> MATCH lines, "ALL MATCH", exit 0;
  2. one file edited (an old T-DRY copy) -> DIFF + STOP, exit 1;
  3. one file missing -> MISSING, exit 1; an unreachable host -> UNREACHABLE, exit 1.

Run (repo root):  python3 -m pytest -q tests/test_pool_sha_check.py
"""
import os
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "tools", "pool_sha_check.sh")
FILES = {"pool_mode.sh": "tools/pool_mode.sh",
         "BM_Devel_Pi/pool_sequence.py": "BM_Devel_Pi/pool_sequence.py",
         "BM_Devel_Pi/rc_preview.py": "BM_Devel_Pi/rc_preview.py",
         "pool_tools/pool_cmd.py": "tools/pool_cmd.py",
         "pool_tools/pool_preview.py": "tools/pool_preview.py",
         "pool_tools/rig_map.json": "tools/rig_map.json"}
FAKE_SSH = """#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -o|-l|-p|-i) shift 2;; -*) shift;; *) break;; esac; done
H="$1"; shift
[ "$H" = "pi@deadhost" ] && { echo "ssh: connect to host deadhost: Connection refused" >&2; exit 255; }
exec bash -c "$*"
"""


class TestShaCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        for dst, src in FILES.items():
            p = os.path.join(self.home, dst)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            data = subprocess.run(["git", "show", f"HEAD:{src}"], cwd=REPO_ROOT, capture_output=True,
                                  check=True).stdout
            with open(p, "wb") as f:
                f.write(data)
        bindir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "ssh"), "w") as f:
            f.write(FAKE_SSH)
        os.chmod(os.path.join(bindir, "ssh"), 0o755)
        self.env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], POOL_PI_HOME=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, *extra):
        return subprocess.run(["bash", SCRIPT, "--ref", "HEAD", "--unit", "pi@bmcam003", "--unit", "pi@bmcam004",
                               "--monitor", "pi@nereus000"] + list(extra),
                              cwd=REPO_ROOT, env=self.env, capture_output=True, text=True, timeout=60)

    def test_all_match(self):
        r = self.run_check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("MATCH    "), 3 * 2 + 3)
        self.assertIn("ALL MATCH HEAD", r.stdout)

    def test_stale_copy_is_a_stop(self):
        with open(os.path.join(self.home, "pool_mode.sh"), "a") as f:
            f.write("# old T-DRY copy\n")
        r = self.run_check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("DIFF     pi@bmcam003", r.stdout)
        self.assertIn("pool_mode.sh deployed=", r.stdout)
        self.assertIn("STOP:", r.stdout)

    def test_missing_and_unreachable(self):
        os.remove(os.path.join(self.home, "pool_tools", "pool_preview.py"))
        r = self.run_check("--unit", "pi@deadhost")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("MISSING  pi@nereus000", r.stdout)
        self.assertIn("UNREACHABLE pi@deadhost", r.stdout)
        self.assertIn("STOP: 2 mismatch(es)", r.stdout)


if __name__ == "__main__":
    unittest.main()
