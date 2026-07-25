#!/usr/bin/env python3
# filename: rc_time_budget.py
# description: Sprint08 M1 — cycle time-budget accountant for the progressive-JPEG RC.
"""
Sprint08 M1 — the single cycle-budget authority (sprint spec section 2).

Exactly one CycleBudget is created per RC cycle, at cycle start. Capture,
every encode attempt, and transmit all *ask it* whether there is time;
nothing else keeps its own clock. Pure logic: no hardware, no sleeping,
no logging side effects — callers log what they decide.

Design decisions (P1 kickoff, Nick-approved):
  - PURE accounting: this module charges exactly what it is asked about and
    reserves nothing hidden. Message-overhead accounting (START/END = +2)
    is the caller's job (M3/M5), enforced by their tests.
  - Monotonic clock by default, so a mid-cycle Spotter/RTC system-clock set
    cannot corrupt the budget. `clock` is injectable for fake-clock tests.
  - Boundary semantics: an exact fit counts as fitting
    (remaining == n * seconds_per_message -> messages_fit(n) is True),
    because each paced message's cost already includes its trailing sleep.

Inputs:  budget_seconds (from progressive_jpeg.max_run_time_min * 60),
         seconds_per_message (from bm_serial.image_transmit_delay_seconds).
Outputs: query methods only; no state mutation after construction.

Example:
  budget = CycleBudget(budget_seconds=18 * 60, seconds_per_message=5.0)
  ...
  if budget.messages_fit(estimated_chunks + 2):   # +2 = START + END (caller's job)
      transmit()

Known limitations: float arithmetic at the fit boundary is exact only for
values representable in binary (5.0, 0.5, ...); with pathological decimal
delays an exact-boundary fit may fall either way by ~1e-9 s. Irrelevant at
field pacing (5 s/msg).
"""

import math
import time


class CycleBudget:
    """Single per-cycle time-budget authority (M1). Pure; no side effects."""

    def __init__(self, budget_seconds, seconds_per_message, clock=time.monotonic):
        budget_seconds = float(budget_seconds)
        seconds_per_message = float(seconds_per_message)
        if budget_seconds <= 0:
            raise ValueError(f"budget_seconds must be > 0, got {budget_seconds}")
        if seconds_per_message <= 0:
            raise ValueError(f"seconds_per_message must be > 0, got {seconds_per_message}")

        self._clock = clock
        self._budget_seconds = budget_seconds
        self._seconds_per_message = seconds_per_message
        # The cycle starts when the budget is constructed: one start, one deadline.
        self._start = clock()

    # -- fixed facts -------------------------------------------------------

    @property
    def budget_seconds(self):
        return self._budget_seconds

    @property
    def seconds_per_message(self):
        return self._seconds_per_message

    # -- time queries ------------------------------------------------------

    def elapsed_s(self):
        """Seconds since cycle start (never negative)."""
        return max(0.0, self._clock() - self._start)

    def remaining_s(self):
        """Seconds left in the budget, clamped to 0 once exhausted."""
        return max(0.0, self._budget_seconds - self.elapsed_s())

    def exhausted(self):
        """True once no budget remains."""
        return self.remaining_s() <= 0.0

    # -- fit queries -------------------------------------------------------

    def has_time_for(self, seconds):
        """Will an action costing `seconds` fit in the remaining budget?

        Generic: M3 uses it for encode attempts (~0.03-0.07 s each).
        An exact fit counts as fitting.
        """
        seconds = float(seconds)
        if seconds < 0:
            raise ValueError(f"seconds must be >= 0, got {seconds}")
        return self.remaining_s() >= seconds

    def messages_fit(self, n_messages):
        """Will n paced messages fit in the remaining budget?

        Pure: charges exactly n * seconds_per_message. Callers must include
        their own overhead messages (e.g. START/END = +2) in n.
        """
        n_messages = int(n_messages)
        if n_messages < 0:
            raise ValueError(f"n_messages must be >= 0, got {n_messages}")
        return self.has_time_for(n_messages * self._seconds_per_message)

    def max_messages_now(self):
        """Largest paced message count that still fits (M5 bounded partial send)."""
        return int(math.floor(self.remaining_s() / self._seconds_per_message))
