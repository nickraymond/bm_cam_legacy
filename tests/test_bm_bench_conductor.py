#!/usr/bin/env python3
# filename: test_bm_bench_conductor.py
# description: Sprint26 S5 — bench conductor: id range, row matching, summary, one full cycle against fakes (heal first, trg acked, row claimed, completion tracked).
"""
Run (repo root):
  python3 -m unittest tests.test_bm_bench_conductor -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import bm_bench_conductor as C  # noqa: E402

T0 = C.parse_utc("2026-09-28T20:33:20Z")


def row(mid, captured, recv, exp):
    return {"media_id": mid, "captured_at_utc": captured, "received_chunks": recv,
            "expected_chunks": exp, "is_complete": recv == exp}


class Pure(unittest.TestCase):
    def test_conductor_id_is_in_the_conductor_range(self):
        cid = C.conductor_id(T0)
        self.assertTrue(2_000_000_000 <= cid < 2**32)
        self.assertEqual(C.conductor_id(T0 + 1), cid + 1)
        self.assertLess(C.conductor_id(T0 + 20 * 365 * 86400), 2**32)

    def test_match_row_takes_the_earliest_unclaimed_capture_after_the_trigger(self):
        rows = [row(1, "2026-09-28T20:00:00+00:00", 5, 5),      # before the trigger
                row(3, "2026-09-28T20:40:00+00:00", 5, 5),
                row(2, "2026-09-28T20:34:00+00:00", 5, 5)]
        self.assertEqual(C.match_row(rows, T0, set())["media_id"], 2)
        self.assertEqual(C.match_row(rows, T0, {2})["media_id"], 3)
        self.assertIsNone(C.match_row(rows, T0, {2, 3}))
        # capture 30 s before our trigger (Spotter clock) still matches (slack 60 s)
        self.assertEqual(C.match_row([row(4, "2026-09-28T20:32:50Z", 1, 1)], T0, set())["media_id"], 4)

    def test_summarize(self):
        ledger = {"BMCAM_003": [
            {"trigger_id": 1, "acked": True, "media_id": 10, "row_s": 900.0,
             "complete_utc": "x", "complete_s": 900.0},
            {"trigger_id": 2, "acked": True, "media_id": 11, "row_s": 1200.0,
             "complete_utc": "x", "complete_s": 4000.0, "healed_by": [100100]},
            {"trigger_id": 3, "acked": True, "media_id": 12, "row_s": 800.0},     # partial
            {"trigger_id": 4, "acked": False}]}                                    # no row
        s = C.summarize(ledger)["BMCAM_003"]
        self.assertEqual((s["triggers"], s["acked"], s["rows"], s["no_row"]), (4, 3, 3, 1))
        self.assertEqual((s["complete"], s["complete_first_try"], s["healed"]), (2, 1, 1))
        self.assertEqual((s["lost"], s["lost_trigger_ids"]), (2, [3, 4]))
        self.assertEqual(s["trigger_to_row_s_p50"], 900.0)


class FakeClock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeConsole:
    """Every published command is answered at once (the unit's "OK id=<id> ...")."""

    def __init__(self):
        self.published = []

    def publish(self, line):
        self.published.append(line)

    def seen_since(self, _mono, needle):
        cid = needle.split("=")[1].strip()
        return any(f'"id":{cid},' in p for p in self.published)


class FakeBackend:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.heal_candidates = []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if "heal-candidates" in path:
            return 200, {"candidates": self.heal_candidates}
        if path.endswith("/heal-commands"):
            return 200, {"command_id": 100200, "heals": [["0dxaaa", "1-2"]], "chunks": 2,
                         "media_ids": [7], "console_line":
                         'bm pub bmcam/cmd {"id":100200,"c":"rsd","h":[["0dxaaa","1-2"]]} 1 1'}
        if "/missing" in path:
            return 409, {"detail": {"reason": "complete"}}
        if "/media?" in path:
            age = self.clock() - T0
            if age < 1200:                  # Sofar exposure lag: nothing yet
                return 200, []
            recv = 180 if age < 2400 else 185
            return 200, [row(55, "2026-09-28T20:34:10+00:00", recv, 185)]
        return 404, {}


class Cycle(unittest.TestCase):
    def make(self, tmp, clock):
        backend = FakeBackend(clock)
        console = FakeConsole()
        cond = C.Conductor(tmp, backend, {"SPOT-X": console}, {"SPOT-X": ("BMCAM_003", "bmcam003")},
                           hours=1, min_interval_s=3600, drain_s=0, clock=clock, sleep=clock.sleep)
        return cond, backend, console

    def test_one_cycle_heals_first_then_triggers_and_tracks_the_clip(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            cond, backend, console = self.make(tmp, clock)
            backend.heal_candidates = [{"media_id": 7, "media_key": "0dxaaa", "ranges": "1-2"}]
            cond.cycle("SPOT-X")
            self.assertIn('"c":"rsd"', console.published[0])        # the heal rides this action
            self.assertIn('"c":"trg","v":2', console.published[1])
            rec = cond.ledger["BMCAM_003"][0]
            self.assertTrue(rec["acked"])
            self.assertEqual(rec["media_id"], 55)
            self.assertGreaterEqual(rec["row_s"], 1200)
            self.assertIsNotNone(rec.get("complete_utc"))          # completed during min-interval
            self.assertTrue(os.path.exists(os.path.join(tmp, "state.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "events.jsonl")))
            s = C.summarize(cond.ledger)["BMCAM_003"]
            self.assertEqual((s["lost"], s["complete"]), (0, 1))

    def test_every_cycle_publishes_a_fresh_heal_while_a_gap_stays_open(self):
        # S5 F10 (24 h run 2026-09-28/29): one command held for up to 3 cycles healed
        # ~1 clip per 90 min on bmcam003. Now: status of the last one, then a new one.
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            cond, backend, console = self.make(tmp, clock)
            backend.heal_candidates = [{"media_id": 7, "media_key": "0dxaaa", "ranges": "1-2"}]
            ids = iter([100300, 100301])
            real = backend.call

            def call(method, path, body=None):
                if path.endswith("/heal-commands"):
                    cid = next(ids)
                    return 200, {"command_id": cid, "heals": [["0dxaaa", "1-2"]], "chunks": 2,
                                 "media_ids": [7], "console_line":
                                 f'bm pub bmcam/cmd {{"id":{cid},"c":"rsd","h":[["0dxaaa","1-2"]]}} 1 1'}
                if "/missing" in path:
                    return 200, {"ranges": "1-2"}                 # still missing, not arriving
                return real(method, path, body)
            backend.call = call
            cond.heal_step("SPOT-X")
            cond.heal_step("SPOT-X")
            rsd = [p for p in console.published if '"c":"rsd"' in p]
            self.assertEqual(len(rsd), 2)
            self.assertIn('"id":100300', rsd[0])
            self.assertIn('"id":100301', rsd[1])
            with open(os.path.join(tmp, "events.jsonl")) as fh:
                self.assertIn('"heal_status"', fh.read())

    def test_no_heal_command_when_nothing_is_healable(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            cond, backend, console = self.make(tmp, clock)
            cond.heal_step("SPOT-X")
            self.assertEqual(console.published, [])
            self.assertIsNone(cond.heals["BMCAM_003"])

    def test_no_row_within_the_wait_is_lost(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            cond, backend, _console = self.make(tmp, clock)
            backend.call = lambda m, p, b=None: (200, [] if "/media?" in p else {"candidates": []})
            cond.cycle("SPOT-X")
            rec = cond.ledger["BMCAM_003"][0]
            self.assertIsNone(rec.get("media_id"))
            self.assertEqual(C.summarize(cond.ledger)["BMCAM_003"]["lost"], 1)

    def test_an_unanswered_trigger_is_retried_with_the_same_id(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"), \
                mock.patch.object(C, "ACK_WAIT_S", 0.05):
            cond, _backend, console = self.make(tmp, clock)
            console.seen_since = lambda *_: False
            self.assertFalse(cond.send("SPOT-X", 2_000_630_000, '{"id":2000630000,"c":"trg","v":2}',
                                       "trg"))
            self.assertEqual(len(console.published), C.PUBLISH_TRIES)
            self.assertEqual(len(set(console.published)), 1)


if __name__ == "__main__":
    unittest.main()
