#!/usr/bin/env python3
# filename: test_soak_scheduler.py
# description: Sprint10 soak — pin the scheduler's catch-up time logic.
"""Pins the late-fire bugfix: a recently-missed HH:MM fires now; an old
one schedules for its next future occurrence.

Run: python3 -m unittest tests.test_soak_scheduler -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

from soak_command_scheduler import next_occurrence  # noqa: E402

NOW = datetime(2026, 7, 27, 23, 5, tzinfo=timezone.utc)


class TestNextOccurrence(unittest.TestCase):
    def test_future_today(self):
        self.assertEqual(next_occurrence("23:30", NOW),
                         NOW.replace(hour=23, minute=30, second=0, microsecond=0))

    def test_recently_missed_fires_now(self):
        # 21:02 was 2h ago (< 3h lookback) -> returns the PAST time
        t = next_occurrence("21:02", NOW)
        self.assertLess(t, NOW)
        self.assertEqual((t.hour, t.minute, t.day), (21, 2, 27))

    def test_old_time_goes_tomorrow(self):
        # 07:30 was 15h ago -> next future occurrence (tomorrow)
        t = next_occurrence("07:30", NOW)
        self.assertGreater(t, NOW)
        self.assertEqual((t.hour, t.minute, t.day), (7, 30, 28))

    def test_after_midnight_entry_still_future(self):
        # 00:08 tonight (59 min ahead) must NOT be treated as missed
        t = next_occurrence("00:08", NOW)
        self.assertGreater(t, NOW)
        self.assertEqual((t.hour, t.minute, t.day), (0, 8, 28))


if __name__ == "__main__":
    unittest.main()
