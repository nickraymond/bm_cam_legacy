#!/usr/bin/env python3
# filename: test_s5_heal.py
# description: Sprint25 S5 — rsd parse, pending heals, daemon dispatch, heal slot, <HL> (ladder 1).
"""
Sprint25 S5 (SPEC_resend_heal.md §4-5, §7 ladder 1; RESEND_DEVICE.md §4-5). Pins:

  parse_rsd         h list / x cancel; key + range grammar; reversed / duplicate /
                    > 40 chunks / > 8 heals / duplicate key -> `val`; a hostile
                    "0-99999999" is refused without expanding it
  pending_heals     newest first, dedupe by key (newest id wins), <= 8, persisted,
                    tolerant load, cancel, the id is ALWAYS recorded
  daemon            heal_validate_fn per heal: >= 1 accepted -> ok=1; none -> ok=0
                    e=rsd, id recorded (re-send = duplicate, no new refusal)
  heal wire         byte-identical to the chunk the original keyed send put on the wire
                    (the rev 5 golden vector); sha mismatch -> refused payload_changed
  wake rules        <= 40 chunks/wake; the capture's room is reserved; wakes_left counts
                    only heals present at wake start; 0 -> dropped
  video cycle       heal chunks BEFORE START; <HL a=sent ... w=<wake key>> AFTER END;
                    ack -> close -> halt ordering kept; an rsd arriving mid-burst ->
                    ack after END + <HL a=requested>; nothing pending -> no extra lines
  stills cycle      same slot before START, <HL> after END
  bench drop        --bench-drop-chunks skips the wire, keeps the pacing, counts as sent
  <HL>              parses with the backend's regex (nvd heal_status_ingest)

Run (repo root):  python3 -m pytest tests/test_s5_heal.py -q
"""

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")
    _stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = _stub

import rc_heal  # noqa: E402
import rc_media_key as mk  # noqa: E402

from command_messages import expand_ranges, parse_command  # noqa: E402
from command_state import CommandState  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from rc_transmit import transmit_video_clip  # noqa: E402
from tests.test_command_integration import IntegrationHarness, make_cmd_frame  # noqa: E402
from tests.test_s3_video_tx_daemon import VideoTxHarness, settings as vtx_settings  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")
REV5 = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264_rev5")
UTC = timezone.utc
OLD_KEY = "0dhnso"                                   # 2026-09-20T06:10:00Z, the golden clip
T_OLD = datetime(2026, 9, 20, 6, 10, 0, tzinfo=UTC)
T_WAKE = datetime(2026, 9, 24, 3, 0, 0, tzinfo=UTC)
WAKE_KEY = mk.key_for_utc(T_WAKE)

with open(os.path.join(VEC, "payload.h264"), "rb") as _fh:
    PAYLOAD = _fh.read()

# Backend parser (nereus-vision-dev backend/app/services/heal_status_ingest.py, nvd #56).
RE_HL_KV = re.compile(r"([a-z]+)=([^\s>]+)")


def rsd(**body):
    return json.dumps(dict({"id": 100001, "c": "rsd"}, **body))


def golden_chunk_line(n):
    """Chunk n exactly as the S4 keyed send put it on the wire (rev 5 golden vector)."""
    with open(os.path.join(REV5, "wire_keyed_complete.txt"), "rb") as fh:
        for line in fh.read().splitlines(keepends=True):
            if line.startswith(f"<I{OLD_KEY}.{n}>".encode()):
                return line
    raise AssertionError(f"chunk {n} not in the golden wire")


def write_old_record(sent_dir):
    """The sent record the golden clip's wake left behind (S4 prepare_keyed_send)."""
    return mk.write_sent_record(
        sent_dir, "2026-09-20T06-10-00Z_video_5s", key=OLD_KEY, fmt="h264",
        filename="2026-09-20T06-10-00Z_video_5s.h264", chunk_b64_chars=384, msgs=126,
        sha256=hashlib.sha256(PAYLOAD).hexdigest(), payload_bytes=PAYLOAD)


class TestParseRsd(unittest.TestCase):
    def ok(self, payload):
        r = parse_command(payload)
        self.assertTrue(r["ok"], r)
        return r["value"]

    def bad(self, payload):
        r = parse_command(payload)
        self.assertFalse(r["ok"], r)
        self.assertEqual((r["id"], r["error"]), (100001, "val"))

    def test_heal_list(self):
        v = self.ok(rsd(h=[["0dhnso", "17,40-42"], ["0dhzzz", "0"]]))
        self.assertEqual(v, {"h": [["0dhnso", [17, 40, 41, 42]], ["0dhzzz", [0]]]})

    def test_cancel(self):
        self.assertEqual(self.ok(rsd(x=1)), {"x": 1})

    def test_rejects(self):
        for body in (
            {},                                              # neither h nor x
            {"x": 1, "h": [["0dhnso", "1"]]},                # both
            {"x": 0}, {"x": True}, {"x": "1"},
            {"h": []}, {"h": "0dhnso:1"},
            {"h": [["0DHNSO", "1"]]}, {"h": [["0dhns", "1"]]}, {"h": [["0dhnso", 1]]},
            {"h": [["0dhnso", "4-2"]]},                      # reversed
            {"h": [["0dhnso", "3,1-4"]]},                    # duplicate index
            {"h": [["0dhnso", " 1"]]}, {"h": [["0dhnso", "1,"]]}, {"h": [["0dhnso", "-1"]]},
            {"h": [["0dhnso", "1"], ["0dhnso", "2"]]},       # duplicate key
            {"h": [["0dhnso", "0-40"]]},                     # 41 chunks
            {"h": [["0dhnso", "0-20"], ["0dhzzz", "0-19"]]}, # 41 across heals
            {"h": [[f"0dh{i:03d}", "1"] for i in range(9)]}, # 9 heals
            {"h": [["0dhnso", "1", "extra"]]},
        ):
            with self.subTest(body=body):
                self.bad(rsd(**body))

    def test_limits_exact(self):
        self.assertEqual(len(self.ok(rsd(h=[["0dhnso", "0-39"]]))["h"][0][1]), 40)
        self.assertEqual(len(self.ok(rsd(h=[[f"0dh{i:03d}", "1"] for i in range(8)]))["h"]), 8)

    def test_hostile_range_not_expanded(self):
        t0 = time.monotonic()
        self.assertIsNone(expand_ranges("0-999999999999"))
        self.assertLess(time.monotonic() - t0, 0.05)


class TestPendingHeals(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.path = os.path.join(self.d.name, "state.json")

    def test_dedupe_newest_first_cap_and_persist(self):
        st = CommandState(path=self.path)
        st.record(1, "rsd", {"h": [["aaaaaa", [1]], ["bbbbbb", [2]]]})
        st.record(2, "rsd", {"h": [["cccccc", [3]], ["aaaaaa", [4, 5]]]})
        self.assertEqual([(h["key"], h["id"]) for h in st.pending_heals],
                         [("cccccc", 2), ("aaaaaa", 2), ("bbbbbb", 1)])
        self.assertEqual(st.pending_heals[1]["n"], [4, 5], "newest id wins for a key")
        with contextlib.redirect_stdout(io.StringIO()):
            st.record(3, "rsd", {"h": [[f"d{i:05d}", [0]] for i in range(8)]})
        self.assertEqual(len(st.pending_heals), 8)
        self.assertEqual(st.pending_heals[0]["key"], "d00000")
        reloaded = CommandState(path=self.path)
        self.assertEqual(reloaded.pending_heals, st.pending_heals)
        self.assertEqual(reloaded.applied_ids, [1, 2, 3])
        self.assertTrue(all(h["wakes_left"] == 3 for h in reloaded.pending_heals))

    def test_cancel_and_all_refused_still_record_the_id(self):
        st = CommandState(path=self.path)
        st.record(1, "rsd", {"h": [["aaaaaa", [1]]]})
        st.record(2, "rsd", {"h": []})
        self.assertEqual(len(st.pending_heals), 1)
        self.assertTrue(st.is_duplicate(2))
        st.record(3, "rsd", {"x": 1})
        self.assertEqual(st.pending_heals, [])
        self.assertEqual(CommandState(path=self.path).applied_ids, [1, 2, 3])

    def test_tolerant_load(self):
        good = {"key": "aaaaaa", "n": [3, 1], "id": 7, "wakes_left": 2}
        with open(self.path, "w") as fh:
            json.dump({"pending_heals": [good, {"key": "BAD", "n": [1], "id": 1, "wakes_left": 1},
                                         {"key": "bbbbbb", "n": [], "id": 1, "wakes_left": 1},
                                         {"key": "cccccc", "n": [1], "id": 1, "wakes_left": 9},
                                         dict(good, id=8), "junk"]}, fh)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            st = CommandState(path=self.path)
        self.assertEqual(st.pending_heals, [{"key": "aaaaaa", "n": [1, 3], "id": 7, "wakes_left": 2}])
        self.assertIn("invalid; dropped", out.getvalue())

    def test_old_state_file_has_no_heals(self):
        with open(self.path, "w") as fh:
            json.dump({"settings": {}, "applied_ids": [5]}, fh)
        self.assertEqual(CommandState(path=self.path).pending_heals, [])


class TestDaemonDispatch(VideoTxHarness):
    """Real CommandDaemon + reader thread (FakeUart), validate fn on a real sent dir."""

    def setUp(self):
        super().setUp()
        self.sent_dir = os.path.join(self.tmp.name, "sent")
        write_old_record(self.sent_dir)
        self.daemon.heal_validate_fn = rc_heal.make_heal_validate_fn(self.sent_dir)
        with contextlib.redirect_stdout(io.StringIO()):
            self.daemon.start()
        self.addCleanup(self.stop_daemon)

    def stop_daemon(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.daemon.stop()

    def feed(self, body):
        self.uart.inject(make_cmd_frame(dict({"c": "rsd"}, **body)))
        deadline = time.monotonic() + 2
        events = []
        while not events and time.monotonic() < deadline:
            time.sleep(0.02)
            with contextlib.redirect_stdout(io.StringIO()):
                events = self.daemon.process_pending()
        self.assertEqual(len(events), 1)
        return events[0], json.loads(self.daemon._acks.pop())

    def test_partial_accept_is_ok(self):
        event, ack = self.feed({"id": 100010, "h": [[OLD_KEY, "63"], ["0dhzzz", "1"], [OLD_KEY[:5] + "x", "1"]]})
        self.assertEqual((event["action"], ack["ok"]), ("applied", 1))
        self.assertEqual([h["key"] for h in self.state.pending_heals], [OLD_KEY])
        self.assertEqual([(e["key"], e["a"], e["r"]) for e in self.daemon.heal_events],
                         [(OLD_KEY, "requested", "ok"), ("0dhzzz", "refused", "no_record"),
                          ("0dhnsx", "refused", "no_record")])

    def test_range_past_msgs_refused(self):
        event, ack = self.feed({"id": 100011, "h": [[OLD_KEY, "126"]]})
        self.assertEqual((ack["ok"], ack["e"]), (0, "rsd"))
        self.assertEqual(self.daemon.heal_events[-1]["r"], "range")

    def test_all_refused_records_id_so_resend_is_duplicate(self):
        _, ack = self.feed({"id": 100012, "h": [["0dhzzz", "1"]]})
        self.assertEqual((ack["ok"], ack["e"]), (0, "rsd"))
        self.assertTrue(self.state.is_duplicate(100012))
        n_events = len(self.daemon.heal_events)
        event, ack = self.feed({"id": 100012, "h": [["0dhzzz", "1"]]})
        self.assertEqual(event["action"], "duplicate")
        self.assertEqual(len(self.daemon.heal_events), n_events, "a re-send must not re-refuse")

    def test_no_validator_refuses(self):
        self.daemon.heal_validate_fn = None
        _, ack = self.feed({"id": 100013, "h": [[OLD_KEY, "1"]]})
        self.assertEqual(ack["ok"], 0)
        self.assertEqual(self.state.pending_heals, [])

    def test_malformed_rsd_is_val(self):
        _, ack = self.feed({"id": 100014, "h": [[OLD_KEY, "5-1"]]})
        self.assertEqual((ack["ok"], ack["e"]), (0, "val"))
        self.assertFalse(self.state.is_duplicate(100014))


class TestHealWire(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.sent_dir = os.path.join(self.d.name, "sent")
        write_old_record(self.sent_dir)
        self.rec = mk.find_sent_record(self.sent_dir, OLD_KEY)

    def test_byte_identical_to_the_original_send(self):
        for n, line in rc_heal.heal_lines(self.rec, [0, 63, 125]):
            self.assertEqual(line, golden_chunk_line(n), f"chunk {n}")

    def test_payload_changed_refused(self):
        with open(self.rec["payload"], "r+b") as fh:
            fh.write(b"X")
        with self.assertRaisesRegex(rc_heal.HealRefused, "payload_changed"):
            rc_heal.heal_lines(self.rec, [1])

    def test_hl_parses_like_the_backend(self):
        line = rc_heal.build_hl_message(OLD_KEY, "sent", 1, "ok", 100003, WAKE_KEY)
        kv = dict(RE_HL_KV.findall(line))
        self.assertEqual(kv, {"v": "1", "key": OLD_KEY, "a": "sent", "n": "1", "r": "ok",
                              "id": "100003", "w": WAKE_KEY})
        self.assertNotIn("w=", rc_heal.build_hl_message(OLD_KEY, "sent", 1, "ok", 1, None))


class FakeDaemon:
    def __init__(self, state):
        self.state = state
        self.heal_events = []


class TestWakeRules(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.sent_dir = os.path.join(self.d.name, "sent")
        write_old_record(self.sent_dir)
        self.state = CommandState(path=os.path.join(self.d.name, "state.json"))

    def wake(self, remaining_msgs=500, reserve=0):
        clk = [0.0]
        budget = CycleBudget(remaining_msgs, 1.0, clock=lambda: clk[0])
        wire = []
        with contextlib.redirect_stdout(io.StringIO()):
            heals = rc_heal.WakeHeals(FakeDaemon(self.state), self.sent_dir, {})
            heals.send_before_start(wire.append, budget, reserve_msgs=reserve, delay_seconds=1.0,
                                    sleep_fn=lambda s: clk.__setitem__(0, clk[0] + s))
        return heals, wire

    def test_cap_40_per_wake_then_rest_next_wake(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, list(range(0, 60))]]})   # direct: > 40 bypasses parse
        heals, wire = self.wake()
        self.assertEqual(len(wire), 40)
        self.assertEqual(self.state.pending_heals[0]["n"], list(range(40, 60)))
        self.assertEqual(self.state.pending_heals[0]["wakes_left"], 2)
        self.assertEqual(heals.outcomes[OLD_KEY]["r"], "partial")
        heals, wire = self.wake()
        self.assertEqual(len(wire), 20)
        self.assertEqual(self.state.pending_heals, [])
        self.assertEqual(heals.outcomes[OLD_KEY]["a"], "sent")

    def test_capture_room_is_reserved(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1, 2, 3, 4, 5]]]})
        _, wire = self.wake(remaining_msgs=130, reserve=127)
        self.assertEqual(len(wire), 3, "130 s at 1 s/msg: 3 heals + the capture's 127")

    def test_dropped_after_three_wakes(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1]]]})
        for expected_left in (2, 1):
            _, wire = self.wake(remaining_msgs=10, reserve=10)
            self.assertEqual(wire, [])
            self.assertEqual(self.state.pending_heals[0]["wakes_left"], expected_left)
        heals, _ = self.wake(remaining_msgs=10, reserve=10)
        self.assertEqual(self.state.pending_heals, [])
        self.assertEqual(heals.outcomes[OLD_KEY], {"a": "dropped", "n": 1, "r": "expired", "id": 1})

    def test_refused_at_send_time_is_removed(self):
        self.state.record(1, "rsd", {"h": [["0dhzzz", [1]]]})
        heals, wire = self.wake()
        self.assertEqual(wire, [])
        self.assertEqual(self.state.pending_heals, [])
        self.assertEqual(heals.outcomes["0dhzzz"]["a"], "refused")

    def test_heal_arriving_mid_wake_does_not_age(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1]]]})
        clk = [0.0]
        with contextlib.redirect_stdout(io.StringIO()):
            heals = rc_heal.WakeHeals(FakeDaemon(self.state), self.sent_dir, {},
                                      pump_fn=lambda: self.state.record(2, "rsd", {"h": [["0dhzzz", [7]]]}))
            heals.send_before_start(lambda b: None, CycleBudget(100, 1.0, clock=lambda: clk[0]),
                                    reserve_msgs=0, delay_seconds=1.0, sleep_fn=lambda s: None)
        self.assertEqual(self.state.pending_heals,
                         [{"key": "0dhzzz", "n": [7], "id": 2, "wakes_left": 3}])


class TestVideoCycleHeal(VideoTxHarness):
    """Ladder 1 end to end: real daemon, real sent record, golden clip, ordered wire."""

    def setUp(self):
        super().setUp()
        self.sent_dir = os.path.join(self.tmp.name, "sent")
        write_old_record(self.sent_dir)
        self.daemon.heal_validate_fn = rc_heal.make_heal_validate_fn(self.sent_dir)
        self.mk_cfg = {"enabled": True, "retain_days": 3.0, "source": "test", "sent_dir": self.sent_dir,
                       "state_path": os.path.join(self.tmp.name, "last_key")}

    def run_heal_vtx(self, **kw):
        """The S3 harness run, with the media key island on and this wake keyed from
        a Spotter read (the S3 fake gate reports no time source)."""
        import tests.test_s3_video_tx_daemon as s3

        def keyed_settings(tmp):
            s = vtx_settings(tmp)
            s["media_key_cfg"] = self.mk_cfg
            return s

        with mock.patch.object(s3, "settings", keyed_settings), \
             mock.patch.object(mk, "spotter_utc_for_wake",
                               lambda gate_info=None, daemon=None, timeout_s=20.0: (T_WAKE, "spotter")):
            return self.run_vtx(**kw)

    def img(self):
        return [(i, m) for i, (k, m) in enumerate(self.wire) if k == "img"]

    def test_heal_before_start_hl_after_end(self):
        self.state.record(100003, "rsd", {"h": [[OLD_KEY, [63]]]})
        summary, out = self.run_heal_vtx()
        self.assertIsNone(summary["error"], summary["error"])
        img = self.img()
        start = next(i for i, m in img if "<START IMG>" in m)
        end = next(i for i, m in img if "<END IMG>" in m)
        heal = [i for i, m in img if m.startswith(f"<I{OLD_KEY}.63>")]
        self.assertEqual(len(heal), 1)
        self.assertLess(heal[0], start, "heals go BEFORE the new START")
        self.assertEqual(self.wire[heal[0]][1].encode("ascii"), golden_chunk_line(63))
        self.assertIn(f"key={WAKE_KEY}", self.wire[start][1], "the new clip is keyed with this wake's key")
        hl = [i for i, m in img if m.startswith("<HL ")]
        self.assertEqual(len(hl), 1)
        self.assertGreater(hl[0], end)
        self.assertEqual(self.wire[hl[0]][1], f"<HL v=1 key={OLD_KEY} a=sent n=1 r=ok id=100003 w={WAKE_KEY}>\n")
        close, halt = self.idx("close")[0], self.idx("halt")[0]
        self.assertTrue(hl[0] < close < halt)
        self.assertEqual(CommandState(path=self.state_path).pending_heals, [])

    def test_rsd_mid_burst_acked_after_end_and_requested(self):
        frame = make_cmd_frame({"id": 100020, "c": "rsd", "h": [[OLD_KEY, "5,7-8"]]})
        summary, _ = self.run_heal_vtx(inject_at_sleep_call=20, inject_frame=frame)
        self.assertIsNone(summary["error"], summary["error"])
        start, end = self.idx("img", "<START IMG>")[0], self.idx("img", "<END IMG>")[0]
        acks = self.idx("ack", '"id":100020')
        self.assertTrue(acks and end < acks[0])
        self.assertFalse([i for i in self.idx("ack") if start < i < end])
        hl = self.idx("img", "<HL ")
        self.assertEqual(len(hl), 1)
        self.assertIn(f"a=requested n=3 r=ok id=100020 w={WAKE_KEY}", self.wire[hl[0]][1])
        self.assertLess(hl[0], self.idx("close")[0])
        pending = CommandState(path=self.state_path).pending_heals
        self.assertEqual(pending, [{"key": OLD_KEY, "n": [5, 7, 8], "id": 100020, "wakes_left": 3}])

    def test_nothing_pending_adds_nothing(self):
        summary, _ = self.run_heal_vtx()
        self.assertIsNone(summary["error"])
        self.assertEqual(self.idx("img", "<HL "), [])
        self.assertTrue(self.wire[self.img()[0][0]][1].startswith("<START IMG>"))


class TestStillsCycleHeal(IntegrationHarness):
    def test_heal_before_start_and_hl_after_end(self):
        sent_dir = os.path.join(self.tmpdir.name, "sent")
        write_old_record(sent_dir)
        self.state.record(100004, "rsd", {"h": [[OLD_KEY, [0, 1]]]})
        with mock.patch.object(rc_heal, "_sent_dir", lambda s: sent_dir):
            r = self.run_rc(transmit=True)
        self.assertIsNone(r.summary.get("error"))
        img = [m for k, m in self.wire if k == "img"]
        start = next(i for i, m in enumerate(img) if m.startswith("<START IMG>"))
        end = next(i for i, m in enumerate(img) if m.startswith("<END IMG>"))
        self.assertEqual([m.encode("ascii") for m in img[:start]],
                         [golden_chunk_line(0), golden_chunk_line(1)])
        self.assertEqual(img[end + 1], f"<HL v=1 key={OLD_KEY} a=sent n=2 r=ok id=100004>\n",
                         "no wake key (skip_time_window: no Spotter read) -> no w=")
        self.assertEqual(CommandState(path=self.state_path).pending_heals, [])


class TestBenchDrop(unittest.TestCase):
    def send(self, drop):
        wire, clk = [], [0.0]
        budget = CycleBudget(600, 1.0, clock=lambda: clk[0])
        with contextlib.redirect_stdout(io.StringIO()):
            r = transmit_video_clip(
                wire.append, budget, payload=PAYLOAD, file_name="c.h264", fps=10, dur=5.0,
                res="480x270", crop="na", crf=40, keyframe_chunks=6, chunk_b64_chars=384,
                delay_seconds=1.0, current_timestamp="2026-09-24T03:00:00Z",
                sleep_fn=lambda s: clk.__setitem__(0, clk[0] + s), clock=lambda: clk[0],
                media_key=OLD_KEY, bench_drop_chunks=drop)
        return r, wire, clk[0]

    def test_drop_skips_wire_keeps_pacing_and_count(self):
        base, base_wire, base_t = self.send(None)
        r, wire, t = self.send([63])
        self.assertEqual(t, base_t, "pacing unchanged")
        self.assertEqual((r["sent"], r["complete_send"]), (base["sent"], True))
        self.assertEqual([m for m in base_wire if not m.startswith(f"<I{OLD_KEY}.63>".encode())][:-1], wire[:-1],
                         "only chunk 63 differs (END carries the uart time)")
        self.assertFalse([m for m in wire if m.startswith(f"<I{OLD_KEY}.63>".encode())])
        self.assertEqual(len(wire), len(base_wire) - 1)


class TestManifest(unittest.TestCase):
    def test_every_runtime_import_is_deployed(self):
        """rc_media_key (S4) was missing from the deploy manifest: a deploy would have
        crashed every boot at import. Walk rc_progressive_jpeg's local imports."""
        man = set()
        with open(os.path.join(REPO_ROOT, "tools", "rc_runtime_manifest.txt")) as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if line:
                    man.add(os.path.basename(line.split(" -> ")[0].strip()))
        pydir = os.path.join(REPO_ROOT, "BM_Devel_Pi")
        todo, seen = ["rc_progressive_jpeg.py"], set()
        while todo:
            name = todo.pop()
            path = os.path.join(pydir, name)
            if name in seen or not os.path.exists(path):
                continue
            seen.add(name)
            with open(path, encoding="utf-8") as fh:
                todo += [m + ".py" for m in re.findall(r"^\s*(?:import|from)\s+([a-z_0-9]+)", fh.read(), re.M)]
        self.assertEqual(sorted(seen - man), [])


if __name__ == "__main__":
    unittest.main()
