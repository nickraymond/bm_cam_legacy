#!/usr/bin/env python3
# filename: test_rc_orchestrator.py
# description: Sprint08 P7 — off-device dry-run tests for M7 (rc_progressive_jpeg.run_cycle).
"""
Sprint08 P7 — off-device orchestrator tests.

Runs the REAL M1->M6 wiring end-to-end on the committed coral native
(compress-only path — no camera), with injected fakes for everything
hardware-adjacent: tx (recorded), sleep (advances a fake clock by the pacing
delay), wake/halt (recorded), BM open/close (fake). Asserts the cycle-level
contracts: one budget charged throughout, correct message sequence, bounded
path on forced no-fit, halt always last, artifacts on disk.

Slow-ish (~15 s): two real native prepares + real encodes. Run:
  python3 -m unittest tests.test_rc_orchestrator -v
  # or: python3 tests/test_rc_orchestrator.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile
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

import rc_progressive_jpeg as rc  # noqa: E402
import rc_transmit_phase as tp  # noqa: E402

CORAL_NATIVE = os.path.join(
    REPO_ROOT, "reference_images", "prepared", "P7071008", "synthetic_native_4608x2592.jpg"
)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class Recorder:
    """Records calls; usable as tx / wake / halt / bm hooks."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def rc_yaml(extra=""):
    return (
        "capture_mode: \"progressive_jpeg\"\n"
        "enforce_time_window: false\n"
        + extra
    )


def write_yaml(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return f.name


class OrchestratorHarness(unittest.TestCase):
    """Shared machinery: run a cycle on the committed coral native with fakes."""

    def run_rc(self, yaml_extra="", transmit=False, capture_only=False,
               budget_override_yaml=None, grid_clock_fn=None,
               settings_override=None):
        config_path = write_yaml(budget_override_yaml or rc_yaml(yaml_extra))
        self.addCleanup(os.unlink, config_path)
        out_dir = tempfile.mkdtemp()
        settings = rc.resolve_rc_settings(config_path)
        # bm_serial pacing comes from PyYAML, which dev Macs may not have
        # (the Pi does). Override in resolved settings so pacing-sensitive
        # tests do not silently run at the 5.0 s default.
        if settings_override:
            settings.update(settings_override)

        clock = FakeClock()

        def pacing_sleep(seconds):
            clock.advance(seconds)

        tx_messages = []

        def tx(payload):
            tx_messages.append(payload.decode("ascii"))

        wake = Recorder()
        halt = Recorder(result={"action": "recorded"})
        bm_close = Recorder()

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            summary = rc.run_cycle(
                settings,
                transmit=transmit,
                capture_only=capture_only,
                native_path=CORAL_NATIVE,
                skip_time_window=True,
                output_dir=out_dir,
                bm_open_fn=lambda cfg: tx,
                bm_close_fn=bm_close,
                wake_fn=wake,
                halt_fn=lambda **kw: (halt(**kw) or {"action": "recorded", **kw}),
                sleep_fn=pacing_sleep,
                clock=clock,
                **({"grid_clock_fn": grid_clock_fn}
                   if grid_clock_fn is not None else {}),
            )
        return types.SimpleNamespace(
            summary=summary, tx=tx_messages, wake=wake, halt=halt,
            bm_close=bm_close, clock=clock, out_dir=out_dir,
            stdout=stdout.getvalue(), settings=settings,
        )


class TestNoSerialCycle(OrchestratorHarness):
    def test_plan_only_cycle_touches_no_bus(self):
        r = self.run_rc(transmit=False)
        self.assertEqual(r.tx, [])            # nothing sent
        self.assertEqual(r.wake.calls, [])    # no heartbeat
        self.assertEqual(r.bm_close.calls, [])
        self.assertIn("send plan (NO transmit)", r.stdout)
        # Coral primary fits at q15 with a fresh 18-min budget (S07: 84 msgs).
        self.assertTrue(r.summary["selection"]["fits"])
        self.assertEqual(r.summary["selection"]["quality"], 15)
        self.assertEqual(r.summary["selection"]["attempts"], 1)

    def test_artifacts_written(self):
        r = self.run_rc(transmit=False)
        final = r.summary["final_path"]
        self.assertTrue(os.path.exists(final))
        self.assertGreater(os.path.getsize(final), 10000)
        sidecar = final + ".capture_metadata.json"
        self.assertTrue(os.path.exists(sidecar))
        with open(sidecar) as f:
            meta = json.load(f)
        self.assertEqual(meta["img_format"], "pjpg")
        self.assertEqual(meta["jpeg_quality_used"], 15)
        self.assertEqual(meta["enc_attempts"], 1)
        self.assertEqual(meta["capture_mode"], "progressive_jpeg")

    def test_halt_called_last_with_config_values(self):
        r = self.run_rc(transmit=False)
        self.assertEqual(len(r.halt.calls), 1)
        _, kwargs = r.halt.calls[0]
        self.assertEqual(kwargs["enabled"], False)
        self.assertEqual(kwargs["dry_run"], True)
        self.assertEqual(kwargs["mode"], "halt")
        self.assertEqual(r.summary["halt_result"]["action"], "recorded")

    def test_capture_only_stops_before_encode(self):
        r = self.run_rc(capture_only=True)
        self.assertIsNone(r.summary["selection"])
        self.assertIsNone(r.summary["final_path"])
        self.assertEqual(r.tx, [])
        self.assertEqual(len(r.halt.calls), 1)  # halt still runs in finally


class TestTransmitCycle(OrchestratorHarness):
    def test_complete_transmit_sequence(self):
        r = self.run_rc(transmit=True)
        result = r.summary["transmit_result"]
        self.assertTrue(result["complete_send"])
        self.assertEqual(result["sent"], result["planned"])
        # Wire order: START, chunks, END; heartbeat went via wake_fn not tx.
        self.assertTrue(r.tx[0].startswith("<START IMG> "))
        self.assertIn("fmt=pjpg", r.tx[0])
        self.assertIn("cmp=1", r.tx[0])
        self.assertIn("rk=1000x562", r.tx[0])
        for m in r.tx[1:-1]:
            self.assertTrue(m.startswith("<I"))
        self.assertTrue(r.tx[-1].startswith("<END IMG> "))
        self.assertIn(f"sent_buffers: {result['sent']}", r.tx[-1])
        self.assertEqual(len(r.tx), 1 + result["planned"] + 1)
        # Production wake heartbeat emitted once (action=cap).
        self.assertEqual(len(r.wake.calls), 1)
        self.assertEqual(r.wake.calls[0][1]["action"], "cap")
        # Serial closed in finally.
        self.assertEqual(len(r.bm_close.calls), 1)

    def test_one_budget_spans_prepare_encode_and_transmit(self):
        r = self.run_rc(transmit=True)
        result = r.summary["transmit_result"]
        # Fake sleeps advanced the shared clock: uart duration reflects pacing
        # of START + chunks (one budget, no resets).
        expected_uart = (1 + result["sent"]) * r.settings["pacing_delay_seconds"]
        self.assertAlmostEqual(result["uart_duration_sec"], expected_uart)

    def test_forced_no_fit_runs_bounded_path(self):
        # 2-minute budget: 24 paced slots. Coral q9 floor needs ~55 chunks ->
        # no-fit; bounded send of max_messages_now-3 chunks.
        r = self.run_rc(budget_override_yaml=(
            "capture_mode: \"progressive_jpeg\"\n"
            "enforce_time_window: false\n"
            "progressive_jpeg:\n"
            "  max_run_time_min: 2\n"
        ), transmit=True)
        sel = r.summary["selection"]
        self.assertFalse(sel["fits"])
        self.assertEqual(sel["quality"], 9)       # walked to the floor
        self.assertEqual(sel["attempts"], 4)
        result = r.summary["transmit_result"]
        self.assertTrue(result["incomplete_emitted"])
        self.assertLess(result["sent"], result["planned"])
        # a=inc FIRST, then START announcing PLANNED with cmp=0.
        self.assertIn("a=inc", r.tx[0])
        self.assertIn("rsn=budget", r.tx[0])
        self.assertTrue(r.tx[1].startswith("<START IMG> "))
        self.assertIn(f"length: {result['planned']}", r.tx[1])
        self.assertIn("cmp=0", r.tx[1])
        self.assertTrue(r.tx[-1].startswith("<END IMG> "))
        self.assertIn(f"sent_buffers: {result['sent']}", r.tx[-1])
        # Halt still last.
        self.assertEqual(len(r.halt.calls), 1)


# 1.0 s pacing is what makes the whole scheme work: the coral primary is
# 84 chunks, so the burst is ~85 s against a 250 s lane. At the 5.0 s
# default it would be 425 s and could not fit any lane (DESIGN D3).
PACING_1S = {"pacing_delay_seconds": 1.0}

PHASE_YAML = (
    "transmit_phase:\n"
    "  enabled: true\n"
    "  grid_seconds: 300\n"
    "  post_boundary_guard_s: 30\n"
    "  pre_boundary_guard_s: 20\n"
)

# A real measured 5-minute boundary (2026-07-29T18:20:00Z), divisible by 300.
BOUNDARY = 1785349200.0


def clock_at_phase(phase_s):
    """grid_clock_fn that pins the cycle to `phase_s` past a boundary.

    Returns a factory matching run_cycle's grid_clock_fn signature. The
    GridClock is anchored to the cycle clock at the moment it is asked for,
    so the phase is exactly what the test names.
    """
    def factory(gate_info, gate_mono, daemon=None, clock=None):
        return tp.GridClock(BOUNDARY + phase_s, clock(), clock=clock)
    return factory


class TestPhaseAwareTransmit(OrchestratorHarness):
    """Sprint11 C2 wired into the real cycle (fake clock, no hardware)."""

    def transmit_start_phase(self, r):
        """Phase at which the first message actually left, from the fake
        clock: the sleeps the cycle performed are the wait we are testing."""
        plan = r.summary["transmit_phase"]
        return plan["start_phase_s"]

    def test_island_off_by_default_means_no_phase_wait(self):
        r = self.run_rc(transmit=True)
        self.assertNotIn("transmit_phase", r.summary)
        self.assertNotIn("[PHASE]", r.stdout)

    def test_mid_lane_transmits_with_no_wait(self):
        r = self.run_rc(yaml_extra=PHASE_YAML, transmit=True,
                        settings_override=PACING_1S, grid_clock_fn=clock_at_phase(40.0))
        plan = r.summary["transmit_phase"]
        self.assertEqual(plan["reason"], "in_lane")
        self.assertEqual(plan["wait_s"], 0.0)
        self.assertFalse(plan["crosses_boundary"])

    def test_on_a_boundary_the_cycle_actually_sleeps_the_guard(self):
        """The wait must be real: it has to show up in the shared clock,
        because the same clock is the transmit budget."""
        before = self.run_rc(yaml_extra=PHASE_YAML, transmit=True,
                             settings_override=PACING_1S, grid_clock_fn=clock_at_phase(40.0))
        on_boundary = self.run_rc(yaml_extra=PHASE_YAML, transmit=True,
                                  settings_override=PACING_1S, grid_clock_fn=clock_at_phase(0.0))
        plan = on_boundary.summary["transmit_phase"]
        self.assertEqual(plan["reason"], "wait_post_guard")
        self.assertAlmostEqual(plan["wait_s"], 30.0)
        # 30 s of extra elapsed time vs the no-wait run.
        self.assertAlmostEqual(
            on_boundary.clock.now - before.clock.now, 30.0, places=3)

    def test_late_in_the_lane_defers_to_the_next_lane(self):
        r = self.run_rc(yaml_extra=PHASE_YAML, transmit=True,
                        settings_override=PACING_1S, grid_clock_fn=clock_at_phase(250.0))
        plan = r.summary["transmit_phase"]
        self.assertEqual(plan["reason"], "wait_next_lane")
        self.assertAlmostEqual(plan["wait_s"], 80.0)      # 50 + 30
        self.assertAlmostEqual(self.transmit_start_phase(r), 30.0)
        # And the image still went out complete after the wait.
        self.assertTrue(r.summary["transmit_result"]["complete_send"])

    def test_clock_read_failure_falls_back_to_unscheduled_transmit(self):
        """DESIGN D1 — the silent-failure path. No clock must mean 'send
        now, exactly like before', never 'guess a phase'."""
        r = self.run_rc(yaml_extra=PHASE_YAML, transmit=True,
                        settings_override=PACING_1S, grid_clock_fn=lambda *a, **k: None)
        plan = r.summary["transmit_phase"]
        self.assertEqual(plan["reason"], "no_clock")
        self.assertEqual(plan["wait_s"], 0.0)
        self.assertIsNone(plan["phase_s"])
        self.assertIn("no Spotter clock", r.stdout)
        self.assertIn("UNSCHEDULED", r.stdout)
        # The cycle is otherwise completely normal.
        self.assertTrue(r.summary["transmit_result"]["complete_send"])

    def test_wait_is_skipped_when_it_would_starve_the_burst(self):
        """A wait spends the same budget the transmit needs. Waiting into a
        budget too small for the burst would truncate the image mid-send —
        strictly worse than sending at a bad phase."""
        r = self.run_rc(budget_override_yaml=(
            "capture_mode: \"progressive_jpeg\"\n"
            "enforce_time_window: false\n"
            + PHASE_YAML +
            "progressive_jpeg:\n"
            "  max_run_time_min: 2\n"
        ), transmit=True, settings_override=PACING_1S,
           grid_clock_fn=clock_at_phase(250.0))
        plan = r.summary["transmit_phase"]
        self.assertEqual(plan["reason"], "skipped_no_budget")
        self.assertEqual(plan["wait_s"], 0.0)
        self.assertIn("skipping the", r.stdout)

    def test_print_config_flags_a_pacing_that_cannot_fit_a_lane(self):
        """The D3 config rule, caught on the bench instead of from the gap
        pattern in an overnight run."""
        config_path = write_yaml(rc_yaml(
            "transmit_phase:\n"
            "  enabled: true\n"
            "bm_serial:\n"
            "  image_transmit_delay_seconds: 1.5\n"
            "progressive_jpeg:\n"
            "  message_cap: 195\n"
        ))
        self.addCleanup(os.unlink, config_path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc.print_resolved_settings(rc.resolve_rc_settings(config_path))
        self.assertIn("DOES NOT FIT", out.getvalue())
        self.assertIn("exceeds the clean lane", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
