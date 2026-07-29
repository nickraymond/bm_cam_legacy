#!/usr/bin/env python3
# filename: test_rc_transmit_phase.py
# description: Sprint11 C2 — fake-clock tests for phase-aware transmit scheduling.
"""
Sprint11 C2 tests — the scheduler that makes the burst MISS the 5-minute
blackout instead of surviving it.

These are the tests the sprint kickoff calls out as mattering most, in
particular TestClockFailureFallback: the clock-read-failure path is the one
that fails SILENTLY in the field (a field unit has no USB and no backend, so
a wrong-but-plausible schedule looks identical to a right one from shore).

Pure fake time throughout -- no sleeps, no hardware, runs in milliseconds.

Run (repo root):
  python3 -m unittest tests.test_rc_transmit_phase -v
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_transmit_phase as tp  # noqa: E402

# The shipped grid: 300 s period, 30 s post-boundary guard, 20 s pre-guard.
GRID = 300.0
POST = 30.0
PRE = 20.0
LANE = 250.0

# A 194-message image at 1.0 s pacing, plus START and END = 196 paced slots.
BURST_1S = 196.0
# The same image at 1.5 s pacing -- deliberately does NOT fit a lane (D3).
BURST_15S = 294.0


def at_phase(phase_s, base_boundary=1785349200.0):
    """An absolute epoch that sits `phase_s` past a 5-minute boundary.

    base_boundary is a real measured boundary (2026-07-29T18:20:00Z) and is
    exactly divisible by 300, so `epoch % 300` is the phase by construction.
    """
    assert base_boundary % GRID == 0, "base must be a grid boundary"
    return base_boundary + phase_s


class TestLaneGeometry(unittest.TestCase):
    def test_shipped_guards_give_the_250s_lane_from_design_d3(self):
        self.assertEqual(tp.usable_lane_seconds(GRID, POST, PRE), LANE)

    def test_design_d3_pacing_feasibility_table(self):
        """delay x cap <= lane is the config rule. Only 1.0/1.25 s fit 194."""
        for delay, expected_fit in ((1.0, True), (1.25, True),
                                    (1.5, False), (2.0, False)):
            burst = 194 * delay
            self.assertEqual(burst <= LANE, expected_fit,
                             f"{delay}s pacing x 194 msgs = {burst}s vs lane {LANE}s")

    def test_unusable_guard_config_raises_rather_than_silently_misplanning(self):
        with self.assertRaises(ValueError):
            tp.plan_transmit(at_phase(0), 10.0, grid_seconds=300.0,
                             post_guard_s=200.0, pre_guard_s=200.0)


class TestPhasePlanning(unittest.TestCase):
    """The four phases named in TRACKER §2."""

    def plan(self, phase_s, burst=BURST_1S, **kw):
        return tp.plan_transmit(at_phase(phase_s), burst,
                                grid_seconds=GRID, post_guard_s=POST,
                                pre_guard_s=PRE, **kw)

    # --- just after a boundary: sit out the blackout -----------------------
    def test_exactly_on_boundary_waits_the_full_post_guard(self):
        p = self.plan(0.0)
        self.assertEqual(p["reason"], "wait_post_guard")
        self.assertAlmostEqual(p["wait_s"], 30.0)
        self.assertAlmostEqual(p["start_phase_s"], 30.0)

    def test_inside_the_blackout_guard_waits_out_the_remainder(self):
        p = self.plan(9.0)          # median measured blackout duration
        self.assertEqual(p["reason"], "wait_post_guard")
        self.assertAlmostEqual(p["wait_s"], 21.0)
        self.assertAlmostEqual(p["start_phase_s"], 30.0)
        self.assertAlmostEqual(p["end_phase_s"], 30.0 + BURST_1S)
        self.assertFalse(p["crosses_boundary"])

    # --- mid lane: go now --------------------------------------------------
    def test_lane_start_is_inclusive(self):
        p = self.plan(POST)
        self.assertEqual(p["reason"], "in_lane")
        self.assertEqual(p["wait_s"], 0.0)

    def test_mid_lane_with_room_transmits_immediately(self):
        p = self.plan(60.0)
        self.assertEqual(p["reason"], "in_lane")
        self.assertEqual(p["wait_s"], 0.0)
        self.assertAlmostEqual(p["end_phase_s"], 256.0)
        self.assertFalse(p["crosses_boundary"])

    def test_capture_first_timing_from_design_d2_lands_in_lane(self):
        """C1 puts transmit start near :01:00; that must need no wait at all.

        This is the arithmetic the whole sprint rests on: boot ~55 s +
        capture/encode ~5 s -> phase ~60 s, and 196 s of burst ends at
        256 s, i.e. 24 s clear of the pre-boundary guard.
        """
        p = self.plan(60.0)
        self.assertEqual(p["wait_s"], 0.0)
        self.assertLessEqual(p["end_phase_s"], GRID - PRE)

    def test_last_instant_that_still_fits_this_lane(self):
        p = self.plan(GRID - PRE - BURST_1S)     # 84.0
        self.assertEqual(p["reason"], "in_lane")
        self.assertEqual(p["wait_s"], 0.0)
        self.assertAlmostEqual(p["end_phase_s"], GRID - PRE)

    def test_one_second_later_defers_to_the_next_lane(self):
        p = self.plan(GRID - PRE - BURST_1S + 1.0)   # 85.0
        self.assertEqual(p["reason"], "wait_next_lane")

    # --- too late in the lane: take the next one ---------------------------
    def test_no_room_left_waits_for_the_next_lane(self):
        p = self.plan(200.0)
        self.assertEqual(p["reason"], "wait_next_lane")
        self.assertAlmostEqual(p["wait_s"], 130.0)   # 100 to boundary + 30
        self.assertAlmostEqual(p["start_phase_s"], 30.0)
        self.assertFalse(p["crosses_boundary"])

    def test_just_before_a_boundary_waits_across_it(self):
        p = self.plan(295.0)
        self.assertEqual(p["reason"], "wait_next_lane")
        self.assertAlmostEqual(p["wait_s"], 35.0)     # 5 to boundary + 30
        self.assertAlmostEqual(p["start_phase_s"], 30.0)

    def test_inside_the_pre_boundary_guard_waits(self):
        p = self.plan(285.0, burst=1.0)
        self.assertEqual(p["reason"], "wait_next_lane")

    def test_every_phase_of_the_grid_lands_a_fitting_burst_in_a_clean_lane(self):
        """Exhaustive sweep: no phase may schedule a fitting burst onto a
        boundary. This is the property the sprint is buying."""
        for phase in range(0, int(GRID)):
            p = self.plan(float(phase))
            self.assertGreaterEqual(p["start_phase_s"], POST - 1e-9,
                                    f"phase={phase} starts inside the blackout guard")
            self.assertLessEqual(p["end_phase_s"], GRID - PRE + 1e-9,
                                 f"phase={phase} ends inside the pre-boundary guard")
            self.assertFalse(p["crosses_boundary"], f"phase={phase}")

    # --- no fit at any phase ----------------------------------------------
    def test_burst_longer_than_the_lane_never_parks_the_cycle(self):
        """A 1.5 s-paced image cannot fit ANY lane. Waiting cannot help, and
        parking would burn the bus window -- so go, and say so loudly."""
        p = self.plan(200.0, burst=BURST_15S)
        self.assertEqual(p["reason"], "burst_exceeds_lane")
        self.assertEqual(p["wait_s"], 0.0)
        self.assertFalse(p["fits_lane"])
        self.assertTrue(p["crosses_boundary"])
        self.assertIn("LONGER than the clean lane",
                      tp.describe_plan(p, BURST_15S))

    def test_burst_longer_than_lane_still_clears_the_blackout_guard(self):
        p = self.plan(4.0, burst=BURST_15S)
        self.assertEqual(p["reason"], "burst_exceeds_lane")
        self.assertAlmostEqual(p["wait_s"], 26.0)
        self.assertAlmostEqual(p["start_phase_s"], 30.0)

    def test_wait_is_clamped_so_a_bad_config_cannot_park_a_cycle(self):
        p = self.plan(200.0, max_wait_s=45.0)
        self.assertEqual(p["wait_s"], 45.0)


class TestGridClock(unittest.TestCase):
    """One Spotter read, extrapolated on monotonic time (never time.time())."""

    def test_epoch_extrapolates_from_the_read_instant(self):
        mono = {"t": 500.0}
        gc = tp.GridClock(utc_epoch=at_phase(10.0), mono_at_read=500.0,
                          clock=lambda: mono["t"])
        self.assertAlmostEqual(gc.epoch_now() % GRID, 10.0)
        mono["t"] = 560.0                      # 60 s of cycle elapsed
        self.assertAlmostEqual(gc.epoch_now() % GRID, 70.0)
        self.assertAlmostEqual(gc.age_s(), 60.0)

    def test_plan_from_clock_uses_the_extrapolated_phase(self):
        mono = {"t": 0.0}
        gc = tp.GridClock(utc_epoch=at_phase(0.0), mono_at_read=0.0,
                          clock=lambda: mono["t"])
        mono["t"] = 60.0                       # capture+encode consumed 60 s
        plan = tp.plan_from_clock(gc, BURST_1S)
        self.assertEqual(plan["reason"], "in_lane")
        self.assertAlmostEqual(plan["phase_s"], 60.0)
        self.assertEqual(plan["clock_source"], "spotter")
        self.assertAlmostEqual(plan["clock_age_s"], 60.0)


class TestClockFailureFallback(unittest.TestCase):
    """DESIGN D1 — the branch that fails silently in the field.

    A field unit has no USB and no backend feedback. If the Spotter clock
    read fails we must degrade to EXACTLY the pre-Sprint11 behaviour --
    transmit now, unscheduled -- and say so on the console. A wrong phase is
    no worse than the status quo; silently trusting a bad clock is worse
    than both.
    """

    def test_no_clock_transmits_immediately(self):
        plan = tp.plan_from_clock(None, BURST_1S)
        self.assertEqual(plan["reason"], "no_clock")
        self.assertEqual(plan["wait_s"], 0.0)

    def test_no_clock_never_reports_a_phase_it_does_not_have(self):
        """The dangerous failure is inventing a phase. Every phase field
        must be None, so no downstream code can act on a fabricated value."""
        plan = tp.plan_from_clock(None, BURST_1S)
        for key in ("phase_s", "start_phase_s", "end_phase_s",
                    "fits_lane", "crosses_boundary"):
            self.assertIsNone(plan[key], key)

    def test_no_clock_is_loud_on_the_console(self):
        line = tp.describe_plan(tp.plan_from_clock(None, BURST_1S), BURST_1S)
        self.assertIn("no Spotter clock", line)
        self.assertIn("UNSCHEDULED", line)


class TestConfigIsland(unittest.TestCase):
    def load(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                        encoding="utf-8")
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return tp.load_transmit_phase_config(f.name)

    def test_absent_island_is_disabled(self):
        cfg = self.load("capture_mode: \"progressive_jpeg\"\n")
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["grid_seconds"], GRID)

    def test_missing_file_is_disabled_not_an_exception(self):
        cfg = tp.load_transmit_phase_config("/nonexistent/camera_schedule.yaml")
        self.assertFalse(cfg["enabled"])

    def test_island_values_are_read(self):
        cfg = self.load(
            "transmit_phase:\n"
            "  enabled: true\n"
            "  grid_seconds: 300\n"
            "  post_boundary_guard_s: 35\n"
            "  pre_boundary_guard_s: 25\n"
        )
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["post_boundary_guard_s"], 35.0)
        self.assertEqual(cfg["pre_boundary_guard_s"], 25.0)

    def test_comments_and_other_islands_do_not_leak_in(self):
        cfg = self.load(
            "bm_commands:\n"
            "  enabled: true\n"
            "transmit_phase:\n"
            "  enabled: true      # inline comment\n"
            "media_gid:\n"
            "  enabled: false\n"
        )
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["grid_seconds"], GRID)

    def test_bad_value_falls_back_per_key_without_crashing(self):
        cfg = self.load("transmit_phase:\n"
                        "  enabled: true\n"
                        "  post_boundary_guard_s: banana\n")
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["post_boundary_guard_s"], POST)

    def test_guards_that_eat_the_whole_grid_disable_the_feature(self):
        cfg = self.load("transmit_phase:\n"
                        "  enabled: true\n"
                        "  post_boundary_guard_s: 200\n"
                        "  pre_boundary_guard_s: 150\n")
        self.assertFalse(cfg["enabled"])


if __name__ == "__main__":
    unittest.main()
