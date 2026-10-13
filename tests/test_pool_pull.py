#!/usr/bin/env python3
# filename: test_pool_pull.py
# description: Pool test 2026-10-13 — tools/pool_pull.py end to end with the real local rsync and a fake ssh (runs the "remote" side locally).
"""
tools/pool_pull.py gate, no hardware. A fake `ssh` on PATH drops the options, takes the host
as a folder name under FAKE_HOMES, cd's there and runs the remote command with sh, so the REAL
rsync (Apple 2.6.9 on the Mac) and the real remote python3 helpers run end to end.

  1. classify(): strict priority classes;
  2. two units pulled in parallel: every file byte-identical, sha256 ok in pull_report.json,
     classes pulled in order 1..5 per unit, exit 0;
  3. a corrupted local file with the same size + mtime (rsync quick-check skips it) is caught
     by the sha256 check, deleted, re-pulled -> ok;
  4. --classes 1,2 -> class 3+ reported "skipped", exit 0; one unit unreachable -> exit 1,
     the other still completes;
  5. the dest already pinned to a different run -> exit 2, nothing copied;
  6. --dry-run lists sizes per class with a projected time and copies nothing.

Run (repo root):  python3 -m pytest -q tests/test_pool_pull.py
"""
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import pool_pull as pp  # noqa: E402

FAKE_SSH = """#!/bin/bash
# fake ssh for tests: skip options, host -> $FAKE_HOMES/<host minus user@>, run the command there
while [ $# -gt 0 ]; do case "$1" in -o|-l|-p|-i|-c) shift 2;; -*) shift;; *) break;; esac; done
H="${1#*@}"; shift
[ -d "$FAKE_HOMES/$H" ] || { echo "ssh: connect to host $H: Connection refused" >&2; exit 255; }
cd "$FAKE_HOMES/$H" && exec /bin/sh -c "$*"
"""


def make_run(home, unit, run="20261013_181500"):
    """A small pool run folder in every class."""
    d = os.path.join(home, "pool_runs", f"{run}_{unit}")
    t = "20261013T181500Z"
    files = {   # names as BM_Devel_Pi/pool_sequence.py (#152) writes them
        "run_manifest.json": b'{"unit": "%s"}' % unit.encode(),
        "pool.log": b"start\nstop\n",
        f"loop/{t}_loop_01_still_compressed.nrjxl": os.urandom(12000),
        f"loop/{t}_loop_01_still_compressed.nrjxl.capture_metadata.json": b"{}",
        f"loop/{t}_loop_01_still_compressed.jpg": os.urandom(9000),
        f"loop/{t}_loop_01_still.metadata.json": b"{}",
        f"loop/{t}_loop_01_still.stdout.log": b"ok",
        f"loop/{t}_loop_01_still.jpg": os.urandom(30000),
        f"loop/{t}_loop_01_still.dng": os.urandom(60000),
        f"preview/{t}_preview_181500_preview.jpg": os.urandom(4000),
        f"preview/{t}_preview_181500.jpg": os.urandom(20000),
        f"calibration/{t}_cal_0001.dng": os.urandom(200000),
        f"calibration/{t}_cal_0001.jpg": os.urandom(50000),
        f"loop/{t}_loop_01_video.mp4": os.urandom(80000),
        "notes/extra.txt": b"x",
    }
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    # an older run that 'latest' must not pick
    os.makedirs(os.path.join(home, "pool_runs", f"20261012_090000_{unit}"), exist_ok=True)
    with open(os.path.join(home, "pool_runs", f"20261012_090000_{unit}", "pool.log"), "w") as f:
        f.write("old\n")
    return d, files


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.homes = os.path.join(t, "homes")
        self.dest = os.path.join(t, "NereusPool")
        bindir = os.path.join(t, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "ssh"), "w") as f:
            f.write(FAKE_SSH)
        os.chmod(os.path.join(bindir, "ssh"), stat.S_IRWXU)
        self.runs = {}
        for u in ("bmcam003", "bmcam004"):
            self.runs[u] = make_run(os.path.join(self.homes, u), u)
        self.env = mock.patch.dict(os.environ, {"PATH": bindir + os.pathsep + os.environ["PATH"],
                                                "FAKE_HOMES": self.homes})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def pull(self, *extra, hosts=("bmcam003=bmcam003", "bmcam004=bmcam004")):
        args = ["--remote-root", "pool_runs", "--dest-root", self.dest, "--progress-s", "0.2",
                "--retries", "1"]
        for h in hosts:
            args += ["--host", h]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = pp.main(args + list(extra))
        return rc, out.getvalue()

    def report(self):
        with open(os.path.join(self.dest, "20261013", "pull_report.json")) as f:
            return json.load(f)


class TestClassify(unittest.TestCase):
    def test_classes(self):
        c, t = pp.classify, "20261013T181500Z"
        self.assertEqual(c("run_manifest.json"), 1)
        self.assertEqual(c("pool.log"), 1)
        self.assertEqual(c(f"loop/{t}_loop_01_still.stdout.log"), 1)
        # 2 = loop products ONLY: nrjxl + pjpg + sidecars, the small preview
        self.assertEqual(c(f"loop/{t}_loop_01_still_compressed.nrjxl"), 2)
        self.assertEqual(c(f"loop/{t}_loop_01_still_compressed.jpg"), 2)
        self.assertEqual(c(f"loop/{t}_loop_01_still_compressed.nrjxl.capture_metadata.json"), 2)
        self.assertEqual(c(f"loop/{t}_loop_01_still.metadata.json"), 2)
        self.assertEqual(c(f"preview/{t}_preview_181500_preview.jpg"), 2)
        # 3 = calibration JPEGs (+ json) only; 4 videos; 5 loop native JPEGs (no DNG)
        self.assertEqual(c(f"calibration/{t}_cal_0001.jpg"), 3)
        self.assertEqual(c(f"calibration/{t}_cal_0001.metadata.json"), 3)
        self.assertEqual(c(f"loop/{t}_loop_01_video.mp4"), 4)
        self.assertEqual(c("calibration/clip.h264"), 4)
        self.assertEqual(c(f"loop/{t}_loop_01_still.jpg"), 5)
        self.assertEqual(c(f"preview/{t}_preview_181500.jpg"), 5)
        self.assertEqual(c("notes/extra.txt"), 6)
        # 7 = every DNG (Nick 10/12: stays on the unit unless --include-dng)
        for rel in (f"calibration/{t}_cal_0001.dng", f"loop/{t}_loop_01_still.dng", f"preview/{t}_preview_181500.dng"):
            self.assertEqual(c(rel), pp.DNG_CLASS)


class TestPull(Base):
    def test_parallel_pull_dngs_left_on_unit(self):
        """Default (pool day): everything but the DNGs; the DNGs are inventoried with the Pi sha256."""
        import hashlib
        rc, out = self.pull()
        self.assertEqual(rc, 0, out)
        rep = self.report()
        for u, (src, files) in self.runs.items():
            ur = rep["units"][u]
            dngs = {r: d for r, d in files.items() if r.endswith(".dng")}
            self.assertEqual(len(dngs), 2)
            self.assertTrue(ur["complete"], ur)
            self.assertEqual(ur["run"], f"20261013_181500_{u}")
            self.assertEqual(ur["counts"], {"ok": len(files) - 2, "left_on_unit": 2})
            self.assertEqual(ur["left_on_unit"]["count"], 2)
            self.assertEqual(ur["left_on_unit"]["bytes"], sum(len(d) for d in dngs.values()))
            self.assertEqual(ur["left_on_unit"]["sha256_recorded"], 2)
            for rel, data in files.items():
                local = os.path.join(self.dest, "20261013", u, rel)
                if rel in dngs:
                    self.assertFalse(os.path.exists(local), rel)                 # never copied
                    self.assertEqual(ur["files"][rel]["status"], "left_on_unit")
                    self.assertEqual(ur["files"][rel]["sha256_pi"], hashlib.sha256(data).hexdigest())
                else:
                    with open(local, "rb") as f:
                        self.assertEqual(f.read(), data, rel)
                    self.assertEqual(ur["files"][rel]["sha256_pi"], ur["files"][rel]["sha256_mac"])
            starts = [int(l.split(" class ")[1].split()[0]) for l in out.splitlines()
                      if f"[{u}] class " in l and "-> start" in l]
            self.assertEqual(starts, [1, 2, 3, 4, 5, 6])
        self.assertIn("LEFT ON UNIT: 2 DNGs", out)
        self.assertTrue(os.path.exists(os.path.join(self.dest, "20261013", "pull.log")))

    def test_include_dng_pulls_them_last(self):
        """Friday pickup: --include-dng adds the DNGs as the last class; a re-run resumes."""
        self.pull()                                               # pool day first
        rc, out = self.pull("--include-dng")
        self.assertEqual(rc, 0, out)
        for u, (src, files) in self.runs.items():
            ur = self.report()["units"][u]
            self.assertEqual(ur["counts"], {"ok": len(files)})
            self.assertEqual(ur["left_on_unit"]["count"], 0)
            for rel, data in files.items():
                with open(os.path.join(self.dest, "20261013", u, rel), "rb") as f:
                    self.assertEqual(f.read(), data, rel)
            starts = [int(l.split(" class ")[1].split()[0]) for l in out.splitlines()
                      if f"[{u}] class " in l and "-> start" in l]
            self.assertEqual(starts, [1, 2, 3, 4, 5, 6, 7])

    def test_dng_class_needs_flag(self):
        rc, out = self.pull("--classes", "7")
        self.assertEqual(rc, 2)

    def test_no_dng_sha(self):
        rc, out = self.pull("--no-dng-sha")
        self.assertEqual(rc, 0, out)
        ur = self.report()["units"]["bmcam003"]
        self.assertEqual(ur["left_on_unit"]["count"], 2)
        self.assertEqual(ur["left_on_unit"]["sha256_recorded"], 0)

    def test_resume_and_sha_mismatch_repulled(self):
        src, files = self.runs["bmcam003"]
        rel = "calibration/20261013T181500Z_cal_0001.jpg"
        local = os.path.join(self.dest, "20261013", "bmcam003", rel)
        os.makedirs(os.path.dirname(local))
        with open(local, "wb") as f:
            f.write(b"\0" * len(files[rel]))
        st = os.stat(os.path.join(src, rel))
        os.utime(local, (st.st_atime, st.st_mtime))      # quick-check would skip it
        rc, out = self.pull()
        self.assertEqual(rc, 0, out)
        self.assertIn("sha256 mismatch", out)
        with open(local, "rb") as f:
            self.assertEqual(f.read(), files[rel])
        self.assertEqual(self.report()["units"]["bmcam003"]["files"][rel]["status"], "ok")

    def test_classes_subset_skips(self):
        rc, out = self.pull("--classes", "1,2")
        self.assertEqual(rc, 0, out)
        ur = self.report()["units"]["bmcam004"]
        t = "20261013T181500Z"
        self.assertEqual(ur["files"][f"calibration/{t}_cal_0001.jpg"]["status"], "skipped")
        self.assertEqual(ur["files"][f"loop/{t}_loop_01_still.dng"]["status"], "left_on_unit")
        self.assertEqual(ur["files"][f"loop/{t}_loop_01_still_compressed.nrjxl"]["status"], "ok")
        self.assertEqual(ur["files"][f"loop/{t}_loop_01_still_compressed.jpg"]["status"], "ok")
        self.assertFalse(os.path.exists(os.path.join(self.dest, "20261013", "bmcam004", "calibration")))
        self.assertIn("SKIPPED by --classes", out)

    def test_unreachable_unit_exit_1_other_completes(self):
        rc, out = self.pull(hosts=("bmcam003=bmcam003", "bmcam009=nohost"))
        self.assertEqual(rc, 1, out)
        self.assertIn("UNREACHABLE", out)
        rep = self.report()
        self.assertTrue(rep["units"]["bmcam003"]["complete"])
        self.assertIn("error", rep["units"]["bmcam009"])

    def test_dest_pinned_to_other_run_refuses(self):
        pin = os.path.join(self.dest, "20261013", "bmcam003", pp.STATE_DIR)
        os.makedirs(pin)
        with open(os.path.join(pin, "run.txt"), "w") as f:
            f.write("20261013_090000_bmcam003\n")
        rc, out = self.pull()
        self.assertEqual(rc, 2, out)
        self.assertIn("REFUSED", out)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "20261013", "bmcam003", "pool.log")))

    def test_per_unit_run_pick(self):
        # an older run per unit; pin each unit to it explicitly (latest would pick 20261013)
        rc, out = self.pull("--run", "bmcam003=20261012_090000_bmcam003",
                            "--run", "bmcam004=20261012_090000_bmcam004", "--date", "old")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.dest, "old", "pull_report.json")) as f:
            rep = json.load(f)
        self.assertEqual(rep["units"]["bmcam003"]["run"], "20261012_090000_bmcam003")
        self.assertEqual(rep["units"]["bmcam004"]["run"], "20261012_090000_bmcam004")
        self.assertEqual(rep["units"]["bmcam004"]["counts"], {"ok": 1})

    def test_streams_cipher_whole_file(self):
        """Speed knobs (10/12 pull-rate finding): 3 parallel streams per class, ssh -c, rsync -W."""
        rc, out = self.pull("--streams", "3", "--cipher", "aes128-gcm@openssh.com", "--whole-file", "--include-dng")
        self.assertEqual(rc, 0, out)
        self.assertIn("streams=3 cipher=aes128-gcm@openssh.com whole-file dng=PULL", out)
        rep = self.report()
        for u, (src, files) in self.runs.items():
            self.assertEqual(rep["units"][u]["counts"], {"ok": len(files)})
            for rel, data in files.items():
                with open(os.path.join(self.dest, "20261013", u, rel), "rb") as f:
                    self.assertEqual(f.read(), data, rel)
        state = os.path.join(self.dest, "20261013", "bmcam003", pp.STATE_DIR)
        logs = sorted(f for f in os.listdir(state) if f.startswith("rsync_class1s"))
        self.assertEqual(logs, ["rsync_class1s1.log", "rsync_class1s2.log", "rsync_class1s3.log"])  # 3 log files -> 3 streams
        text = open(os.path.join(state, logs[0])).read()
        self.assertIn("-c aes128-gcm@openssh.com", text)
        self.assertIn(" -W ", text)

    def test_streams_bounds(self):
        rc, out = self.pull("--streams", "9")
        self.assertEqual(rc, 2)

    def test_dry_run(self):
        rc, out = self.pull("--dry-run", "--probe-mb", "1")
        self.assertEqual(rc, 0, out)
        self.assertIn("3 calibration JPEGs", out)
        self.assertIn("5 loop native JPEGs", out)
        self.assertIn("LEFT ON UNIT: --include-dng to pull", out)
        self.assertIn("projected wall time", out)
        self.assertIn("MB/s (measured)", out)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "20261013", "bmcam003")))

    def test_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            pp.main(["--help"])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("strict priority", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
