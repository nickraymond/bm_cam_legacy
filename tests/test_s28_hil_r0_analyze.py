#!/usr/bin/env python3
# filename: test_s28_hil_r0_analyze.py
# description: Sprint28 — offline proof of hil/tools/hil_s28_r0_analyze.py on synthetic probe output (the probe itself needs a Pi).
"""Builds the files hil_s28_r0_probe.sh pulls (captures.csv, cma_samples.csv, encodes.csv,
env.txt, dmesg_tail.txt) and checks the R0.1-R0.4 verdicts and the predicted wake."""

import csv
import importlib.util
import json
import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
