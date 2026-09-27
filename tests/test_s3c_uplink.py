#!/usr/bin/env python3
# filename: test_s3c_uplink.py
# description: Sprint26 S3c.2 — an action that sent nothing is not an uplink (stay_on timers), output on Boot.
"""
Sprint26 S3c.2 (PLAN_S3c.md §5 C1, C2, C6, C17).

Pins, with the S3b fake loop (fake daemon, clock and action):
  - a save_local action that sent nothing (summary "uplinked": False) moves
    neither the heartbeat timer nor the O5 idle-heal timer: a unit saving every
    60 s still beats every heartbeat_s and still sends pending heals after
    IDLE_HEAL_S (the review blocker: before S3c every action reset both);
  - an action with "uplinked": True, or without the key (every transmitting
    action), resets them exactly as in S3b;
  - configure_output puts output, save quality and the ONE storage limit pair
    (video.storage.*) on the Boot, from the v2 values or the registry defaults;
  - the heartbeat reads its reason at send time (storage_full);
  - the action log records the resolved output.

Run (repo root):
  python3 -m unittest tests.test_s3c_uplink -v
"""

import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_progressive_jpeg as rc  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_s3a_supervisor import SETTINGS  # noqa: E402
from tests.test_s3b_stay_on import Loop  # noqa: E402


class SaveLocalTimers(Loop):
    def saving_action(self, duration=5.0, uplinked=False):
        inner = self.action(duration=duration)

        def fn(boot, settings):
            s = inner(boot, settings)
            if uplinked is not None:
                s["uplinked"] = uplinked
            return s
        return fn

    def test_heartbeats_continue_while_saving_every_minute(self):
        self.loop(self.saving_action(), interval_s=60, heartbeat_s=300, stop_at=1000)
        self.assertGreaterEqual(len(self.actions), 16)
        self.assertEqual([round(b) for b in self.beats], [300, 600, 900])

    def test_s3b_behaviour_kept_for_actions_that_sent(self):
        for uplinked in (True, None):              # None: the key is absent (transmit)
            self.beats.clear()
            self.actions.clear()
            self.loop(self.saving_action(uplinked=uplinked), interval_s=60,
                      heartbeat_s=300, stop_at=1000)
            self.assertEqual(self.beats, [], uplinked)

    def test_idle_heals_still_go_out_while_saving(self):
        self.state.pending_heals = [{"key": "0dpr00"}]
        passes = []

        def fake_heal_pass(boot, daemon, settings, tx_open_fn, clock, sleep_fn):
            passes.append(round(clock()))
            boot.summary = {"command_events": [], "stage": "done"}
            return boot.summary
        with mock.patch.object(sup, "heal_pass", fake_heal_pass):
            self.loop(self.saving_action(), interval_s=60, heartbeat_s=0, stop_at=1300,
                      heal_tx_open_fn=lambda path: object())
        self.assertEqual(len(passes), 2)
        self.assertAlmostEqual(passes[0], 600, delta=6)
        self.assertAlmostEqual(passes[1], 1200, delta=12)

    def test_the_action_log_records_the_output(self):
        def fn(boot, settings):
            boot.output = "save_local"
            return self.saving_action()(boot, settings)
        self.loop(fn, interval_s=60, stop_at=100)
        self.assertEqual({line["output"] for line in self.log_lines()}, {"save_local"})


class ConfigureOutput(unittest.TestCase):
    class Values:
        def __init__(self, values):
            self.values = values

    def test_from_v2_values(self):
        boot = sup.Boot(dict(SETTINGS), media="still", bm_commands_cfg={}, command_state=None,
                        transmit=True, bench_commands=False)
        rc.configure_output(boot, self.Values({
            "still.save.quality": 70, "video.storage.max_used_pct": 50.0,
            "video.storage.min_free_gb": 1.5, "video.storage.ring_dry_run": True}), "save_local")
        self.assertEqual((boot.output, boot.save_quality), ("save_local", 70))
        self.assertEqual(boot.storage_cfg, {"max_used_pct": 50.0, "min_free_gb": 1.5,
                                            "ring_dry_run": True})
        self.assertNotIn("output", boot.settings)       # C2: never in settings

    def test_registry_defaults_without_a_v2_file(self):
        boot = sup.Boot(dict(SETTINGS), media="still", bm_commands_cfg={}, command_state=None,
                        transmit=True, bench_commands=False)
        rc.configure_output(boot, None, "transmit")
        self.assertEqual((boot.output, boot.save_quality), ("transmit", 85))
        self.assertEqual(boot.storage_cfg, {"max_used_pct": 75.0, "min_free_gb": 10.0,
                                            "ring_dry_run": False})


class HeartbeatReason(unittest.TestCase):
    def test_reason_is_read_at_send_time(self):
        state = {"reason": None}
        send = rc._heartbeat_fn(lambda s: "1000x562", lambda s: 90,
                                reason_fn=lambda: state["reason"])
        settings = {"timezone": "UTC", "window_start": "10:00", "window_end": "15:00"}
        with mock.patch("rc_telemetry.send_wake_status") as wake:
            send(settings)
            state["reason"] = "storage_full"
            send(settings)
        self.assertEqual([c.kwargs["reason"] for c in wake.call_args_list],
                         [None, "storage_full"])
        self.assertEqual({c.kwargs["action"] for c in wake.call_args_list}, {"idle"})


if __name__ == "__main__":
    unittest.main()
