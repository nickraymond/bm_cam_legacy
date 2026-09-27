#!/usr/bin/env python3
# filename: test_s3b_stay_on.py
# description: Sprint26 S3b.3 — the stay_on loop rules (PLAN_S3b.md H2-H6), fake time.
"""
Sprint26 S3b.3 (DESIGN_supervisor.md §4 stay_on; PLAN_S3b.md H2-H6).

Pins, with a fake daemon, a fake clock and a fake action:
  - trigger-only (interval_s 0): no action at boot; a trg fires one action;
  - scheduled: first action at boot, next at start + interval_s; an overrun
    runs the next one at once (missed slots are not caught up);
  - every action gets fresh settings from settings_fn (a one-shot trg key
    never leaks into the next action);
  - heartbeat heartbeat_s after the last uplink; 0 = off; a quiet window skip
    is not an uplink; an action is;
  - quiet_skip is set only for a scheduled skip that follows a skip;
  - a trg whose consume cannot be persisted fires once, not in a loop;
  - an action that raises is logged and the loop goes on;
  - SIGTERM (a real signal) stops the loop: shutdown -> close, never a halt;
    per_boot never installs the handler;
  - no daemon -> EXIT_ARGS; a UART failure at start -> EXIT_CRASH, closed, no halt;
  - the action log carries run/action/kind and rotates at ACTION_LOG_MAX_LINES;
  - post_transmit_listen does nothing when supervised.listen_tail is False;
  - resolve_run_mode: stay_on only from a v2 file and under the supervisor.

Run (repo root):
  python3 -m unittest tests.test_s3b_stay_on -v
"""

import json
import os
import signal
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_command_hooks as cmd_hooks  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_s3a_supervisor import SETTINGS, Base, quiet  # noqa: E402


class FakeState:
    def __init__(self):
        self.pending_trigger = None
        self.persist_ok = True

    def consume(self):
        if self.persist_ok:
            self.pending_trigger = None


class FakeDaemon:
    """What the loop and PortOwner.finish touch. `script` = [(t, fn)]: fn runs
    on the first process_pending at or after fake time t (a command arriving)."""

    def __init__(self, clock, state, script=()):
        self.clock, self.state = clock, state
        self.script = sorted(script, key=lambda x: x[0])
        self.fresh_time_reads = False
        self.pending_acks = 0
        self.stopped = False

    def start(self):
        pass

    def process_pending(self):
        while self.script and self.clock.t >= self.script[0][0]:
            self.script.pop(0)[1]()
        return []

    def drain_acks(self, max_n=None, clock=None):
        return 0

    def drain_console(self, max_lines=None, sleep_fn=None):
        return 0

    def stop(self, join_timeout=2.0):
        self.stopped = True


class Loop(Base):
    def setUp(self):
        super().setUp()
        self.state = FakeState()
        self.actions = []            # (t, kind-ish settings marker)
        self.beats = []
        self.settings_made = 0
        sup.STOP.update(requested=False, halting=False, signal=None)
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))
        # S3b.4: the loop checks RSS after every action; off-device that reads
        # the test process's PEAK, which a full suite run pushes past the ceiling.
        patcher = mock.patch.object(sup.guard, "current_rss_kb", return_value=50_000)
        patcher.start()
        self.addCleanup(patcher.stop)
        marker = mock.patch.object(sup.guard, "MARKER_PATH",
                                   os.path.join(self.tmp.name, "stay_on.marker"))
        marker.start()
        self.addCleanup(marker.stop)

    def stay_boot(self, transmit=True):
        return sup.Boot(dict(SETTINGS), media="still", bm_commands_cfg={"enabled": True},
                        command_state=self.state, transmit=transmit, bench_commands=False,
                        action_log=self.log)

    def settings_fn(self):
        self.settings_made += 1
        return dict(SETTINGS, made=self.settings_made)

    def action(self, duration=100.0, skip=False, raise_at=None):
        def fn(boot, settings):
            n = len(self.actions) + 1
            boot.start({"command_events": []}, clock=self.clock, sleep_fn=self.clock.sleep,
                       halt_fn=self.rec.halt, bm_close_fn=self.rec.close,
                       daemon_factory=None, log_fn=lambda *a: None, close_warn=print,
                       end_line=None)
            self.actions.append({"t": self.clock.t, "made": settings["made"],
                                 "quiet": boot.quiet_skip,
                                 "trg": self.state.pending_trigger is not None})
            self.state.consume()
            self.clock.t += duration
            if raise_at == n:
                raise RuntimeError("camera gone")
            s = boot.summary
            s["schedule_allowed"] = not (skip(n) if callable(skip) else skip)
            return s
        return fn

    def loop(self, action, *, interval_s=0, heartbeat_s=0, stop_at=5000.0, script=(),
            daemon=True):
        orig_sleep = self.clock.sleep

        def sleep(s):
            orig_sleep(s)
            if self.clock.t >= stop_at:
                sup.STOP["requested"] = True
        self.clock.sleep = sleep
        d = FakeDaemon(self.clock, self.state, script)
        boot = self.stay_boot(transmit=daemon)      # no --transmit: no daemon

        def act(b, settings):
            try:
                return action(b, settings)
            finally:                    # back-to-back actions never reach a sleep
                if self.clock.t >= stop_at:
                    sup.STOP["requested"] = True
        code = quiet(sup.run_stay_on, boot, act, settings_fn=self.settings_fn,
                     interval_s=interval_s, heartbeat_s=heartbeat_s,
                     heartbeat_fn=lambda s: self.beats.append(self.clock.t),
                     clock=self.clock, sleep_fn=sleep, halt_fn=self.rec.halt,
                     bm_close_fn=self.rec.close,
                     daemon_factory=lambda s, c, st: d,
                     gate_fn=lambda path, **kw: (True, {"utc_time": "t"}))
        return code, boot, d

    def trg_at(self, t, tid=1):
        return (t, lambda: setattr(self.state, "pending_trigger", {"id": tid, "value": 2}))


class TriggerAndSchedule(Loop):
    def test_trigger_only_waits_then_fires_once(self):
        code, boot, d = self.loop(self.action(), script=[self.trg_at(300)], stop_at=1000)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.actions), 1)
        self.assertAlmostEqual(self.actions[0]["t"], 300.0, delta=0.21)
        self.assertTrue(self.actions[0]["trg"])
        self.assertEqual(self.rec.calls, ["close"])     # stop: close, never a halt
        self.assertTrue(d.stopped)
        self.assertEqual(boot.summary["halt_result"]["action"], "none")

    def test_scheduled_from_boot_start_to_start(self):
        self.loop(self.action(duration=100), interval_s=600, stop_at=1900)
        self.assertEqual([round(a["t"]) for a in self.actions], [0, 600, 1200, 1800])

    def test_overrun_runs_the_next_at_once_not_caught_up(self):
        self.loop(self.action(duration=1500), interval_s=600, stop_at=3100)
        ts = [round(a["t"]) for a in self.actions]
        self.assertEqual(ts, [0, 1500, 3000])

    def test_fresh_settings_per_action(self):
        self.loop(self.action(), script=[self.trg_at(10, 1), self.trg_at(500, 2)], stop_at=900)
        made = [a["made"] for a in self.actions]
        self.assertEqual(len(made), 2)
        self.assertEqual(len(set(made)), 2)             # never the same dict twice

    def test_stuck_trigger_fires_once(self):
        self.state.persist_ok = False
        self.loop(self.action(duration=10), script=[self.trg_at(5)], stop_at=500)
        self.assertEqual(len(self.actions), 1)

    def test_action_exception_is_logged_and_the_loop_goes_on(self):
        self.loop(self.action(raise_at=1), interval_s=600, stop_at=700)
        self.assertEqual(len(self.actions), 2)
        lines = self.log_lines()
        self.assertIn("camera gone", lines[0]["error"])
        self.assertIsNone(lines[1]["error"])


class Heartbeat(Loop):
    def test_after_heartbeat_s_since_the_last_uplink(self):
        self.loop(self.action(duration=100), script=[self.trg_at(1000)], heartbeat_s=300,
                 stop_at=1500)
        # boot 0 -> beats 300, 600, 900; trg at 1000 (ends 1100) -> 1400
        self.assertEqual([round(b) for b in self.beats], [300, 600, 900, 1400])

    def test_off(self):
        self.loop(self.action(), heartbeat_s=0, stop_at=2000)
        self.assertEqual(self.beats, [])

    def test_quiet_skip_is_not_an_uplink_and_first_skip_is(self):
        self.loop(self.action(duration=0, skip=True), interval_s=600, heartbeat_s=300,
                 stop_at=1300)
        self.assertEqual([a["quiet"] for a in self.actions], [False, True, True])
        # first skip at 0 is an uplink; quiet skips at 600/1200 are not
        self.assertEqual([round(b) for b in self.beats], [300, 600, 900, 1200])

    def test_a_sent_action_ends_the_skip_run(self):
        self.loop(self.action(duration=0, skip=lambda n: n != 2), interval_s=600,
                 stop_at=1900)
        self.assertEqual([a["quiet"] for a in self.actions], [False, True, False, True])


class StopAndFailures(Loop):
    def test_real_sigterm_stops_without_halt(self):
        script = [(50, lambda: os.kill(os.getpid(), signal.SIGTERM))]
        code, boot, d = self.loop(self.action(), interval_s=600, script=script, stop_at=1e9)
        self.assertEqual(code, 0)
        self.assertEqual(sup.STOP["signal"], signal.SIGTERM)
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(self.rec.calls, ["close"])
        self.assertLess(self.clock.t, 200)             # stopped within a tick of the flag

    def test_sigterm_ignored_once_halting(self):
        with mock.patch("builtins.print"):
            sup.install_stop_flag()
        sup.STOP["halting"] = True
        os.kill(os.getpid(), signal.SIGTERM)
        self.assertFalse(sup.stop_requested())

    def test_per_boot_never_installs_the_handler(self):
        before = signal.getsignal(signal.SIGTERM)
        boot = self.boot()
        quiet(sup.run_per_boot, boot, lambda b: {})
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_no_daemon_is_exit_args_without_halt(self):
        code, _boot, _d = self.loop(self.action(), daemon=False, stop_at=10)
        self.assertEqual(code, sup.EXIT_ARGS)
        self.assertNotIn("halt", self.rec.calls)
        self.assertEqual(self.actions, [])

    def test_uart_failure_is_exit_crash_closed_no_halt(self):
        boot = self.stay_boot()
        code = quiet(sup.run_stay_on, boot, self.action(), settings_fn=self.settings_fn,
                     interval_s=0, heartbeat_s=0, heartbeat_fn=None, clock=self.clock,
                     sleep_fn=self.clock.sleep, halt_fn=self.rec.halt,
                     bm_close_fn=self.rec.close, daemon_factory=self.fakes()["daemon_factory"],
                     gate_fn=lambda path, **kw: (True, {}))
        self.assertEqual(code, sup.EXIT_CRASH)
        self.assertEqual(self.rec.calls, ["daemon_start", "close"])


class ActionLog(Loop):
    def test_lines_carry_run_action_kind(self):
        self.loop(self.action(), interval_s=600, script=[self.trg_at(100)], stop_at=700)
        lines = self.log_lines()
        self.assertEqual([(r["run"], r["action"], r["kind"]) for r in lines],
                         [("stay_on", 1, "scheduled"), ("stay_on", 2, "trg"),
                          ("stay_on", 3, "scheduled")])

    def test_rotates(self):
        with mock.patch.object(sup, "ACTION_LOG_MAX_LINES", 3):
            self.loop(self.action(duration=0), interval_s=60, stop_at=60 * 4 + 1)
        with open(self.log + ".1", encoding="utf-8") as fh:
            self.assertEqual(len(fh.readlines()), 3)
        self.assertEqual(len(self.log_lines()), 2)


class TailAndRunMode(unittest.TestCase):
    def test_no_listen_tail_in_stay_on(self):
        class Sup:
            listen_tail = False
        daemon = mock.Mock()
        got = quiet(cmd_hooks.post_transmit_listen, daemon, {"post_transmit_listen_s": 150},
                    {"command_events": []}, mock.Mock(), supervised=Sup())
        self.assertEqual(got, 0.0)
        daemon.listen_window.assert_not_called()

    def test_resolve_run_mode(self):
        class B:
            def __init__(self, values):
                self.values = values
        on = {"mode.run": "stay_on", "mode.interval_s": 600, "mode.heartbeat_s": 300}
        self.assertEqual(rc.resolve_run_mode(B(on), "supervisor"), ("stay_on", 600, 300))
        self.assertEqual(quiet(rc.resolve_run_mode, B(on), "legacy"), ("per_boot", 0, 0))
        self.assertEqual(rc.resolve_run_mode(None, "supervisor"), ("per_boot", 0, 0))
        self.assertEqual(rc.resolve_run_mode(B({"mode.run": "per_boot"}), "supervisor"),
                         ("per_boot", 0, 0))


if __name__ == "__main__":
    unittest.main()
