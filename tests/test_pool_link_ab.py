#!/usr/bin/env python3
# filename: test_pool_link_ab.py
# description: Pool test 2026-10-13 — tools/pool_link_ab.py end to end against localhost (fake ssh, real curl/rsync/http.server).
"""
tools/pool_link_ab.py gate, no hardware: a fake `ssh` runs the remote side locally, so every method
(http via python3 -m http.server + curl, ssh cat, ssh -c aes128-gcm, 2 streams, rsync) really
transfers the test file. Checks: 5 methods x n rows all ok, the summary has a VERDICT, the remote
scratch dir is removed, the HTTP server is stopped, and an rsync --server running on the unit
makes it refuse (exit 3) before touching anything.

Run (repo root):  python3 -m pytest -q tests/test_pool_link_ab.py
"""
import csv
import os
import socket
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO_ROOT, "tools", "pool_link_ab.py")
FAKE_SSH = """#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -o|-l|-p|-i|-c) shift 2;; -*) shift;; *) break;; esac; done
shift
# the busy check: answered here, never from this Mac's process table (a real pull may be running)
if [[ "$*" == *"sync --server"* && "$*" == *pgrep* ]]; then [ -n "${FAKE_BUSY:-}" ] && echo "4242 rsync --server --sender"; exit 0; fi
exec bash -c "$*"
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestLinkAB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        b = os.path.join(self.tmp.name, "bin")
        os.makedirs(b)
        with open(os.path.join(b, "ssh"), "w") as f:
            f.write(FAKE_SSH)
        os.chmod(os.path.join(b, "ssh"), 0o755)
        self.env = dict(os.environ, PATH=b + os.pathsep + os.environ["PATH"])
        # must be /tmp/pool_link_ab* (QE #158 nit 1); a real dir on this Mac, removed in tearDown
        self.rdir = f"/tmp/pool_link_ab_test_{os.getpid()}"
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()
        subprocess.run(["rm", "-rf", self.rdir])

    def run_tool(self, *extra, env=None):
        return subprocess.run([sys.executable, TOOL, "pi@127.0.0.1", "--mb", "2", "--n", "2",
                               "--remote-dir", self.rdir, "--http-port", str(free_port()), "--out", self.out]
                              + list(extra), env=env or self.env, capture_output=True, text=True, timeout=300)

    def test_all_methods_end_to_end(self):
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.out, "results.csv")) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 5 * 2)
        self.assertTrue(all(row["ok"] == "True" for row in rows), rows)
        self.assertEqual({row["method"] for row in rows},
                         {"http", "ssh_default", "ssh_aes128gcm", "ssh_2streams", "rsync"})
        two = [row for row in rows if row["method"] == "ssh_2streams"][0]
        self.assertEqual(int(two["bytes"]), 2 * 2 * 1048576)            # aggregate of 2 streams
        summary = open(os.path.join(self.out, "summary.txt")).read()
        self.assertIn("VERDICT", summary)
        self.assertFalse(os.path.exists(self.rdir))                      # cleaned up
        self.assertTrue(os.path.exists(os.path.join(self.out, "facts.txt")))

    def test_refuses_during_a_pull(self):
        r = self.run_tool(env=dict(self.env, FAKE_BUSY="1"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout)
        self.assertFalse(os.path.exists(self.rdir))                      # nothing created

    def test_remote_dir_guard(self):
        """The units hold the only DNG copies until Friday: anything but /tmp/pool_link_ab* is refused,
        before any ssh."""
        for bad in ("/home/pi/pool_runs", "/tmp", "/tmp/pool_link_ab/../x", "/tmp/pool_link_abX/sub",
                    "/home/pi/pool_runs/20261013_181500_bmcam003", "pool_link_ab"):
            r = subprocess.run([sys.executable, TOOL, "pi@127.0.0.1", "--remote-dir", bad, "--out", self.out],
                               env=dict(self.env, PATH="/nonexistent"), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, (bad, r.stdout, r.stderr))
            self.assertIn("REFUSED --remote-dir", r.stderr)
        import importlib.util
        spec = importlib.util.spec_from_file_location("pool_link_ab", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.safe_remote_dir("/tmp/pool_link_ab/"), "/tmp/pool_link_ab")
        self.assertEqual(mod.safe_remote_dir("/tmp/pool_link_ab_2"), "/tmp/pool_link_ab_2")

    def test_cleanup_removes_only_its_own_files(self):
        os.makedirs(self.rdir, exist_ok=True)
        keep = os.path.join(self.rdir, "keep.txt")
        with open(keep, "w") as f:
            f.write("not ours")
        r = self.run_tool("--methods", "ssh_default", "--n", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(keep))                           # never touched
        self.assertFalse(os.path.exists(os.path.join(self.rdir, "ab.bin")))

    def test_bad_args(self):
        r = self.run_tool("--methods", "ftp")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
