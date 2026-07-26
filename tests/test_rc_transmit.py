#!/usr/bin/env python3
# filename: test_rc_transmit.py
# description: Sprint08 P5 — forced no-fit tests for M5 (rc_transmit bounded partial send).
"""
Sprint08 P5 — tests for the RC transmit loop and the incomplete-cycle path.

Harness: a recording fake `tx`, a fake clock, and a `sleep_fn` that ADVANCES
the fake clock by the requested pacing delay — so the budget drains exactly
as it would in the field, with zero wall-clock sleeps. Forced no-fit
scenarios follow the tracker row: the a=inc message, the bounded partial
send, and clean stops are all asserted on the actual emitted bytes.

Run (repo root; serial stubbed, no UART):
  python3 -m unittest tests.test_rc_transmit -v
  # or: python3 tests/test_rc_transmit.py
"""

import base64
import os
import sys
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

from rc_time_budget import CycleBudget  # noqa: E402
from rc_transmit import (  # noqa: E402
    INCOMPLETE_OVERHEAD_MSGS,
    split_base64_chunks,
    transmit_progressive_image,
)

CHUNK = 300
DELAY_S = 5.0
FILENAME = "2026-07-25T12:00:00Z_image_compressed.jpg"
TIMESTAMP = "2026-07-25T12:03:00Z"

# 225 raw bytes -> exactly 300 base64 chars, so 8 * 225 raw -> exactly 8 chunks.
JPEG_DATA = bytes(range(225)) * 8
PLANNED = 8


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class PacingSleep:
    """sleep_fn that advances the fake clock — field-accurate budget drain."""

    def __init__(self, clock, stall_after=None, stall_extra=0.0):
        self.clock = clock
        self.calls = []
        self.stall_after = stall_after
        self.stall_extra = float(stall_extra)

    def __call__(self, seconds):
        self.calls.append(float(seconds))
        self.clock.advance(seconds)
        if self.stall_after is not None and len(self.calls) > self.stall_after:
            self.clock.advance(self.stall_extra)  # simulate a mid-send stall


class RecordingTx:
    def __init__(self):
        self.messages = []

    def __call__(self, payload):
        self.messages.append(payload.decode("ascii"))


def run_transmit(budget, clock, *, fits, selector_reason=None, sleep=None, tx=None):
    tx = tx if tx is not None else RecordingTx()
    sleep = sleep if sleep is not None else PacingSleep(clock)
    result = transmit_progressive_image(
        tx,
        budget,
        jpeg_data=JPEG_DATA,
        compressed_file_name=FILENAME,
        quality=9 if not fits else 13,
        enc_attempts=4 if not fits else 1,
        fits=fits,
        selector_reason=selector_reason,
        chunk_b64_chars=CHUNK,
        delay_seconds=DELAY_S,
        cpu_temp_text="50.0",
        software_sha="abc123def456",
        hostname="bmcam000",
        current_timestamp=TIMESTAMP,
        sleep_fn=sleep,
        clock=clock,
    )
    return result, tx, sleep


class TestChunking(unittest.TestCase):
    def test_chunks_reassemble_to_full_base64(self):
        chunks = split_base64_chunks(JPEG_DATA, CHUNK)
        self.assertEqual(len(chunks), PLANNED)
        self.assertEqual("".join(chunks), base64.b64encode(JPEG_DATA).decode("ascii"))
        self.assertTrue(all(len(c) == CHUNK for c in chunks))


class TestCompleteSend(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.budget = CycleBudget(18 * 60, DELAY_S, clock=self.clock)
        self.result, self.tx, self.sleep = run_transmit(self.budget, self.clock, fits=True)

    def test_message_sequence_and_framing(self):
        msgs = self.tx.messages
        # START, I0..I7, END — production framing exactly.
        self.assertEqual(len(msgs), 1 + PLANNED + 1)
        self.assertTrue(msgs[0].startswith("<START IMG> "))
        chunks = split_base64_chunks(JPEG_DATA, CHUNK)
        for i in range(PLANNED):
            self.assertEqual(msgs[1 + i], f"<I{i}>{chunks[i]}\n")  # byte-parity w/ production loop
        self.assertTrue(msgs[-1].startswith("<END IMG> "))

    def test_start_fields(self):
        start = self.tx.messages[0]
        self.assertIn(f"length: {PLANNED}", start)
        self.assertIn("fmt=pjpg", start)
        self.assertIn("q=13", start)
        self.assertIn("att=1", start)
        self.assertIn("cmp=1", start)
        self.assertNotIn("rsn=", start)

    def test_no_incomplete_message(self):
        self.assertFalse(any("a=inc" in m for m in self.tx.messages))
        self.assertFalse(self.result["incomplete_emitted"])

    def test_end_reports_all_sent(self):
        self.assertIn(f"sent_buffers: {PLANNED}", self.tx.messages[-1])
        self.assertEqual(self.result["sent"], PLANNED)
        self.assertTrue(self.result["complete_send"])

    def test_pacing(self):
        # Production rhythm: sleep after START and after every chunk; none after END.
        self.assertEqual(self.sleep.calls, [DELAY_S] * (1 + PLANNED))
        self.assertAlmostEqual(self.result["uart_duration_sec"], (1 + PLANNED) * DELAY_S)


class TestBoundedPartialSend(unittest.TestCase):
    """Forced no-fit: budget holds only 7 paced messages -> send_target 4."""

    def setUp(self):
        self.clock = FakeClock()
        self.budget = CycleBudget(7 * DELAY_S, DELAY_S, clock=self.clock)
        self.result, self.tx, self.sleep = run_transmit(
            self.budget, self.clock, fits=False, selector_reason="no_fit_budget"
        )

    def test_send_target_reserves_overhead(self):
        self.assertEqual(INCOMPLETE_OVERHEAD_MSGS, 3)
        self.assertEqual(self.result["send_target"], 7 - 3)

    def test_incomplete_message_emitted_first(self):
        first = self.tx.messages[0]
        self.assertIn("a=inc", first)
        self.assertIn("fmt=pjpg", first)
        self.assertIn("q=9", first)
        self.assertIn("att=4", first)
        self.assertIn("rsn=budget", first)
        self.assertIn(f"pln={PLANNED}", first)
        self.assertIn("snd=4", first)

    def test_start_announces_planned_not_bounded(self):
        start = self.tx.messages[1]
        self.assertIn(f"length: {PLANNED}", start)  # length = PLANNED (P5 decision)
        self.assertIn("cmp=0", start)
        self.assertIn("rsn=budget", start)

    def test_exactly_bounded_chunks_then_end(self):
        msgs = self.tx.messages
        # inc, START, I0..I3, END
        self.assertEqual(len(msgs), 1 + 1 + 4 + 1)
        chunks = split_base64_chunks(JPEG_DATA, CHUNK)
        for i in range(4):
            self.assertEqual(msgs[2 + i], f"<I{i}>{chunks[i]}\n")
        self.assertIn("sent_buffers: 4", msgs[-1])
        self.assertEqual(self.result["sent"], 4)
        self.assertFalse(self.result["complete_send"])
        self.assertTrue(self.result["incomplete_emitted"])

    def test_sent_prefix_is_decodable_progressive_prefix(self):
        # The sent chunks form the exact base64 prefix — the S07 P4 render premise.
        payload = "".join(
            m[m.index(">") + 1:].rstrip("\n") for m in self.tx.messages[2:6]
        )
        self.assertEqual(payload, base64.b64encode(JPEG_DATA).decode("ascii")[: 4 * CHUNK])

    def test_budget_respected(self):
        # inc + START + 4 chunks paced = 6 slots = 30 s; END rides in the reserve.
        self.assertLessEqual(self.clock.now - 1000.0, 7 * DELAY_S)


class TestZeroSendCase(unittest.TestCase):
    def test_only_inc_emitted_when_no_room(self):
        clock = FakeClock()
        budget = CycleBudget(3 * DELAY_S, DELAY_S, clock=clock)  # max 3 msgs -> send_target 0
        result, tx, _ = run_transmit(budget, clock, fits=False, selector_reason="no_fit_budget")
        self.assertEqual(len(tx.messages), 1)
        self.assertIn("a=inc", tx.messages[0])
        self.assertIn("snd=0", tx.messages[0])
        self.assertEqual(result["sent"], 0)
        self.assertFalse(result["started"])
        self.assertFalse(result["complete_send"])

    def test_cap_reason_rides_the_wire(self):
        clock = FakeClock()
        budget = CycleBudget(7 * DELAY_S, DELAY_S, clock=clock)
        _, tx, _ = run_transmit(budget, clock, fits=False, selector_reason="no_fit_cap")
        self.assertIn("rsn=cap", tx.messages[0])
        self.assertIn("rsn=cap", tx.messages[1])


class TestMidSendStall(unittest.TestCase):
    def test_stall_stops_loop_and_still_sends_end(self):
        clock = FakeClock()
        # Fits exactly fresh: START + 8 chunks + END = 10 paced slots.
        budget = CycleBudget(10 * DELAY_S, DELAY_S, clock=clock)
        stall_sleep = PacingSleep(clock, stall_after=3, stall_extra=3 * DELAY_S)
        result, tx, _ = run_transmit(
            budget, clock, fits=True, sleep=stall_sleep, tx=RecordingTx()
        )
        # The stall burned budget mid-send: fewer than 8 chunks, but END closed.
        self.assertLess(result["sent"], PLANNED)
        self.assertGreater(result["sent"], 0)
        self.assertTrue(tx.messages[-1].startswith("<END IMG> "))
        self.assertIn(f"sent_buffers: {result['sent']}", tx.messages[-1])
        self.assertFalse(result["complete_send"])
        # Chunk indices remain contiguous from 0 (a clean prefix, still renderable).
        chunk_msgs = [m for m in tx.messages if m.startswith("<I")]
        for i, m in enumerate(chunk_msgs):
            self.assertTrue(m.startswith(f"<I{i}>"))


class TestInputValidation(unittest.TestCase):
    def test_bad_chunk_size_rejected(self):
        with self.assertRaises(ValueError):
            split_base64_chunks(b"data", 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
