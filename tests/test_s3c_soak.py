#!/usr/bin/env python3
# filename: test_s3c_soak.py
# description: Sprint26 S3c.8 — save_local soak: 50 stay_on actions with a growing fake SD stay bounded.
"""
Sprint26 S3c.8 (PLAN_S3c.md §1 S3c.8; the Mac-side twin of the bmcam003 gate
"stay_on x save_local 1 h, SD bounded").

1. Video, REAL pieces: one stay_on x save_local process (rc_supervisor.run_stay_on)
   runs 50 real video actions (rc_video_tx.run_video_tx_cycle, supervised) with
   the recorder faked at function level, the REAL ring (video_ring.ensure_room)
   and the REAL command daemon on a fake uart. The fake SD's usage = a fixed
   base + every byte in the video dir, so every clip really fills it.
   Asserts: all 50 actions saved (none refused), SD usage after every action
   <= the cap + one clip, the clip count bounded, ONE uart ever opened, no
   START/chunk on the wire, heartbeats in the idle gaps (S3c C1), no halt.

2. Stills, the guard over time: 50 save_local stems (native + crop + sidecar),
   each preceded by rc_still_storage.ensure_room on the same kind of fake SD,
   with one transmitted stem whose JPEG a live sent record names (a heal
   payload). Asserts: usage bounded after every save, the heal payload survives
   all 50 prunes, the oldest stems go first.

Run (repo root):
  python3 -m unittest tests.test_s3c_soak -v
"""

import collections
import contextlib
import io
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import bm_port  # noqa: E402
import rc_capture  # noqa: E402
import rc_still_storage  # noqa: E402
import rc_supervisor as sup  # noqa: E402
import rc_video_tx as vtx  # noqa: E402
import video_ring  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from command_state import CommandState  # noqa: E402
from tests.test_rc_video_tx_cycle import settings as video_settings  # noqa: E402
from tests.test_s3b_soak import SoakUart, fd_count  # noqa: E402

ACTIONS = 50
Usage = collections.namedtuple("Usage", "total used free")
TOTAL, BASE_USED = 100_000, 60_000          # bytes; cap 75 % = 15 000 B above the base
CLIP_BYTES = 1_000
LIMITS = {"max_used_pct": 75.0, "min_free_gb": 0.0, "ring_dry_run": False}


def dir_bytes(*dirs):
    total = 0
    for d in dirs:
        for root, _dirs, files in os.walk(d):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    return total


def fake_disk(*dirs):
    def usage(_path):
        used = BASE_USED + dir_bytes(*dirs)
        return Usage(TOTAL, used, TOTAL - used)
    return usage


class WireUart(SoakUart):
    """SoakUart that keeps what was written, to prove nothing media went out."""

    def write(self, data):
        self.bytes_out = getattr(self, "bytes_out", b"") + bytes(data)
        return super().write(data)


class VideoSoak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        SoakUart.opened = []
        videos = os.path.join(self.tmp.name, "videos")
        os.makedirs(videos)
        self.videos = videos
        for p in (mock.patch.object(bm_port.serial, "Serial", WireUart),
                  mock.patch.object(sup.guard, "current_rss_kb", return_value=50_000),
                  mock.patch.object(sup.guard, "MARKER_PATH", os.path.join(self.tmp.name, "m")),
                  mock.patch.object(sup.guard, "SCHED_PATH", os.path.join(self.tmp.name, "s")),
                  mock.patch.object(rc_still_storage, "DISK_USAGE_FN", fake_disk(videos))):
            p.start()
            self.addCleanup(p.stop)
        bm_port._bm, bm_port._shared, bm_port._closed = None, False, False
        self.addCleanup(setattr, bm_port, "_bm", None)
        self.addCleanup(setattr, bm_port, "_shared", False)
        self.addCleanup(setattr, bm_port, "_closed", False)
        sup.STOP.update(requested=False, halting=False, signal=None)
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))
        self.addCleanup(setattr, rc_capture, "stop_check", None)

    def test_fifty_saved_clips_keep_the_sd_bounded(self):
        t = {"now": 0.0}
        clock = lambda: t["now"]                      # noqa: E731
        state = CommandState(path=os.path.join(self.tmp.name, "state.json"))
        halts, daemons, probes, beats = [], [], [], []
        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True,
                                                source="test"))
        base = video_settings(self.videos)
        base["video"]["storage"] = dict(LIMITS)
        # The window on: the (fake) gate is the time read. With it off a save_local
        # action reads Spotter time itself (S3c C9), which this fake uart never answers.
        base["enforce_time_window"] = True
        disk = fake_disk(self.videos)

        def factory(settings, bm_commands_cfg, st):
            d = CommandDaemon(bm_port.open_shared("/dev/ttyAMA0", 115200, timeout=0.1), st,
                              topic="bmcam/cmd")
            daemons.append(d)
            return d

        def record(s, vcfg, video_dir, **kw):
            base_name = f"2026-09-24T{int(t['now']):08d}Z_video_480x270_10fps"
            mp4 = os.path.join(video_dir, base_name + ".mp4")
            with open(mp4, "wb") as fh:
                fh.write(b"\0" * CLIP_BYTES)
            return {"ok": True, "stage": "done", "bytes": CLIP_BYTES, "basename": base_name,
                    "mp4": mp4}

        def action(boot, settings):
            summary = vtx.run_video_tx_cycle(
                settings, cfg, supervised=boot, transmit=True,
                gate_fn=lambda path, **kw: (True, {"reason": "soak", "source_time": "spotter"}),
                record_fn=record, fit_fn=mock.Mock(side_effect=AssertionError("no fit")),
                tx_open_fn=lambda path: bm_port.get().spotter_tx,
                bm_close_fn=bm_port.close, halt_fn=lambda **kw: halts.append(kw),
                ensure_room_fn=lambda d, st: video_ring.ensure_room(
                    d, st, disk_usage_fn=disk, log_fn=lambda *a: None),
                sleep_fn=lambda s: t.__setitem__("now", t["now"] + s), clock=clock,
                encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg",
                bm_commands_cfg={"enabled": True, "topic": "bmcam/cmd"}, command_state=state,
                bench_commands=False)
            u = disk(None)
            probes.append({"stage": summary.get("stage"), "used_pct": 100.0 * u.used / u.total,
                           "clips": len(video_ring.completed_clip_triples(self.videos)),
                           "uart": bm_port.current().uart, "fds": fd_count()})
            if len(probes) >= ACTIONS:
                sup.STOP["requested"] = True
            return summary

        boot = sup.Boot(dict(base), media="video",
                        bm_commands_cfg={"enabled": True, "topic": "bmcam/cmd"},
                        command_state=state, transmit=True, bench_commands=False,
                        action_log=os.path.join(self.tmp.name, "cron_logs", "actions.jsonl"))
        boot.output, boot.storage_cfg = "save_local", dict(LIMITS)
        started = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            code = sup.run_stay_on(
                boot, action, settings_fn=lambda: json.loads(json.dumps(base)),
                interval_s=600, heartbeat_s=300,
                heartbeat_fn=lambda s: beats.append(bm_port.get().spotter_tx(b"<WS a=idle>\n")),
                clock=clock, sleep_fn=lambda s: t.__setitem__("now", t["now"] + s),
                halt_fn=lambda **kw: halts.append(kw), bm_close_fn=bm_port.close,
                daemon_factory=factory, gate_fn=lambda path, **kw: (True, {"utc_time": "soak"}))
        self.assertLess(time.monotonic() - started, 60.0)

        self.assertEqual(code, 0)
        self.assertEqual([p["stage"] for p in probes], ["saved"] * ACTIONS)
        clip_pct = 100.0 * (CLIP_BYTES + 1_000) / TOTAL     # a clip + its sidecar/manifest
        self.assertTrue(all(p["used_pct"] <= LIMITS["max_used_pct"] + clip_pct for p in probes),
                        [round(p["used_pct"], 1) for p in probes])
        self.assertLessEqual(max(p["clips"] for p in probes), 16)
        self.assertEqual(len(SoakUart.opened), 1, "a second uart was opened")
        self.assertTrue(all(p["uart"] is SoakUart.opened[0] for p in probes))
        self.assertEqual(probes[-1]["fds"], probes[0]["fds"])
        wire = SoakUart.opened[0].bytes_out
        self.assertNotIn(b"<START", wire)
        self.assertNotIn(b"<I", wire.replace(b"<WS", b""))
        # Every idle gap between two 600 s slots holds heartbeats (S3c C1).
        self.assertGreaterEqual(len(beats), ACTIONS - 1)
        self.assertEqual(halts, [])


class StillsGuardSoak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.images = os.path.join(self.tmp.name, "images")
        self.sent = os.path.join(self.tmp.name, "sent")
        os.makedirs(self.images)
        os.makedirs(self.sent)

    def write(self, name, data):
        with open(os.path.join(self.images, name), "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)

    def stem(self, stem, output):
        self.write(f"{stem}_native_full.jpg", b"\0" * 900)
        self.write(f"{stem}_native_full.metadata.json", "{}")
        self.write(f"{stem}_compressed.jpg", b"\0" * 100)
        self.write(f"{stem}_compressed.jpg.capture_metadata.json", json.dumps({"output": output}))

    def test_fifty_saves_bounded_and_the_heal_payload_survives(self):
        heal = "2026-09-01T00:00:00Z_image"
        self.stem(heal, "transmit")
        with open(os.path.join(self.sent, f"{heal}_compressed.sent.json"), "w") as fh:
            json.dump({"payload": os.path.join(self.images, f"{heal}_compressed.jpg")}, fh)
        disk = fake_disk(self.images)
        pcts = []
        for n in range(ACTIONS):
            r = rc_still_storage.ensure_room(self.images, LIMITS, sent_dir=self.sent,
                                             retain_days=14, disk_usage_fn=disk,
                                             log_fn=lambda *a: None)
            self.assertFalse(r["full"], n)
            self.stem(f"2026-09-24T{n:08d}Z_image", "save_local")
            u = disk(None)
            pcts.append(100.0 * u.used / u.total)
        stem_pct = 100.0 * 1_100 / TOTAL
        self.assertLessEqual(max(pcts), LIMITS["max_used_pct"] + stem_pct + 0.1)
        self.assertTrue(os.path.exists(os.path.join(self.images, f"{heal}_compressed.jpg")))
        left = sorted(n for n in os.listdir(self.images) if n.endswith("_compressed.jpg"))
        self.assertEqual(left[-1], f"2026-09-24T{ACTIONS - 1:08d}Z_image_compressed.jpg")
        self.assertNotIn("2026-09-24T00000000Z_image_compressed.jpg", left)   # oldest went


if __name__ == "__main__":
    unittest.main()
