#!/usr/bin/env python3
# filename: test_s3a_supervisor.py
# description: Sprint26 S3a.5 — the per_boot supervisor: always shutdown -> close -> halt, one action log line.
"""
Sprint26 S3a.5 (PLAN_S3a.md S3a.5, G1, G5). The wire parity of the supervisor
is pinned by tests/test_golden_vectors.py (every scenario under both
runtimes); this file pins what the goldens cannot see:

  - G5: a UART failure at daemon start still closes the port and halts under
    the supervisor (stills and video); legacy stills keeps its old behaviour
    (no close, no halt) — the one intended non-wire difference;
  - an action that fails before it ever reaches the boot still halts;
  - G1: the budget is anchored before the daemon starts;
  - one action-log line per action; a log failure never costs the halt.

Run (repo root):
  python3 -m unittest tests.test_s3a_supervisor -v
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
    stub = types.ModuleType("serial")
    stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = stub

import bm_port  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402
import rc_supervisor  # noqa: E402
import rc_video_tx as vtx  # noqa: E402

SETTINGS = {"budget_seconds": 600, "pacing_delay_seconds": 1.3, "config_path": "/nonexistent.yaml",
            "power_halt_enabled": True, "power_halt_dry_run": True, "power_halt_mode": "halt",
            "power_halt_script_path": "/nonexistent_halt.sh"}
CMDS = {"enabled": True}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class BrokenDaemon:
    def __init__(self, calls):
        self.calls = calls

    def start(self):
        self.calls.append("daemon_start")
        raise OSError("uart gone")


class Recorder:
    def __init__(self):
        self.calls = []

    def close(self):
        self.calls.append("close")

    def halt(self, **kw):
        self.calls.append("halt")
        return {"action": "dry_run"}


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class Base(unittest.TestCase):
    def setUp(self):
        bm_port._bm, bm_port._closed = None, False
        self.addCleanup(setattr, bm_port, "_closed", False)
        self.addCleanup(setattr, bm_port, "_bm", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "cron_logs", "supervisor_actions.jsonl")
        self.rec = Recorder()
        self.clock = Clock()

    def boot(self, media="still", settings=SETTINGS):
        return rc_supervisor.Boot(dict(settings), media=media, bm_commands_cfg=CMDS,
                                  command_state=object(), transmit=True, bench_commands=False,
                                  action_log=self.log)

    def fakes(self):
        return dict(bm_close_fn=self.rec.close, halt_fn=self.rec.halt, clock=self.clock,
                    sleep_fn=self.clock.sleep,
                    daemon_factory=lambda s, c, st: BrokenDaemon(self.rec.calls))

    def log_lines(self):
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]


class DaemonFailureG5(Base):
    def test_supervisor_stills_closes_and_halts(self):
        sup = self.boot()
        with self.assertRaises(OSError):
            quiet(rc_supervisor.run_per_boot, sup,
                  lambda b: rc.run_cycle(dict(SETTINGS), transmit=True, bm_commands_cfg=CMDS,
                                         command_state=object(), supervised=b, **self.fakes()))
        self.assertEqual(self.rec.calls, ["daemon_start", "close", "halt"])
        self.assertIn("uart gone", self.log_lines()[0]["error"])

    def test_legacy_stills_keeps_skipping_close_and_halt(self):
        with self.assertRaises(OSError):
            quiet(rc.run_cycle, dict(SETTINGS), transmit=True, bm_commands_cfg=CMDS,
                  command_state=object(), **self.fakes())
        self.assertEqual(self.rec.calls, ["daemon_start"])

    def test_supervisor_video_closes_and_halts(self):
        sup = self.boot(media="video")
        summary = quiet(rc_supervisor.run_per_boot, sup,
                        lambda b: vtx.run_video_tx_cycle(
                            dict(SETTINGS), {}, transmit=True, bm_commands_cfg=CMDS,
                            command_state=object(), supervised=b, **self.fakes()))
        self.assertIn("uart gone", summary["error"])
        self.assertEqual(summary["stage"], "daemon_start")
        self.assertEqual(self.rec.calls, ["daemon_start", "close", "halt"])
        self.assertEqual(summary["halt_result"], {"action": "dry_run"})


class OtherPaths(Base):
    def test_action_failing_before_the_boot_still_halts(self):
        sup = self.boot()
        with self.assertRaises(KeyError):
            quiet(rc_supervisor.run_per_boot, sup, lambda b: {}["boom"])
        # production defaults: bm_port.close + perform_power_halt(dry_run=True)
        self.assertEqual(sup.summary["halt_result"]["action"], "dry_run")
        self.assertTrue(bm_port._closed)

    def test_budget_is_anchored_before_the_daemon(self):
        order = []

        class D:
            def start(self_inner):
                order.append(("daemon_start", self.clock()))
                self.clock.t += 3.0            # subscribe + reader start take time

        sup = self.boot()
        self.clock.t = 5.0
        daemon, budget = quiet(sup.start, {}, clock=self.clock, sleep_fn=self.clock.sleep,
                               halt_fn=self.rec.halt, bm_close_fn=self.rec.close,
                               daemon_factory=lambda s, c, st: D(), log_fn=print,
                               close_warn=print, end_line=lambda: "end")
        self.clock.t = 10.0
        self.assertEqual(round(budget.elapsed_s(), 3), 5.0)     # anchored at 5.0, before the daemon
        self.assertEqual(order, [("daemon_start", 5.0)])

    def test_log_failure_never_costs_the_halt(self):
        sup = self.boot()
        sup.action_log = "/dev/null/not_a_dir/actions.jsonl"
        out = quiet(rc_supervisor.run_per_boot, sup, lambda b: {"stage": "done"})
        self.assertEqual(out, {"stage": "done"})
        self.assertEqual(sup.summary["halt_result"]["action"], "dry_run")

    def test_one_log_line_per_action(self):
        sup = self.boot()
        quiet(rc_supervisor.run_per_boot, sup, lambda b: None)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual((lines[0]["runtime"], lines[0]["run"], lines[0]["media"]),
                         ("supervisor", "per_boot", "still"))


class BootDrainW4(Base):
    """W4: what is queued at boot applies this boot, and a trg that just
    arrived is serviced AFTER the drain (it fires this boot)."""

    def test_drain_then_reresolve_then_trigger(self):
        order = []

        class D:
            def start(self_inner):
                pass

            def process_pending(self_inner):
                order.append("process_pending")
                return [{"action": "applied"}]

        sup = rc_supervisor.Boot(dict(SETTINGS), media="still", bm_commands_cfg=CMDS,
                                 command_state=object(), transmit=True, bench_commands=False,
                                 action_log=self.log,
                                 reresolve_fn=lambda s: order.append("reresolve") or dict(s, roi=2))
        quiet(sup.start, {}, clock=self.clock, sleep_fn=lambda s: order.append(f"sleep{s}"),
              halt_fn=self.rec.halt, bm_close_fn=self.rec.close,
              daemon_factory=lambda s, c, st: D(), log_fn=print, close_warn=print,
              end_line=lambda: "end")
        summary = {"command_events": []}

        def fake_trigger(settings, state, transmit):
            order.append("trigger")
            return dict(settings, trigger={"id": 9}), {"skip_time_window": True,
                                                       "capture_only": False}

        orig = rc_supervisor.cmd_hooks.service_pending_trigger
        rc_supervisor.cmd_hooks.service_pending_trigger = fake_trigger
        try:
            settings, flags = quiet(sup.boot_drain, dict(SETTINGS), summary,
                                    lambda s: order.append(f"sleep{s}"))
        finally:
            rc_supervisor.cmd_hooks.service_pending_trigger = orig
        self.assertEqual(order, ["sleep0.0", "process_pending", "reresolve", "trigger"])
        self.assertEqual((settings["roi"], settings["trigger"]), (2, {"id": 9}))
        self.assertTrue(flags["skip_time_window"])
        self.assertEqual(summary["command_events"], ["applied"])
        self.assertEqual(summary["trigger"], {"id": 9})

    def test_nothing_queued_keeps_settings(self):
        class D:
            def start(self_inner):
                pass

            def process_pending(self_inner):
                return []

        calls = []
        state = types.SimpleNamespace(pending_trigger=None, consume_trigger=lambda: None)
        sup = rc_supervisor.Boot(dict(SETTINGS), media="video", bm_commands_cfg=CMDS,
                                 command_state=state, transmit=True, bench_commands=False,
                                 action_log=self.log, reresolve_fn=calls.append)
        quiet(sup.start, {}, clock=self.clock, sleep_fn=self.clock.sleep, halt_fn=self.rec.halt,
              bm_close_fn=self.rec.close, daemon_factory=lambda s, c, st: D(), log_fn=print,
              close_warn=print, end_line=lambda: "end")
        before = dict(SETTINGS)
        settings, flags = quiet(sup.boot_drain, before, {"command_events": []}, self.clock.sleep)
        self.assertIs(settings, before)
        self.assertEqual(calls, [])               # nothing applied, no trg pending
        self.assertEqual(flags, {"skip_time_window": False, "capture_only": False})


class BootDrainFollowUps(Base):
    """Review fix: what was built from the pre-drain settings follows a drained
    txd/win/hlt (budget limits with the SAME start, halt settings, summary);
    a failed re-resolve keeps the pre-drain settings."""

    def boot_with(self, reresolve_fn):
        class D:
            def start(self_inner):
                pass

            def process_pending(self_inner):
                return [{"action": "applied"}]

        state = types.SimpleNamespace(pending_trigger=None, consume_trigger=lambda: None)
        sup = rc_supervisor.Boot(dict(SETTINGS), media="still", bm_commands_cfg=CMDS,
                                 command_state=state, transmit=True, bench_commands=False,
                                 action_log=self.log, reresolve_fn=reresolve_fn)
        self.clock.t = 10.0
        _d, budget = quiet(sup.start, {}, clock=self.clock, sleep_fn=self.clock.sleep,
                           halt_fn=self.rec.halt, bm_close_fn=self.rec.close,
                           daemon_factory=lambda s, c, st: D(), log_fn=print,
                           close_warn=print, end_line=lambda: "end")
        return sup, budget

    def test_drained_txd_win_hlt_reach_budget_owner_and_summary(self):
        sup, budget = self.boot_with(lambda s: dict(s, budget_seconds=300,
                                                    pacing_delay_seconds=5.0,
                                                    power_halt_dry_run=False))
        summary = {"command_events": [], "budget_seconds": 600}
        self.clock.t = 40.0
        quiet(sup.boot_drain, dict(SETTINGS), summary, self.clock.sleep)
        self.assertEqual((budget.budget_seconds, budget.seconds_per_message), (300.0, 5.0))
        self.assertEqual(budget.elapsed_s(), 30.0)             # same start: not re-anchored
        self.assertEqual(summary["budget_seconds"], 300)
        self.assertFalse(sup.owner.settings["power_halt_dry_run"])

    def test_failed_reresolve_keeps_the_pre_drain_settings(self):
        def boom(_s):
            raise ValueError("bad override")
        sup, budget = self.boot_with(boom)
        before = dict(SETTINGS)
        settings, _flags = quiet(sup.boot_drain, before, {"command_events": []},
                                 self.clock.sleep)
        self.assertIs(settings, before)
        self.assertEqual(budget.budget_seconds, 600.0)


class VideoTriggerW5(Base):
    """W5: a video unit services trg; trg 1 = record to SD, send nothing."""

    def run_video(self, flags):
        from rc_time_budget import CycleBudget
        from tests.test_rc_video_tx_cycle import settings as vsettings
        tmp = tempfile.mkdtemp(dir=self.tmp.name)
        calls, wire = [], []
        clk = self.clock

        class FakeBoot:
            def start(self_inner, summary, **kw):
                return None, CycleBudget(600, 1.0, clock=clk)

            def gate_kwargs(self_inner, daemon, settings):
                return {}

            def boot_drain(self_inner, settings, summary, sleep_fn):
                return settings, flags

        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True,
                                                source="test"))
        summary = quiet(
            vtx.run_video_tx_cycle, vsettings(tmp, enforce_time_window=True), cfg, transmit=True,
            gate_fn=lambda path, **kw: calls.append("gate") or (False, {"reason": "outside"}),
            record_fn=lambda s, v, d, **kw: calls.append("record") or {
                "ok": True, "stage": "done", "bytes": 1, "mp4": os.path.join(d, "c.mp4"),
                "basename": "2026-09-21T07-30-05Z_video_1920x1080_15fps"},
            fit_fn=lambda *a, **kw: calls.append("fit"), tx_open_fn=lambda p: wire.append,
            ensure_room_fn=lambda d, st: {"paused": False}, sleep_fn=clk.sleep, clock=clk,
            encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg",
            halt_fn=self.rec.halt, bm_close_fn=self.rec.close, supervised=FakeBoot())
        return summary, calls, wire

    def test_trg1_records_and_sends_nothing_even_outside_the_window(self):
        summary, calls, wire = self.run_video({"skip_time_window": True, "capture_only": True})
        self.assertEqual(calls, ["gate", "record"])
        self.assertEqual((summary["stage"], wire), ("done_capture_only", []))
        self.assertEqual(self.rec.calls, [])      # the supervisor halts, not the cycle

    def test_trg2_bypasses_the_window(self):
        summary, calls, _wire = self.run_video({"skip_time_window": True, "capture_only": False})
        self.assertEqual(calls[:3], ["gate", "record", "fit"])   # not stopped at the window


if __name__ == "__main__":
    unittest.main()
