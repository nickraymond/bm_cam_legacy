#!/usr/bin/env python3
# filename: test_bm_video_soak_report.py
# description: Sprint22 — parsers behind the per-cycle soak table.
"""
Sprint22 — tools/bm_video_soak_report.py parsers, fed the EXACT line formats
the runtime and the Spotter console produced on 2026-09-21.

Run (repo root):
  python3 -m unittest tests.test_bm_video_soak_report -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import bm_video_soak_report as rep  # noqa: E402

CAMERA_LOG = """[RC-CRON] start_utc=2026-09-21T07:43:20+00:00
[RC-CRON] uptime_s=41.87
[VTX] cycle start: budget=480s pacing=1.0s/msg transmit=True
[VTX] message budget: cap=126 affordable_now=409 (after reserving 32) -> 126 chunk msgs (36288 B)
[VTX] encode try 1: 55.7 kbps -> 28069 B = 77% of 126 msgs
[VTX] payload: 35123 B = 122 msgs (96.8% of budget) 3 pass-2 tries, prescale 5.5s encode 6.2s
[PHASE] phase=237.9s burst=138s lane=220s -> wait_next_lane wait=122.1s (start@+60s end@+198s)
[VTX] transmit done: sent=122/122 complete=True keyframe_repeat=14/14 uart=138.2s file=2026-09-21T07-43-35Z_video_5s.h264
[VTX] cycle end: stage=done elapsed=291.9s of 480s; halt=halt_initiated
"""

CONSOLE = """2026-09-21T07:30:30Z 2026-09-21T07:30:30.621Z [MS] [INFO] Added message(id: 379 len: 359) to queue MS_Q_CELLULAR_ONLY: (2)!
2026-09-21T07:30:31Z 2026-09-21T07:30:31.640Z [MS] [ERROR] Queue MS_Q_CELLULAR_ONLY is full.
2026-09-21T07:30:32Z .2026-09-21T07:30:32.652Z [MS] [ERROR] Queue MS_Q_CELLULAR_ONLY is full.
2026-09-21T07:30:40Z .2026-09-21T07:30:40.695Z [ORC] [INFO] Message 7 transmitted successfully!
2026-09-21T07:30:48Z 2026-09-21T07:30:48.010Z [MS] [INFO] Added message(id: 380 len: 518) to queue MS_Q_CELLULAR_ONLY: (1)!
2026-09-21T09:00:00Z 2026-09-21T09:00:00.000Z [MS] [ERROR] Queue MS_Q_CELLULAR_ONLY is full.
"""


class TestParsers(unittest.TestCase):
    def test_camera_log(self):
        row = rep.parse_camera_log(CAMERA_LOG)
        self.assertEqual((row["boot_to_runtime_s"], row["budget_msgs"], row["chunk_msgs"]), (41.87, 126, 122))
        self.assertEqual((row["budget_used_pct"], row["pass2_tries"], row["frames_trimmed"]), (96.8, 3, 0))
        self.assertEqual((row["phase_s"], row["lane_plan"], row["lane_wait_s"]), (237.9, "wait_next_lane", 122.1))
        self.assertEqual((row["sent"], row["planned"], row["keyframe_repeated"], row["keyframe_chunks"]),
                         (122, 122, 14, 14))
        self.assertEqual((row["file"], row["halt"], row["stage"]),
                         ("2026-09-21T07-43-35Z_video_5s.h264", "halt_initiated", "done"))
        self.assertFalse(row["lane_wait_skipped"])
        self.assertEqual(rep.parse_camera_log("[RC-CRON] nothing video here\n").get("stage"), None)

    def test_skipped_lane_wait_and_errors_are_flagged(self):
        row = rep.parse_camera_log(CAMERA_LOG + "[VTX][WARN] skipping the 250s lane wait: only 90s of budget left\n"
                                   "[VTX][ERROR] cycle failed at stage 'fit': ClipError: prescale failed\n")
        self.assertTrue(row["lane_wait_skipped"])
        self.assertIn("prescale failed", row["camera_errors"])

    def test_console_window_counts(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(CONSOLE)
        events = rep.console_events([fh.name])
        t0 = datetime(2026, 9, 21, 7, 30, 30, tzinfo=timezone.utc)
        cols = rep.console_columns(events, t0, t0 + timedelta(seconds=30))
        self.assertEqual((cols["spotter_queued"], cols["spotter_queue_full"], cols["spotter_own_tx_in_burst"]),
                         (2, 2, 1))                       # the 09:00 queue-full is outside the burst
        self.assertEqual(cols["queue_full_window"], "07:30:31-07:30:32Z")

    def test_sofar_groups_attribute_chunks_to_the_open_start(self):
        def row(ts, text):
            return {"bristlemouth_node_id": "n", "timestamp": ts, "value": text.encode().hex()}
        entries = [
            row("2026-09-21T07:30:30.000Z", "<START IMG> filename: 2026-09-21T07-28-34Z_video_5s.h264, timestamp: x, length: 4, fmt=h264, fps=10"),
            row("2026-09-21T07:30:31.000Z", "<I0>AAAA"), row("2026-09-21T07:30:34.000Z", "<I3>AAAA"),
            row("2026-09-21T07:30:35.000Z", "<I0>AAAA"),                       # keyframe repeat: same index
            row("2026-09-21T07:30:36.000Z", "<END IMG> filename: 2026-09-21T07-28-34Z_video_5s.h264, fmt: h264"),
            row("2026-09-21T07:31:00.000Z", "<START IMG> filename: 2026-09-21T07-30-00Z_image_q35.jpg, length: 9, fmt=pjpg"),
            row("2026-09-21T07:31:01.000Z", "<I1>AAAA"),                       # a JPEG's chunk: not ours
            {"bristlemouth_node_id": "other", "timestamp": "2026-09-21T07:30:32.000Z", "value": "<I1>AAAA".encode().hex()},
        ]
        g = rep.sofar_groups(entries, "n")["2026-09-21T07-28-34Z_video_5s.h264"]
        self.assertEqual((sorted(g["got"]), g["length"], g["end"]), ([0, 3], 4, True))
        # END lost: the next START (a JPEG) must still close the video group.
        no_end = [e for e in entries if "<END IMG>" not in bytes.fromhex(e["value"]).decode()]
        g = rep.sofar_groups(no_end, "n")["2026-09-21T07-28-34Z_video_5s.h264"]
        self.assertEqual((sorted(g["got"]), g["end"]), ([0, 3], False))


if __name__ == "__main__":
    unittest.main()
