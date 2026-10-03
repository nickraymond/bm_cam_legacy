#!/usr/bin/env python3
# filename: test_s28_hil_r0_analyze.py
# description: Sprint28 — offline proof of hil/tools/hil_s28_r0_analyze.py on synthetic probe output (the probe itself needs a Pi).
"""Builds the files hil_s28_r0_probe.sh pulls (captures.csv, cma_samples.csv, encodes.csv,
env.txt, dmesg_tail.txt) and checks the R0.1-R0.4 verdicts and the predicted wake."""

import csv
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "hil_s28_r0_analyze", os.path.join(REPO, "hil", "tools", "hil_s28_r0_analyze.py"))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

ENV = ("2026-10-05T16:00:00Z\nbmcam003\ncjxl: /usr/bin/cjxl JPEG XL encoder v0.11.1\n"
       "numpy 2.2.4\nCmaTotal: 131072 kB\n"
       'guard {"rc": -9, "kind": "mem", "seconds": 0.4, "peak_rss_kb": 250000}\n')


def write(run, host, *, raw_dng=10, cma_raw=1500, raw_s=6.0, rung_s=6.4, rss=31000,
          presets=("1600x900", "2000x1124", "2400x1350"), fail_preset=None, env=ENV, dmesg=""):
    p = os.path.join(run, "pulled", f"{host}_r0")
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "captures.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["mode", "i", "rc", "elapsed_s", "jpeg_bytes", "dng_bytes"])
        for i in range(1, 11):
            wr.writerow(["plain", i, 0, 5.0, 4_000_000, 0])
        for i in range(1, 11):
            wr.writerow(["raw", i, 0, raw_s, 4_000_000, 24_029_068 if i <= raw_dng else 0])
    with open(os.path.join(p, "cma_samples.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["label", "t", "cma_free_kb", "mem_available_kb"])
        for i in range(1, 11):
            wr.writerow([f"cap_plain_{i}", 1.0, 1900, 200000])
            wr.writerow([f"cap_raw_{i}", 2.0, cma_raw, 190000])
        for pr in presets:
            wr.writerow([f"enc_{pr}_1", 3.0, 120000, 150000])
    with open(os.path.join(p, "encodes.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["preset", "i", "rc", "wall_s", "bytes", "distance", "cjxl_s_sum",
                     "peak_rss_kb", "rfb"])
        for k, pr in enumerate(presets):
            for i in range(1, 11):
                bad = pr == fail_preset
                wr.writerow([pr, i, 1 if bad else 0, 9.0, 55000, 3.8, rung_s * (1 + k),
                             rss * (1 + k), "mem" if bad else ""])
    with open(os.path.join(p, "env.txt"), "w") as fh:
        fh.write(env)
    with open(os.path.join(p, "dmesg_tail.txt"), "w") as fh:
        fh.write(dmesg)


class Analyze(unittest.TestCase):
    def run_it(self, **kw):
        with tempfile.TemporaryDirectory() as run:
            write(run, "bmcam003", **kw)
            rc = A.main([run, "bmcam003"])
            with open(os.path.join(run, "analysis", "r0_verdict_bmcam003.json")) as fh:
                v = json.load(fh)
            with open(os.path.join(run, "analysis", "r0_budget_bmcam003.csv")) as fh:
                budget = list(csv.DictReader(fh))
            return rc, v, budget

    def test_all_pass_and_the_largest_passing_preset(self):
        rc, v, budget = self.run_it()
        self.assertEqual(rc, 0)
        self.assertEqual([v[k]["verdict"] for k in ("R0.1", "R0.2", "R0.3", "R0.4")], ["PASS"] * 4)
        self.assertEqual(v["R0.2"]["delta_s"], 1.0)
        # 2400x1350 is 3 x 6.4 s per rung: wake = 9 + 6 + 2.5 + 1 + 38.4 + 231.4 + 150 = 438.3 s
        self.assertEqual(v["R0.3"]["largest_passing_preset"], "2400x1350")
        self.assertAlmostEqual(float(budget[2]["predicted_end_s"]), 438.3, places=1)

    def test_cma_below_1_mb_or_a_missing_dng_fails_r01(self):
        self.assertEqual(self.run_it(cma_raw=900)[1]["R0.1"]["verdict"], "FAIL")
        self.assertEqual(self.run_it(raw_dng=9)[1]["R0.1"]["verdict"], "FAIL")
        self.assertEqual(self.run_it(dmesg="unicam: dma alloc error\n")[1]["R0.1"]["verdict"],
                         "FAIL")

    def test_slow_raw_capture_fails_r02(self):
        self.assertEqual(self.run_it(raw_s=8.5)[1]["R0.2"]["verdict"], "FAIL")

    def test_the_first_preset_gates_r03_and_a_failure_stops_the_ladder(self):
        rc, v, _ = self.run_it(fail_preset="2000x1124")
        self.assertEqual(rc, 0)                                 # 1600x900 passes: R0.3 PASS
        self.assertEqual(v["R0.3"]["largest_passing_preset"], "1600x900")
        self.assertEqual(self.run_it(fail_preset="1600x900")[1]["R0.3"]["verdict"], "FAIL")
        self.assertEqual(self.run_it(rung_s=21.0)[1]["R0.3"]["verdict"], "FAIL")
        self.assertEqual(self.run_it(rss=130 * 1024)[1]["R0.3"]["verdict"], "FAIL")

    def test_missing_tools_or_a_toothless_memory_guard_fail_r04(self):
        self.assertEqual(self.run_it(env="cjxl:  \nnumpy missing\n")[1]["R0.4"]["verdict"], "FAIL")
        weak = ENV.replace('"kind": "mem"', '"kind": "ok"')
        self.assertEqual(self.run_it(env=weak)[1]["R0.4"]["verdict"], "FAIL")


class NotMeasured(unittest.TestCase):
    def test_an_empty_cma_log_is_not_scored(self):
        with tempfile.TemporaryDirectory() as run:
            write(run, "bmcam003")
            path = os.path.join(run, "pulled", "bmcam003_r0", "cma_samples.csv")
            with open(path, "w") as fh:
                fh.write("label,t,cma_free_kb,mem_available_kb\n")      # the #120 bug's output
            self.assertEqual(A.main([run, "bmcam003"]), 2)
            self.assertFalse(os.path.exists(os.path.join(run, "analysis",
                                                         "r0_verdict_bmcam003.json")))

    def test_a_cma_log_without_raw_capture_rows_is_not_scored(self):
        with tempfile.TemporaryDirectory() as run:
            write(run, "bmcam003")
            path = os.path.join(run, "pulled", "bmcam003_r0", "cma_samples.csv")
            with open(path) as fh:
                rows = [ln for ln in fh if not ln.startswith("cap_raw_")]
            with open(path, "w") as fh:
                fh.writelines(rows)
            self.assertEqual(A.main([run, "bmcam003"]), 2)


PROBE = os.path.join(REPO, "hil", "tools", "hil_s28_r0_probe.sh")
FAKE_MEMINFO = "MemTotal:  427000 kB\nMemAvailable:  190000 kB\nCmaTotal:  131072 kB\nCmaFree:  1900 kB\n"
BUGGY = """python3 - <<'PY' > cma_samples.csv 2> sampler.err < /dev/null &
import sys
print("label,t,cma_free_kb,mem_available_kb", flush=True)
PY"""


def _sampler_block():
    with open(PROBE) as fh:
        text = fh.read()
    return text[text.index("# >>> sampler"):text.index("# <<< sampler")]


@unittest.skipIf(shutil.which("bash") is None or shutil.which("python3") is None, "needs bash")
class ProbeSampler(unittest.TestCase):
    """Runs the probe's OWN sampler block (not a copy) against a fake meminfo."""

    def run_block(self, block):
        with tempfile.TemporaryDirectory() as d:
            meminfo = os.path.join(d, "meminfo")
            with open(meminfo, "w") as fh:
                fh.write(FAKE_MEMINFO)
            env = dict(os.environ, S28R0_MEMINFO=meminfo)
            r = subprocess.run(["bash", "-c", "set -u\n" + block + "\nsleep 0.3\n"
                                "echo cap_raw_1 > label.txt; sleep 0.3; echo stop > label.txt; wait"],
                               cwd=d, env=env, capture_output=True, text=True, timeout=30)
            time.sleep(0.2)
            with open(os.path.join(d, "cma_samples.csv")) as fh:
                rows = list(csv.DictReader(fh))
            return r, rows

    def test_the_sampler_writes_rows(self):
        r, rows = self.run_block(_sampler_block())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CMA sampler running", r.stdout)
        self.assertGreaterEqual(len(rows), 5)
        self.assertEqual({row["cma_free_kb"] for row in rows}, {"1900"})
        self.assertTrue(any(row["label"] == "cap_raw_1" for row in rows))
        self.assertEqual(rows[-1]["label"], "stop")

    def test_the_guard_catches_the_original_heredoc_bug(self):
        block = re.sub(r"python3 sampler\.py > cma_samples\.csv 2> sampler\.err < /dev/null &",
                       lambda _m: BUGGY, _sampler_block())
        self.assertIn("python3 - <<'PY'", block)
        r, rows = self.run_block(block)
        self.assertEqual(r.returncode, 3)
        self.assertIn("FATAL: the CMA sampler wrote", r.stdout)
        self.assertEqual(rows, [])

    def test_no_heredoc_program_also_takes_dev_null(self):
        with open(PROBE) as fh:
            for n, line in enumerate(fh, 1):
                code = line.split("#", 1)[0]
                self.assertFalse("<<'" in code and "< /dev/null" in code,
                                 f"line {n}: a heredoc program with < /dev/null runs EMPTY")


if __name__ == "__main__":
    unittest.main()
