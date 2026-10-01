#!/usr/bin/env python3
# filename: test_bm_bench_conductor.py
# description: Sprint26 S5 — bench conductor: id range, row matching, summary, one full cycle against fakes (heal first, trg acked, row claimed, completion tracked); console accounting and the --indoor-flush rule against recorded Spotter console lines.
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
    """Every published command is answered at once (the unit's "OK id=<id> ...", the
    Spotter's "Sync request sent successfully"). `snap` is what snapshot() reports."""

    def __init__(self):
        self.published = []
        self.snap = {"queue_full": 0, "sync_attempt": 0, "sync_requested": 0, "mono": 0.0,
                     "pct": None, "pct_age_s": None, "ms_age_s": None}
        self.pcts = []

    def publish(self, line):
        self.published.append(line)

    def seen_since(self, _mono, needle):
        if needle == C.FLUSH_ACK:
            return C.FLUSH_LINE in self.published
        cid = needle.split("=")[1].strip()
        return any(f'"id":{cid},' in p for p in self.published)

    def snapshot(self):
        return dict(self.snap)

    def pcts_since(self, _mono):
        return list(self.pcts)


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

    def test_no_heal_triggers_only(self):
        # S6b §9.14 H2-H4: another heal sender is live (Sofar lane / backend auto-send);
        # the conductor must never publish an rsd.
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            backend = FakeBackend(clock)
            backend.heal_candidates = [{"media_id": 7, "media_key": "0dxaaa", "ranges": "1-2"}]
            console = FakeConsole()
            cond = C.Conductor(tmp, backend, {"SPOT-X": console},
                               {"SPOT-X": ("BMCAM_003", "bmcam003")}, hours=1,
                               min_interval_s=3600, drain_s=0, clock=clock, sleep=clock.sleep,
                               heal=False)
            cond.cycle("SPOT-X")
            self.assertEqual([p for p in console.published if '"c":"rsd"' in p], [])
            self.assertTrue(any('"c":"trg"' in p for p in console.published))
            self.assertFalse(any("heal" in path for _m, path in backend.calls))

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


RUNS = os.path.join(REPO_ROOT, "runs")
WAKE1 = os.path.join(RUNS, "s5_console_20260928", "pulled", "console_wake1_1640.txt")
LADDER4 = os.path.join(RUNS, "s5_console_20260928", "pulled", "console_bmcam004_ladder.txt")
S2_CMDS = os.path.join(RUNS, "s2_bench_20260926", "console_commands.log")


def recorded(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return [line.rstrip("\n") for line in fh]


class ConsoleLines(unittest.TestCase):
    """Real SPOT-33507C / SPOT-31593C console lines (S5 bench, 2026-09-28)."""

    def test_classify_recorded_lines(self):
        k = C.classify_console_line
        self.assertEqual(k("2026-09-28T16:42:00Z 2026-09-28T16:42:01.835Z [MS] [DEBUG] "
                           "Notecard is 6.000000 pct full."), ("pct", 6.0))
        self.assertEqual(k("2026-09-28T16:42:03Z 2026-09-28T16:42:04.652Z [MS] [ERROR] "
                           "Queue MS_Q_CELLULAR_ONLY is full."), ("queue_full",))
        # a leading "." (the monitor's partial-line marker) does not matter
        self.assertEqual(k("2026-09-28T16:42:03Z .2026-09-28T16:42:04.785Z [MS] [DEBUG] "
                           "Attempting to Sync."), ("sync_attempt",))
        # the two companion lines of one rejection are not counted again
        self.assertIsNone(k("2026-09-28T16:42:03Z 2026-09-28T16:42:04.652Z [MS] [INFO] "
                            "Error adding message to queue."))
        self.assertIsNone(k("[BM_TX] [ERROR] Unable to submit message to cell-only queue"))
        self.assertIsNone(k("2026-09-28T16:42:03Z bm pub bmcam/cmd {} 1 1"))

    def test_the_monitor_answer_to_note_sync_is_recognised(self):
        lines = [x for x in recorded(S2_CMDS) if C.classify_console_line(x) == ("sync_requested",)]
        self.assertEqual(len(lines), 2)            # SPOT-33507C + SPOT-31593C, 2026-09-26 00:35

    def test_replaying_a_wake_counts_like_grep(self):
        # RESULTS F1: 90 queue-full rejections in bmcam003's 16:40 production wake,
        # two Spotter syncs (16:42:04 at 6 %, 16:43:14 at 4 %)
        con = C.Console("/nonexistent", "SPOT-33507C")
        for i, line in enumerate(recorded(WAKE1)):
            con.account(float(i), line)
        snap = con.snapshot()
        self.assertEqual((snap["queue_full"], snap["sync_attempt"]), (90, 2))
        self.assertEqual(con.pcts_since(0.0)[0], 2.0)
        self.assertLess(max(con.pcts_since(0.0)), 10.0)   # it rejected at a near-empty Notecard

    def test_bmcam004_ladder_sync_at_20_pct_then_drained(self):
        con = C.Console("/nonexistent", "SPOT-31593C")
        for i, line in enumerate(recorded(LADDER4)):
            con.account(float(i), line)
        pcts = con.pcts_since(0.0)
        self.assertEqual(pcts[0], 20.0)            # 18:55, before its own sync at 18:56:02
        self.assertEqual(min(pcts), 3.0)           # drained, then a burst adds ~11 %
        self.assertEqual(con.snapshot()["sync_attempt"], 1)


class FlushRule(unittest.TestCase):
    ARGS = dict(pct_threshold=10.0, after_s=480.0, lead_s=300.0)

    def decide(self, **kw):
        base = dict(since_trigger_s=600.0, to_next_trigger_s=1200.0, pct=14.0,
                    queue_full_cycle=0, ms_age_s=300.0, already=False)
        base.update(kw)
        return C.flush_decision(**base, **self.ARGS)

    def test_backed_up_notecard_between_bursts_flushes(self):
        self.assertEqual(self.decide(), (True, "pct"))
        self.assertEqual(self.decide(pct=4.0, queue_full_cycle=12), (True, "queue_full"))
        self.assertEqual(self.decide(ms_age_s=None), (True, "pct"))   # no MS line seen yet

    def test_never_mid_burst_or_near_the_next_trigger(self):
        self.assertEqual(self.decide(since_trigger_s=200.0), (False, "burst_window"))
        self.assertEqual(self.decide(to_next_trigger_s=120.0), (False, "too_close_to_next_trigger"))
        self.assertEqual(self.decide(ms_age_s=5.0), (False, "console_busy"))
        self.assertEqual(self.decide(already=True), (False, "already_flushed_this_cycle"))

    def test_not_needed_when_low_and_clean(self):
        self.assertEqual(self.decide(pct=4.0), (False, "not_needed"))
        self.assertEqual(self.decide(pct=None), (False, "not_needed"))


class IndoorFlushCycle(unittest.TestCase):
    def make(self, tmp, clock, flush):
        backend = FakeBackend(clock)
        console = FakeConsole()
        cond = C.Conductor(tmp, backend, {"SPOT-X": console}, {"SPOT-X": ("BMCAM_003", "bmcam003")},
                           hours=1, min_interval_s=1800, drain_s=0, flush=flush,
                           clock=clock, sleep=clock.sleep)
        return cond, backend, console

    def test_default_off_never_sends_note_sync(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            cond, _backend, console = self.make(tmp, clock, None)
            console.snap.update(pct=30.0, ms_age_s=999.0, queue_full=0)
            cond.cycle("SPOT-X")
            self.assertNotIn(C.FLUSH_LINE, console.published)
            rec = cond.ledger["BMCAM_003"][0]
            self.assertNotIn("flush", rec)
            self.assertIn("console", rec)          # accounting is always on

    def test_one_flush_per_cycle_in_the_idle_window_and_its_effect_logged(self):
        clock = FakeClock()
        flush = {"pct": 10.0, "after_s": 480.0, "lead_s": 300.0}
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            cond, _backend, console = self.make(tmp, clock, flush)
            console.snap.update(pct=14.0, ms_age_s=999.0)
            console.pcts = [3.0, 14.0]
            cond.cycle("SPOT-X")
            self.assertEqual(console.published.count(C.FLUSH_LINE), 1)
            rec = cond.ledger["BMCAM_003"][0]
            f = rec["flush"]
            self.assertTrue(f["requested"])
            self.assertEqual((f["reason"], f["pct_before"]), ("pct", 14.0))
            self.assertGreaterEqual(f["after_trigger_s"], 480.0)
            self.assertGreaterEqual(f["to_next_trigger_s"], 300.0)
            # the next trigger records what happened between the flush and it
            console.pcts = [3.0]
            cond.cycle("SPOT-X")
            nxt = cond.ledger["BMCAM_003"][1]
            self.assertEqual(nxt["flushed_before"]["pct_first_after"], 3.0)
            s = C.summarize(cond.ledger)["BMCAM_003"]
            self.assertEqual(s["flushes"], 2)
            self.assertEqual(s["after_flush"]["cycles"], 1)
            with open(os.path.join(tmp, "events.jsonl")) as fh:
                text = fh.read()
            for kind in ('"flush"', '"flush_effect"', '"console_cycle"'):
                self.assertIn(kind, text)

    def test_no_flush_while_the_console_is_busy(self):
        clock = FakeClock()
        flush = {"pct": 10.0, "after_s": 480.0, "lead_s": 300.0}
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            cond, _backend, console = self.make(tmp, clock, flush)
            console.snap.update(pct=40.0, ms_age_s=1.0)      # MS lines still arriving
            cond.cycle("SPOT-X")
            self.assertNotIn(C.FLUSH_LINE, console.published)

    def test_an_unanswered_note_sync_is_logged_not_retried(self):
        clock = FakeClock()
        flush = {"pct": 10.0, "after_s": 480.0, "lead_s": 300.0}
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"), \
                mock.patch.object(C, "FLUSH_ACK_WAIT_S", 0.05):
            cond, _backend, console = self.make(tmp, clock, flush)
            console.snap.update(pct=14.0, ms_age_s=999.0)
            real = console.seen_since
            console.seen_since = lambda m, n: False if n == C.FLUSH_ACK else real(m, n)
            cond.cycle("SPOT-X")
            self.assertEqual(console.published.count(C.FLUSH_LINE), 1)
            self.assertFalse(cond.ledger["BMCAM_003"][0]["flush"]["requested"])


if __name__ == "__main__":
    unittest.main()
