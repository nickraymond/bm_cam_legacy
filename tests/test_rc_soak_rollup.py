#!/usr/bin/env python3
# filename: test_rc_soak_rollup.py
# description: Sprint08 P8 — offline tests for the soak rollup parser (fixture logs).
"""
Sprint08 P8 — tests for tools/bm_rc_soak_rollup.py.

Fixture logs replicate the exact line formats the wrapper + orchestrator
emit (complete cycle, bounded-incomplete cycle, truncated-at-halt log,
error cycle). Pure offline: no SSH, no Pi.

Run (repo root, stdlib only):
  python3 -m unittest tests.test_rc_soak_rollup -v
  # or: python3 tests/test_rc_soak_rollup.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

from bm_rc_soak_rollup import parse_cycle_log, summarize  # noqa: E402

COMPLETE_LOG = """============================================================
[RC-CRON] Sprint08 progressive-JPEG RC cycle starting
[RC-CRON] start_utc=2026-07-26T12:00:31+00:00
[RC] cycle start: budget=960s pacing=5.0s/msg
[RC] schedule gate: Within transmit window 00:01-23:59 America/New_York; local_time=x
[RC] attempt q90: 72614 B, 323 msgs, over_cap=True, budget_fit=False
[RC] attempt q80: 49011 B, 218 msgs, over_cap=True, budget_fit=False
[RC] attempt q70: 39300 B, 175 msgs, over_cap=False, budget_fit=True
[RC] selection: quality=70 attempts=3 fits=True reason=fit
[RC] final JPEG: /home/pi/BM_Devel_Pi/images/x_compressed.jpg (39300 B, 175 msgs, sha256=abc...)
[RC] transmit done: sent=175/175 complete=True incomplete_emitted=False uart=880.2s
[RC][halt] executing: sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh (SSH/process death after this is SUCCESS)
[RC][halt] halt command returned 0; system shutdown in progress
[RC] cycle end: elapsed=905.1s of 960s; halt=halt_initiated
[RC-CRON] rc_progressive_jpeg.py exit_code=0
"""

INCOMPLETE_LOG = """[RC-CRON] start_utc=2026-07-26T13:00:31+00:00
[RC] cycle start: budget=960s pacing=5.0s/msg
[RC] attempt q90: 90000 B, 400 msgs, over_cap=True, budget_fit=False
[RC] attempt q80: 70000 B, 311 msgs, over_cap=True, budget_fit=False
[RC] attempt q70: 60000 B, 267 msgs, over_cap=True, budget_fit=False
[RC] attempt q60: 55000 B, 245 msgs, over_cap=True, budget_fit=False
[RC] attempt q50: 52000 B, 232 msgs, over_cap=True, budget_fit=False
[RC] attempt q40: 50000 B, 223 msgs, over_cap=True, budget_fit=False
[RC] attempt q30: 48800 B, 217 msgs, over_cap=True, budget_fit=False
[RC] attempt q25: 47000 B, 209 msgs, over_cap=True, budget_fit=False
[RC] attempt q20: 46000 B, 205 msgs, over_cap=True, budget_fit=False
[RC] attempt q15: 45500 B, 203 msgs, over_cap=True, budget_fit=False
[RC] attempt q13: 45000 B, 200 msgs, over_cap=True, budget_fit=False
[RC] attempt q11: 44500 B, 198 msgs, over_cap=True, budget_fit=False
[RC] attempt q9: 44100 B, 196 msgs, over_cap=True, budget_fit=False
[RC] selection: quality=9 attempts=13 fits=False reason=no_fit_cap
[RC] transmit done: sent=180/196 complete=False incomplete_emitted=True uart=915.0s
[RC][halt] halt command returned 0; system shutdown in progress
[RC] cycle end: elapsed=940.0s of 960s; halt=halt_initiated
"""

TRUNCATED_LOG = """[RC-CRON] start_utc=2026-07-26T14:00:31+00:00
[RC] cycle start: budget=960s pacing=5.0s/msg
[RC] attempt q70: 39300 B, 175 msgs, over_cap=False, budget_fit=True
[RC] selection: quality=70 attempts=1 fits=True reason=fit
[RC] transmit done: sent=175/175 complete=True incomplete_emitted=False uart=880.0s
[RC][halt] halt command returned 0; system shutdown in progress
"""

ERROR_LOG = """[RC-CRON] start_utc=2026-07-26T15:00:31+00:00
[RC] cycle start: budget=960s pacing=5.0s/msg
[RC][ERROR] cycle failed: Native capture failed after 4 attempts; last_error=...
[RC-CRON] rc_progressive_jpeg.py exit_code=1
"""


def write_logs(dirpath, logs):
    for i, text in enumerate(logs):
        Path(dirpath, f"rc_cycle_2026072{i}T000000Z.log").write_text(text)


class TestParseCycleLog(unittest.TestCase):
    def _parse(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "rc_cycle_test.log")
            p.write_text(text)
            return parse_cycle_log(p)

    def test_complete_cycle(self):
        row = self._parse(COMPLETE_LOG)
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["quality"], 70)
        self.assertEqual(row["attempts"], 3)
        self.assertEqual((row["sent"], row["planned"]), (175, 175))
        self.assertTrue(row["halt_initiated"])
        self.assertEqual(row["halt"], "halt_initiated")
        self.assertAlmostEqual(row["elapsed_s"], 905.1)
        self.assertIn("q90:323msgs", row["attempt_qualities"])

    def test_incomplete_cycle(self):
        row = self._parse(INCOMPLETE_LOG)
        self.assertEqual(row["status"], "incomplete_bounded")
        self.assertEqual(row["quality"], 9)
        self.assertEqual(row["attempts"], 13)  # full field-trial ladder walked
        self.assertEqual(row["reason"], "no_fit_cap")
        self.assertTrue(row["incomplete_emitted"])
        self.assertEqual((row["sent"], row["planned"]), (180, 196))

    def test_truncated_log_counts_as_halted_success(self):
        row = self._parse(TRUNCATED_LOG)
        self.assertTrue(row["halt_initiated"])
        self.assertEqual(row["status"], "complete")  # transmit line present
        self.assertIsNone(row["elapsed_s"])          # cycle-end line lost — tolerated

    def test_error_cycle(self):
        row = self._parse(ERROR_LOG)
        self.assertEqual(row["status"], "error")
        self.assertIn("Native capture failed", row["errors"])


class TestSummarize(unittest.TestCase):
    def test_acceptance_over_mixed_soak(self):
        with tempfile.TemporaryDirectory() as d:
            write_logs(d, [COMPLETE_LOG, INCOMPLETE_LOG, TRUNCATED_LOG, ERROR_LOG])
            rows = [parse_cycle_log(p) for p in sorted(Path(d).glob("rc_cycle_*.log"))]
        s = summarize(rows)
        self.assertEqual(s["cycles_total"], 4)
        self.assertEqual(s["complete_sends"], 2)
        self.assertEqual(s["incomplete_bounded_sends"], 1)
        self.assertEqual(s["error_cycles"], 1)
        self.assertEqual(s["halts_initiated"], 3)
        self.assertEqual(s["adaptive_cycles_gt1_attempt"], 2)
        self.assertEqual(s["quality_histogram"], {"q70": 2, "q9": 1})
        self.assertTrue(s["acceptance"]["progressive_jpeg_sent"])
        self.assertTrue(s["acceptance"]["adaptive_quality_with_attempts_logged"])
        self.assertTrue(s["acceptance"]["incomplete_cycle_logged"])
        self.assertTrue(s["acceptance"]["power_halt_performed"])
        self.assertTrue(s["acceptance_pass"])

    def test_acceptance_not_yet_without_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            write_logs(d, [COMPLETE_LOG])
            rows = [parse_cycle_log(p) for p in sorted(Path(d).glob("rc_cycle_*.log"))]
        s = summarize(rows)
        self.assertFalse(s["acceptance"]["incomplete_cycle_logged"])
        self.assertFalse(s["acceptance_pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
