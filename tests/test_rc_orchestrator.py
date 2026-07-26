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
               budget_override_yaml=None):
        config_path = write_yaml(budget_override_yaml or rc_yaml(yaml_extra))
        self.addCleanup(os.unlink, config_path)
        out_dir = tempfile.mkdtemp()
        settings = rc.resolve_rc_settings(config_path)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
