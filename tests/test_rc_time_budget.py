#!/usr/bin/env python3
# filename: test_rc_time_budget.py
# description: Sprint08 P1 fake-clock unit tests for M1 (rc_time_budget.CycleBudget).
"""
Sprint08 P1 — fake-clock unit tests for the M1 cycle-budget accountant.

M1 is pure logic, so every test drives an injected fake clock; no sleeping,
no hardware, no wall-clock dependence. Field numbers used throughout are the
Sprint07 §4 values: 18 min budget, 5 s/msg pacing, 195-msg cap.

Run (repo root; stdlib only):
  python3 -m unittest tests.test_rc_time_budget -v
  # or: python3 tests/test_rc_time_budget.py
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

from rc_time_budget import CycleBudget  # noqa: E402

BUDGET_S = 18 * 60  # 1080 s
DELAY_S = 5.0


class FakeClock:
    """Injectable monotonic clock: starts at an arbitrary origin, advances on demand."""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def make_budget(clock, budget_seconds=BUDGET_S, seconds_per_message=DELAY_S):
    return CycleBudget(
        budget_seconds=budget_seconds,
        seconds_per_message=seconds_per_message,
        clock=clock,
    )


class TestFreshBudget(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.budget = make_budget(self.clock)

    def test_initial_values(self):
        self.assertEqual(self.budget.elapsed_s(), 0.0)
        self.assertEqual(self.budget.remaining_s(), BUDGET_S)
        self.assertFalse(self.budget.exhausted())

    def test_full_budget_holds_216_paced_messages(self):
        # 1080 s / 5 s per message = 216 (matches the P0 skeleton's derived line).
        self.assertEqual(self.budget.max_messages_now(), 216)
        self.assertTrue(self.budget.messages_fit(216))
        self.assertFalse(self.budget.messages_fit(217))

    def test_sprint07_reference_counts_fit_fresh(self):
        # S07 §4 worst cases (+2 for START/END is the caller's job — shown here
        # exactly as M3/M5 will ask): q9 fleet worst 126, q15 worst coral 188,
        # 195-msg hard cap.
        for chunks in (126, 188):
            self.assertTrue(self.budget.messages_fit(chunks + 2))
        # The 195-msg cap + overhead needs 985 s <= 1080 s: still fits fresh.
        self.assertTrue(self.budget.messages_fit(195 + 2))


class TestClockAdvance(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.budget = make_budget(self.clock)

    def test_remaining_decreases_as_clock_advances(self):
        self.clock.advance(100)
        self.assertEqual(self.budget.elapsed_s(), 100.0)
        self.assertEqual(self.budget.remaining_s(), BUDGET_S - 100.0)

    def test_one_budget_charged_from_cycle_start(self):
        # Capture (5.3 s) + prep (2.4 s) + 3 encode attempts (0.07 s each) all
        # charge the SAME budget the transmit fit-check then consults.
        self.clock.advance(5.3)
        self.clock.advance(2.4)
        for _ in range(3):
            self.clock.advance(0.07)
        spent = 5.3 + 2.4 + 3 * 0.07
        self.assertAlmostEqual(self.budget.remaining_s(), BUDGET_S - spent, places=9)
        # 169 chunks (S07 prog q13 worst case) + START/END no longer computes
        # against the full 1080 s but against what is actually left.
        self.assertEqual(self.budget.max_messages_now(), int((BUDGET_S - spent) // DELAY_S))

    def test_fit_flips_false_when_time_runs_short(self):
        n = 169 + 2  # S07 q13 worst case + START/END
        self.assertTrue(self.budget.messages_fit(n))
        # Leave exactly one message's time too little: need 855 s, leave 854 s.
        self.clock.advance(BUDGET_S - (n * DELAY_S - 1.0))
        self.assertFalse(self.budget.messages_fit(n))
        self.assertTrue(self.budget.messages_fit(n - 1))


class TestBoundarySemantics(unittest.TestCase):
    def test_exact_fit_counts_as_fitting(self):
        clock = FakeClock()
        budget = make_budget(clock)
        # Advance so remaining is exactly 10 messages' worth.
        clock.advance(BUDGET_S - 10 * DELAY_S)
        self.assertEqual(budget.remaining_s(), 10 * DELAY_S)
        self.assertTrue(budget.messages_fit(10))
        self.assertFalse(budget.messages_fit(11))
        self.assertEqual(budget.max_messages_now(), 10)

    def test_zero_messages_always_fit_until_exhaustion(self):
        clock = FakeClock()
        budget = make_budget(clock)
        clock.advance(BUDGET_S)  # exactly exhausted
        self.assertTrue(budget.messages_fit(0))  # 0 s cost fits in 0 s remaining
        self.assertFalse(budget.messages_fit(1))

    def test_has_time_for_encode_attempt(self):
        clock = FakeClock()
        budget = make_budget(clock)
        clock.advance(BUDGET_S - 0.05)
        self.assertTrue(budget.has_time_for(0.03))   # S07 attempt cost lower bound
        self.assertFalse(budget.has_time_for(0.07))  # upper bound no longer fits

    def test_fractional_delay(self):
        clock = FakeClock()
        budget = make_budget(clock, budget_seconds=10.0, seconds_per_message=2.5)
        self.assertEqual(budget.max_messages_now(), 4)
        clock.advance(1.0)
        self.assertEqual(budget.max_messages_now(), 3)
        self.assertTrue(budget.messages_fit(3))
        self.assertFalse(budget.messages_fit(4))


class TestExhaustion(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.budget = make_budget(self.clock)

    def test_exactly_exhausted(self):
        self.clock.advance(BUDGET_S)
        self.assertTrue(self.budget.exhausted())
        self.assertEqual(self.budget.remaining_s(), 0.0)
        self.assertEqual(self.budget.max_messages_now(), 0)

    def test_overrun_clamps_to_zero_never_negative(self):
        self.clock.advance(BUDGET_S + 500)
        self.assertEqual(self.budget.remaining_s(), 0.0)
        self.assertEqual(self.budget.elapsed_s(), BUDGET_S + 500)
        self.assertTrue(self.budget.exhausted())
        self.assertFalse(self.budget.messages_fit(1))
        self.assertFalse(self.budget.has_time_for(0.01))
        self.assertEqual(self.budget.max_messages_now(), 0)


class TestConstructionAndInputs(unittest.TestCase):
    def test_invalid_construction_rejected(self):
        clock = FakeClock()
        for bad_budget in (0, -5):
            with self.assertRaises(ValueError):
                CycleBudget(bad_budget, DELAY_S, clock=clock)
        for bad_delay in (0, -1):
            with self.assertRaises(ValueError):
                CycleBudget(BUDGET_S, bad_delay, clock=clock)

    def test_invalid_queries_rejected(self):
        budget = make_budget(FakeClock())
        with self.assertRaises(ValueError):
            budget.messages_fit(-1)
        with self.assertRaises(ValueError):
            budget.has_time_for(-0.1)

    def test_no_hidden_reserves(self):
        # PURE accounting (P1 decision): the full budget is available; nothing
        # is silently held back for END/halt/margin.
        budget = make_budget(FakeClock())
        self.assertEqual(budget.remaining_s(), budget.budget_seconds)
        self.assertTrue(budget.messages_fit(int(BUDGET_S // DELAY_S)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
