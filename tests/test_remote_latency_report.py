#!/usr/bin/env python3
# filename: test_remote_latency_report.py
# description: Sprint26 S6b — remote_latency_report: console parsing on recorded Spotter lines (W9 /M heal chunks from hex dumps), the per-command join, hops, and an offline end-to-end run.
"""
Run (repo root):
  python3 -m unittest tests.test_remote_latency_report -v
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import remote_latency_report as L  # noqa: E402

FIX = os.path.join(REPO_ROOT, "tests", "fixtures", "remote_latency", "console_rsd_heal_w9.log")
RSD = 'bm pub bmcam/cmd {"id":100077,"c":"rsd","h":[["0dz8um","5,17,83-85"]]} 1 1'
T_POST = L.parse_utc("2026-09-29T18:30:00Z")


def fixture_lines():
    with open(FIX, "r", encoding="utf-8") as fh:
        return fh.read().split("\n")


class Pure(unittest.TestCase):
    def test_chunk_regex_accepts_the_w9_total_and_the_old_prefix(self):
        m = L.RE_CHUNK.match("<I0dz8um.84/185>5WIH1vbM")
        self.assertEqual(m.groups(), ("0dz8um", "84", "185"))
        m = L.RE_CHUNK.match("<I0dpr00.41>UUV7j6zh")
        self.assertEqual(m.groups(), ("0dpr00", "41", None))

    def test_line_time_prefers_the_spotter_stamp(self):
        t, _ = L.line_time("2026-09-29T18:43:41Z 2026-09-29T18:43:44.011Z [BM_TX] [INFO] x")
        self.assertEqual(L.iso(t), "2026-09-29T18:43:44.011Z")
        t, _ = L.line_time("2026-09-29T18:43:41Z .2026-09-29T18:43:44.500Z [MS] x")
        self.assertEqual(L.iso(t), "2026-09-29T18:43:44.500Z")
        t, _ = L.line_time("2026-09-28T18:55:57Z 1790621758.464 e6fe83ea6b4a2b7f, "
                           "[bmcam004] OK id=61090 hold awake 120 min cfg=55e424b4")
        self.assertAlmostEqual(t, 1790621758.464, places=3)
        t, _ = L.line_time("2026-09-29T18:43:16Z  3c 53 54 41")          # hex: monitor time
        self.assertEqual(L.iso(t), "2026-09-29T18:43:16.000Z")

    def test_expand_ranges(self):
        self.assertEqual(L.expand_ranges("5,17,83-85"), [5, 17, 83, 84, 85])


class Console(unittest.TestCase):
    def test_recorded_heal_chunks_decode_from_the_hex_dumps(self):
        con = L.parse_console(fixture_lines())
        payloads = [p[:18] for _t, p in con["cells"]]
        self.assertEqual(payloads[:2], ["<I0dz8um.84/185>5W", "<I0dz8um.85/185>LP"])
        self.assertTrue(payloads[2].startswith("<START IMG>"))
        self.assertEqual(L.iso(con["cells"][0][0]), "2026-09-29T18:43:44.011Z")
        self.assertEqual([(i, ok) for _t, _h, ok, i, _r in con["answers"]], [(100077, True)])
        self.assertEqual(len(con["remote_rx"]), 1)

    def test_join_one_rsd(self):
        con = L.parse_console(fixture_lines())
        send = {"spotter_id": "SPOT-33507C", "t_post": T_POST, "message": RSD,
                "http_status": 202, "id": 100077, "verb": "rsd"}
        row = L.join_console(send, con, 90 * 60)
        self.assertEqual(L.iso(row["t_console"]), "2026-09-29T18:43:12.500Z")
        self.assertEqual(L.iso(row["t_answer"]), "2026-09-29T18:43:22.250Z")
        self.assertEqual(row["answer"], "OK")
        self.assertEqual((row["chunks_asked"], row["chunks_on_console"]), (5, 2))   # 84, 85 here
        self.assertEqual(L.iso(row["t_chunks_last"]), "2026-09-29T18:43:45.332Z")

    def test_a_send_after_the_receive_is_not_matched_to_it(self):
        con = L.parse_console(fixture_lines())
        send = {"spotter_id": "S", "t_post": T_POST + 3600, "message": RSD, "http_status": 202,
                "id": 100077, "verb": "rsd"}
        self.assertIsNone(L.join_console(send, con, 60).get("t_console"))

    def test_rejected_answer(self):
        con = L.parse_console([
            "2026-09-29T19:00:05Z 2026-09-29T19:00:05.100Z [SYS] [INFO] Remote message "
            'received(53)! "bm pub bmcam/cmd {"id":1000900,"c":"set","kv":{"q":7}} 1 1',
            "2026-09-29T19:00:06Z 1790708406.500 53171fa3d81a8e6f, [bmcam003] REJECTED "
            "id=1000900 e=val k=still.save.quality cfg=580ce986"])
        send = {"spotter_id": "S", "t_post": L.parse_utc("2026-09-29T18:50:00Z"),
                "message": 'bm pub bmcam/cmd {"id":1000900,"c":"set","kv":{"q":7}} 1 1',
                "http_status": 202, "id": 1000900, "verb": "set"}
        row = L.finish_row({**send, **L.join_console(send, con, 60), "t_post": send["t_post"]}, {})
        self.assertEqual(row["answer"], "REJECTED e=val")
        self.assertEqual(row["post_to_console_s"], 605.1)


class EndToEnd(unittest.TestCase):
    def test_offline_run_writes_csv_summary_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "logs", "SPOT-33507C")
            os.makedirs(root)
            with open(os.path.join(root, "console_20260929.log"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(fixture_lines()) + "\n")
            send_log = os.path.join(tmp, "sends.jsonl")
            with open(send_log, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": T_POST, "spotter_id": "SPOT-33507C", "message": RSD,
                                     "http_status": 202}) + "\n")
                fh.write(json.dumps({"ts": T_POST + 30, "spotter_id": "SPOT-31593C",
                                     "message": RSD, "http_status": 202}) + "\n")   # other rig
            events = os.path.join(tmp, "events.jsonl")
            with open(events, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"utc": "2026-09-29T20:15:42Z", "event": "complete",
                                     "media_id": 55503}) + "\n")
            out = os.path.join(tmp, "out")
            acks = {100077: (L.parse_utc("2026-09-29T18:44:30Z"), "HL a=sent r=ok n=5", [55503])}
            with mock.patch("builtins.print"), \
                    mock.patch.object(L, "backend_acks", lambda *a, **k: acks), \
                    mock.patch.object(L, "read_token", lambda _p: "x"):
                code = L.main(["--send-log", send_log, "--log-root", os.path.join(tmp, "logs"),
                               "--rig", "SPOT-33507C=BMCAM_003", "--since", "2026-09-29T00:00:00Z",
                               "--until", "2026-09-30T00:00:00Z", "--conductor-events", events,
                               "--api", "http://fake", "--out", out])
            self.assertEqual(code, 0)
            with open(os.path.join(out, "latency.csv"), newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)                       # only the --rig Spotter
            r = rows[0]
            self.assertEqual((r["command_id"], r["verb"], r["answer"]), ("100077", "rsd", "OK"))
            self.assertEqual(r["t_ack"], "2026-09-29T18:44:30.000Z")
            self.assertEqual((r["media_ids"], r["media_complete"]), ("55503", "True"))
            self.assertEqual(r["post_to_media_complete_s"], "6342.0")     # 18:30:00 -> 20:15:42
            with open(os.path.join(out, "summary.json")) as fh:
                s = json.load(fh)
            self.assertEqual((s["accepted_202"], s["reached_console"], s["answered"],
                              s["backend_ack"], s["heal_media_complete"]), (1, 1, 1, 1, 1))
            self.assertEqual(s["post_to_console_s"]["p50"], 792.5)
            with open(os.path.join(out, "run_manifest.json")) as fh:
                self.assertNotIn("env_file", json.load(fh)["args"])


if __name__ == "__main__":
    unittest.main()
