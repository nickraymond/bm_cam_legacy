#!/usr/bin/env python3
# filename: test_s3c_save_local_still.py
# description: Sprint26 S3c.4 — still x save_local: what the goldens cannot reach (time-read failures, heal slot gating).
"""
Sprint26 S3c.4 (PLAN_S3c.md J2, J7 + §5 C9, C13, C14). The wire and files of
still x save_local are pinned by the goldens (vectors_save_local/,
vectors_stay_on/stay_on_save_local_still); this file pins what they cannot:

  - a Spotter time read that FAILS inside the gate: the still is saved anyway
    on the Pi clock (sidecar time_source "system"), the window not enforced;
  - Boot.save_local_time_read (window disabled): no daemon / a failed read ->
    "system" (no clock step); a good read steps the clock on the process's first
    read and later only on drift >= CLOCK_STEP_MIN_DRIFT_S (W6 rule);
  - the save_local heal slot runs only with --transmit, a daemon, and pending
    heals or heal events; a failing slot never costs the save;
  - the saved pair is written atomically (no temp file left) and the sidecar
    says output save_local; no sent record, no camera_log row.

Run (repo root):
  python3 -m unittest tests.test_s3c_save_local_still -v
"""

import datetime as dt
import json
import os
import shutil
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_progressive_jpeg as rc  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_s3a_supervisor import Base, quiet  # noqa: E402

NATIVE = os.path.join(REPO_ROOT, "reference_images", "prepared", "P7071008",
                      "synthetic_native_4608x2592.jpg")
STILL = {"budget_seconds": 600, "pacing_delay_seconds": 1.3, "pacing_chunk_b64_chars": 384,
         "config_path": "/nonexistent.yaml", "enforce_time_window": True,
         "timezone": "UTC", "window_start": "00:00", "window_end": "23:59",
         "crop_native_xywh": (1504, 846, 1600, 900), "output_width": 1000,
         "output_size": (1000, 562), "q_max": 90, "power_halt_enabled": False,
         "power_halt_dry_run": True, "power_halt_mode": "halt",
         "power_halt_script_path": "/nonexistent_halt.sh"}


class StillSaveLocal(Base):
    def setUp(self):
        super().setUp()
        self.images = os.path.join(self.tmp.name, "images")
        os.makedirs(self.images)

    def capture(self, settings, output_dir):
        stem = "2026-09-24T15:00:00Z_image"
        dest = os.path.join(output_dir, f"{stem}_native_full.jpg")
        shutil.copyfile(NATIVE, dest)
        return dest, {}, stem

    def run_action(self, gate_result, *, run="per_boot"):
        boot = self.boot(settings=STILL)
        boot.output, boot.save_quality, boot.run = "save_local", 85, run
        boot.storage_cfg = None                       # the guard is S3c.3's test
        wakes = []
        summary = {"command_events": []}
        with mock.patch.object(rc, "should_transmit_now_from_schedule",
                               lambda path, **kw: gate_result), \
                mock.patch.object(rc, "collect_storage_health", lambda: {}):
            quiet(rc.still_action, dict(STILL), summary, None, mock.Mock(elapsed_s=lambda: 1.0),
                  transmit=True, capture_only=False, native_path=None,
                  skip_time_window=False, output_dir=self.images,
                  capture_fn=self.capture, bm_open_fn=None,
                  wake_fn=lambda **kw: wakes.append(kw["action"]),
                  sleep_fn=self.clock.sleep, clock=self.clock, bm_commands_cfg={},
                  bench_commands=False, grid_clock_fn=None, supervised=boot)
        return summary, wakes

    def sidecar(self):
        path = os.path.join(self.images, "2026-09-24T15:00:00Z_image_compressed.jpg"
                                         ".capture_metadata.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_failed_spotter_read_saves_on_the_pi_clock(self):
        gate = (False, {"source_time": "system", "spotter_time_error": "timeout",
                        "reason": "Spotter time unavailable and fallback disabled: timeout"})
        summary, wakes = self.run_action(gate)
        self.assertEqual(summary["stage"], "saved")
        self.assertTrue(summary["schedule_allowed"])
        self.assertEqual(summary["saved"]["time_source"], "system")
        self.assertEqual(self.sidecar()["time_source"], "system")
        self.assertEqual(wakes, ["cap"])                 # per_boot: the one status line

    def test_spotter_time_and_the_saved_pair(self):
        summary, _ = self.run_action((True, {"source_time": "spotter", "reason": "ok"}))
        meta = self.sidecar()
        self.assertEqual((meta["output"], meta["time_source"], meta["jpeg_quality_used"]),
                         ("save_local", "spotter", 85))
        self.assertEqual(sorted(os.listdir(self.images)), [
            "2026-09-24T15:00:00Z_image_compressed.jpg",
            "2026-09-24T15:00:00Z_image_compressed.jpg.capture_metadata.json",
            "2026-09-24T15:00:00Z_image_native_full.jpg"])      # no temp file left
        self.assertFalse(summary["uplinked"] is None)
        self.assertNotIn("transmit_result", summary)

    def test_stay_on_sends_no_per_action_status(self):
        summary, wakes = self.run_action((True, {"source_time": "spotter"}), run="stay_on")
        self.assertEqual(wakes, [])
        self.assertIs(summary["uplinked"], False)

    def test_outside_the_window_on_spotter_time_is_a_skip(self):
        summary, wakes = self.run_action((False, {"source_time": "spotter", "reason": "out"}))
        self.assertIs(summary["schedule_allowed"], False)
        self.assertEqual(wakes, ["skip_win"])
        self.assertEqual(os.listdir(self.images), [])


class FakeDaemon:
    def __init__(self, utc=None, exc=None, pending=(), events=()):
        self.utc, self.exc = utc, exc
        self.state = mock.Mock(pending_heals=list(pending))
        self.heal_events = list(events)

    def wait_for_spotter_utc(self, timeout):
        if self.exc:
            raise self.exc
        return self.utc


class TimeRead(Base):
    def setUp(self):
        super().setUp()
        cfg = mock.Mock(time_source="spotter_utc", spotter_time_timeout_seconds=5,
                        set_system_clock_from_spotter=True)
        p = mock.patch("spotter_time_sync.load_camera_schedule", return_value=cfg)
        p.start()
        self.addCleanup(p.stop)
        self.steps = []
        p = mock.patch("spotter_time_sync.set_system_clock_utc", self.steps.append)
        p.start()
        self.addCleanup(p.stop)

    def test_no_daemon_or_failed_read_is_system_without_a_step(self):
        boot = self.boot()
        self.assertEqual(quiet(boot.save_local_time_read, None, STILL), "system")
        self.assertEqual(quiet(boot.save_local_time_read, FakeDaemon(exc=TimeoutError("t")),
                               STILL), "system")
        self.assertEqual(self.steps, [])

    def test_first_read_steps_later_only_on_drift(self):
        boot = self.boot()
        boot.gate_reads = 1          # a window-off gate "read" nothing (review S3c #5)
        now = dt.datetime.now(dt.timezone.utc)
        self.assertEqual(quiet(boot.save_local_time_read, FakeDaemon(utc=now), STILL), "spotter")
        quiet(boot.save_local_time_read, FakeDaemon(utc=now), STILL)          # no drift
        quiet(boot.save_local_time_read, FakeDaemon(utc=now + dt.timedelta(seconds=30)), STILL)
        self.assertEqual(len(self.steps), 2)
        self.assertEqual(boot.save_time_reads, 3)

    def test_clock_stepping_off_in_config_is_honoured(self):
        cfg = mock.Mock(time_source="spotter_utc", spotter_time_timeout_seconds=5,
                        set_system_clock_from_spotter=False)
        with mock.patch("spotter_time_sync.load_camera_schedule", return_value=cfg):
            got = quiet(self.boot().save_local_time_read,
                        FakeDaemon(utc=dt.datetime.now(dt.timezone.utc)), STILL)
        self.assertEqual((got, self.steps), ("spotter", []))


class HealSlot(Base):
    def call(self, daemon, transmit=True, send=None):
        send = send or mock.Mock(return_value=3)
        with mock.patch.object(sup, "send_pending_heals", send):
            got = quiet(sup.save_local_heals, daemon, STILL, {}, mock.Mock(), transmit=transmit,
                        tx_open_fn=None, clock=self.clock, sleep_fn=self.clock.sleep)
        return got, send

    def test_per_boot_only_and_only_when_something_was_planned(self):
        # Review S3c #4/#7: stay_on leaves heals to the O5 idle pass; a slot
        # that planned nothing is not an uplink.
        send = mock.Mock(return_value=3)
        with mock.patch.object(sup, "send_pending_heals", send):
            got = quiet(sup.save_local_heals, FakeDaemon(pending=[{"key": "k"}]), STILL, {},
                        mock.Mock(), transmit=True, tx_open_fn=None, clock=self.clock,
                        sleep_fn=self.clock.sleep, run="stay_on")
        self.assertFalse(got)
        send.assert_not_called()
        self.assertFalse(self.call(FakeDaemon(pending=[{"key": "k"}]),
                                   send=mock.Mock(return_value=0))[0])

    def test_runs_only_with_work_a_daemon_and_transmit(self):
        self.assertEqual(self.call(None)[0], False)
        got, send = self.call(FakeDaemon())
        self.assertFalse(got)
        send.assert_not_called()
        self.assertFalse(self.call(FakeDaemon(pending=[{"key": "k"}]), transmit=False)[0])
        self.assertTrue(self.call(FakeDaemon(pending=[{"key": "k"}]))[0])
        self.assertTrue(self.call(FakeDaemon(events=[{"key": "k"}]))[0])

    def test_a_failing_slot_never_raises(self):
        got, _ = self.call(FakeDaemon(pending=[{"key": "k"}]),
                           send=mock.Mock(side_effect=OSError("uart")))
        self.assertTrue(got)


if __name__ == "__main__":
    unittest.main()
