#!/usr/bin/env python3
# filename: test_rc_quality_selector.py
# description: Sprint08 P3 — forced step-down tests for M3 (rc_quality_selector).
"""
Sprint08 P3 — tests for the M3 adaptive quality selector.

Strategy: SELF-CALIBRATING forced step-downs. Synthetic 1000x562 sources are
generated in-test (seeded noise = high detail, flat color = trivial detail);
each test first measures the real per-rung message counts via M2, then picks
message_cap / fake-clock budget values that force exactly the behavior under
test. No magic byte counts to rot.

Pure off-device: fake clock, no sleeps, no serial stub needed (M3 imports
only M1/M2). Needs PIL.

Run (repo root):
  python3 -m unittest tests.test_rc_quality_selector -v
  # or: python3 tests/test_rc_quality_selector.py
"""

import os
import random
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

from PIL import Image  # noqa: E402

from rc_jpeg_encoder import encode_progressive  # noqa: E402
from rc_quality_selector import (  # noqa: E402
    ENCODE_ATTEMPT_ALLOWANCE_S,
    TRANSMIT_OVERHEAD_MSGS,
    compute_quality_ladder,
    parse_ladder_spec,
    select_quality,
)
from rc_time_budget import CycleBudget  # noqa: E402

SIZE = (1000, 562)  # frozen RC output size
CHUNK = 300
DELAY_S = 5.0
LADDER_ARGS = dict(q_max=15, q_min=9, q_step=2)  # select_quality kwargs


def ladder():
    return compute_quality_ladder(
        LADDER_ARGS["q_max"], LADDER_ARGS["q_min"], LADDER_ARGS["q_step"]
    )
HUGE_CAP = 10**6
HUGE_BUDGET_S = 10**7


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def make_budget(clock, budget_seconds=HUGE_BUDGET_S):
    return CycleBudget(budget_seconds=budget_seconds, seconds_per_message=DELAY_S, clock=clock)


def noise_source(seed=42):
    """High-detail synthetic source: seeded random RGB noise (deterministic)."""
    rng = random.Random(seed)
    data = rng.randbytes(SIZE[0] * SIZE[1] * 3)
    return Image.frombytes("RGB", SIZE, data)


def flat_source():
    """Trivial-detail source: flat mid-gray."""
    return Image.new("RGB", SIZE, (128, 128, 128))


class TestSelectorBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.noise = noise_source()
        cls.flat = flat_source()
        # Calibrate the real per-rung message counts once (M2 is deterministic).
        cls.counts = {
            q: encode_progressive(cls.noise, q, CHUNK)["message_count"]
            for q in ladder()
        }

    def test_calibration_counts_strictly_decrease(self):
        # The forcing logic below relies on lower q -> fewer messages.
        rungs = ladder()
        for higher, lower in zip(rungs, rungs[1:]):
            self.assertGreater(
                self.counts[higher], self.counts[lower],
                msg=f"expected msgs(q{higher}) > msgs(q{lower}); counts={self.counts}",
            )


class TestFitAtTop(TestSelectorBase):
    def test_flat_image_fits_first_try(self):
        budget = make_budget(FakeClock(), budget_seconds=18 * 60)
        result = select_quality(
            self.flat, budget, message_cap=195, chunk_b64_chars=CHUNK, ladder=ladder()
        )
        self.assertTrue(result["fits"])
        self.assertEqual(result["reason"], "fit")
        self.assertEqual(result["quality"], 15)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(result["attempt_log"]), 1)
        self.assertEqual(result["encode"]["quality"], 15)


class TestCapForcedStepDown(TestSelectorBase):
    def test_cap_between_q15_and_q13_steps_once(self):
        # Cap admits q13 but not q15; budget effectively unlimited.
        cap = self.counts[13]
        budget = make_budget(FakeClock())
        result = select_quality(
            self.noise, budget, message_cap=cap, chunk_b64_chars=CHUNK, ladder=ladder()
        )
        self.assertTrue(result["fits"])
        self.assertEqual(result["quality"], 13)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual([a["quality"] for a in result["attempt_log"]], [15, 13])
        self.assertTrue(result["attempt_log"][0]["over_cap"])
        self.assertTrue(result["attempt_log"][0]["budget_fit"])  # budget was fine; cap forced it

    def test_cap_below_floor_returns_floor_no_fit(self):
        cap = self.counts[9] - 1
        budget = make_budget(FakeClock())
        result = select_quality(
            self.noise, budget, message_cap=cap, chunk_b64_chars=CHUNK, ladder=ladder()
        )
        self.assertFalse(result["fits"])
        self.assertEqual(result["reason"], "no_fit_cap")
        self.assertEqual(result["quality"], 9)
        self.assertEqual(result["attempts"], 4)
        # The floor's bytes ARE returned for M5's bounded partial send.
        direct = encode_progressive(self.noise, 9, CHUNK)
        self.assertEqual(result["encode"]["jpeg_sha256"], direct["jpeg_sha256"])


class TestBudgetForcedStepDown(TestSelectorBase):
    def test_budget_admits_only_q11(self):
        # Remaining budget fits q11 (+2 overhead) but not q13.
        clock = FakeClock()
        budget_seconds = (self.counts[11] + TRANSMIT_OVERHEAD_MSGS) * DELAY_S
        budget = make_budget(clock, budget_seconds=budget_seconds)
        result = select_quality(
            self.noise, budget, message_cap=HUGE_CAP, chunk_b64_chars=CHUNK, ladder=ladder()
        )
        self.assertTrue(result["fits"])
        self.assertEqual(result["quality"], 11)
        self.assertEqual(result["attempts"], 3)
        self.assertEqual([a["quality"] for a in result["attempt_log"]], [15, 13, 11])
        self.assertFalse(result["attempt_log"][0]["budget_fit"])
        self.assertFalse(result["attempt_log"][0]["over_cap"])  # cap was fine; budget forced it

    def test_elapsed_time_charged_before_selection(self):
        # A budget that fits q13 fresh stops fitting after the clock advances —
        # capture/prep time and encode retries all drain the ONE budget.
        clock = FakeClock()
        budget_seconds = (self.counts[13] + TRANSMIT_OVERHEAD_MSGS) * DELAY_S
        budget = make_budget(clock, budget_seconds=budget_seconds)
        clock.advance((self.counts[13] - self.counts[11]) * DELAY_S)  # eat the q13 slack
        result = select_quality(
            self.noise, budget, message_cap=HUGE_CAP, chunk_b64_chars=CHUNK, ladder=ladder()
        )
        self.assertTrue(result["fits"])
        self.assertEqual(result["quality"], 11)

    def test_no_fit_even_at_floor_returns_floor_bytes(self):
        clock = FakeClock()
        # One second short of what the floor needs.
        budget_seconds = (self.counts[9] + TRANSMIT_OVERHEAD_MSGS) * DELAY_S - 1.0
        budget = make_budget(clock, budget_seconds=budget_seconds)
        result = select_quality(
            self.noise, budget, message_cap=HUGE_CAP, chunk_b64_chars=CHUNK, ladder=ladder()
        )
        self.assertFalse(result["fits"])
        self.assertEqual(result["reason"], "no_fit_budget")
        self.assertEqual(result["quality"], 9)
        self.assertEqual(result["attempts"], 4)
        self.assertEqual([a["quality"] for a in result["attempt_log"]], [15, 13, 11, 9])
        direct = encode_progressive(self.noise, 9, CHUNK)
        self.assertEqual(result["encode"]["jpeg_sha256"], direct["jpeg_sha256"])
        self.assertEqual(result["encode"]["message_count"], self.counts[9])


class TestOverheadBoundary(TestSelectorBase):
    """Pins the +2 START/END charge M1's pure design pushes onto M3."""

    def test_exact_chunks_only_budget_fails(self):
        n = self.counts[9]
        budget = make_budget(FakeClock(), budget_seconds=n * DELAY_S)
        result = select_quality(
            self.noise, budget, message_cap=HUGE_CAP, chunk_b64_chars=CHUNK,
            ladder=[9],
        )
        self.assertFalse(result["fits"])  # chunks alone fit, chunks+START/END don't

    def test_chunks_plus_overhead_budget_passes(self):
        n = self.counts[9]
        budget = make_budget(
            FakeClock(), budget_seconds=(n + TRANSMIT_OVERHEAD_MSGS) * DELAY_S
        )
        result = select_quality(
            self.noise, budget, message_cap=HUGE_CAP, chunk_b64_chars=CHUNK,
            ladder=[9],
        )
        self.assertTrue(result["fits"])
        self.assertEqual(result["attempts"], 1)


class TestEncodeAttemptGuard(TestSelectorBase):
    def test_no_time_for_any_encode(self):
        clock = FakeClock()
        budget = make_budget(clock, budget_seconds=100.0)
        clock.advance(100.0 - ENCODE_ATTEMPT_ALLOWANCE_S / 2)  # 0.5 s left < 1.0 s allowance
        result = select_quality(
            self.noise, budget, message_cap=HUGE_CAP, chunk_b64_chars=CHUNK, ladder=ladder()
        )
        self.assertFalse(result["fits"])
        self.assertEqual(result["reason"], "no_time_for_encode")
        self.assertEqual(result["attempts"], 0)
        self.assertIsNone(result["encode"])
        self.assertIsNone(result["quality"])

    def test_allowance_is_conservative_vs_sprint07(self):
        # Documented assumption: allowance >= ~15x the S07-measured 0.063 s max.
        self.assertGreaterEqual(ENCODE_ATTEMPT_ALLOWANCE_S, 15 * 0.063)


class TestInputValidation(TestSelectorBase):
    def test_bad_cap_rejected(self):
        budget = make_budget(FakeClock())
        with self.assertRaises(ValueError):
            select_quality(
                self.flat, budget, message_cap=0, chunk_b64_chars=CHUNK, ladder=ladder()
            )

    def test_bad_ladder_rejected(self):
        budget = make_budget(FakeClock())
        with self.assertRaises(ValueError):
            select_quality(
                self.flat, budget, message_cap=195, chunk_b64_chars=CHUNK,
                ladder=[9, 15],  # ascending
            )
        with self.assertRaises(ValueError):
            select_quality(
                self.flat, budget, message_cap=195, chunk_b64_chars=CHUNK,
                ladder=[],
            )


class TestLadderSpecParsing(TestSelectorBase):
    def test_field_trial_spec(self):
        self.assertEqual(
            parse_ladder_spec("90,80,70,60,50,40,30,25,20,15,13,11,9"),
            [90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 13, 11, 9],
        )

    def test_spaces_tolerated(self):
        self.assertEqual(parse_ladder_spec("15, 13, 11, 9"), [15, 13, 11, 9])

    def test_invalid_specs_rejected(self):
        for bad in ("", "abc", "15,15,9", "9,15", "0,5", "96,90"):
            with self.assertRaises(ValueError):
                parse_ladder_spec(bad)

    def test_multi_segment_walk(self):
        # Selector walks an explicit multi-segment ladder in order.
        budget = make_budget(FakeClock())
        cap = self.counts[11]  # only q11 and below fit the cap
        result = select_quality(
            self.noise, budget, message_cap=cap, chunk_b64_chars=CHUNK,
            ladder=[15, 13, 11, 9],
        )
        self.assertEqual([a["quality"] for a in result["attempt_log"]], [15, 13, 11])
        self.assertEqual(result["quality"], 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
