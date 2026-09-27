#!/usr/bin/env python3
# filename: test_s3b_soak.py
# description: Sprint26 S3b.8 — 50-action fake-time stay_on soak: one uart, stable threads and fds.
"""
Sprint26 S3b.8 (DESIGN_supervisor.md §8.3 S3b row: "a 50-action fake-time soak
asserting the same uart object and stable thread and descriptor counts").

One stay_on process, fake time, REAL pieces wherever the long-lived risks are:
  - bm_port.open_shared (serial.Serial patched to a fake uart that counts opens),
  - the real CommandDaemon with its real reader thread,
  - the real CommandState on disk (trg recorded, persisted, consumed),
  - the real video action (rc_video_tx.run_video_tx_cycle, supervised) with the
    recorder/encoder faked at function level (tests/test_rc_video_tx_cycle.py),
  - the real stay_on loop (rc_supervisor.run_stay_on): scheduled slots every
    600 s, a trg every 700 s over the uart, pings acked while idle, heartbeats.

Asserts after 50 actions: exactly ONE uart was ever opened and every action
saw that same object as the shared port; the thread count and the open file
descriptor count after action 50 equal those after action 1 (one reader
thread throughout); 50 action-log lines numbered 1..50; heal_events bounded;
the stop closes the port and never halts.

Run (repo root):
  python3 -m unittest tests.test_s3b_soak -v
"""

import json
import os
import queue
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import bm_port  # noqa: E402
import rc_capture  # noqa: E402
import rc_supervisor as sup  # noqa: E402
import rc_video_tx as vtx  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from command_state import CommandState  # noqa: E402
from tests.test_command_daemon import make_cmd_frame  # noqa: E402
from tests.test_rc_video_tx_cycle import VEC, settings as video_settings  # noqa: E402

ACTIONS = 50
TRG_EVERY_S = 700.0
PING_EVERY_S = 170.0


class SoakUart:
    """A fake /dev/ttyAMA0: counts every construction (a second open is a
    second descriptor on the real UART)."""
    opened = []

    def __init__(self, port=None, baudrate=None, timeout=None):
        self.port, self.timeout = port, timeout
        self._rx = queue.Queue()
        self.written = 0
        self.is_open = True
        SoakUart.opened.append(self)

    def inject(self, data):
        self._rx.put(bytes(data))

    def read(self, n):
        try:
            return self._rx.get(timeout=0.01)
        except queue.Empty:
            return b""

    def write(self, data):
        self.written += 1
        return len(data)

    def reset_input_buffer(self):
        pass

    def close(self):
        self.is_open = False


def fd_count():
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))


class Soak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        SoakUart.opened = []
        for p in (mock.patch.object(bm_port.serial, "Serial", SoakUart),
                  mock.patch.object(sup.guard, "current_rss_kb", return_value=50_000),
                  mock.patch.object(sup.guard, "MARKER_PATH",
                                    os.path.join(self.tmp.name, "marker")),
                  mock.patch.object(sup.guard, "SCHED_PATH",
                                    os.path.join(self.tmp.name, "sched"))):
            p.start()
            self.addCleanup(p.stop)
        bm_port._bm, bm_port._shared, bm_port._closed = None, False, False
        self.addCleanup(setattr, bm_port, "_bm", None)
        self.addCleanup(setattr, bm_port, "_shared", False)
        self.addCleanup(setattr, bm_port, "_closed", False)
        sup.STOP.update(requested=False, halting=False, signal=None)
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))
        self.addCleanup(setattr, rc_capture, "stop_check", None)

    def test_fifty_actions_one_uart_stable_threads_and_fds(self):
        t = {"now": 0.0}
        clock = lambda: t["now"]                      # noqa: E731
        state = CommandState(path=os.path.join(self.tmp.name, "state.json"))
        daemons = []
        halts = []
        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True,
                                                source="test"))
        with open(os.path.join(VEC, "payload.h264"), "rb") as fh:
            payload = fh.read()
        base = video_settings(os.path.join(self.tmp.name, "videos"))
        os.makedirs(base["video"]["dir"], exist_ok=True)

        def factory(settings, bm_commands_cfg, st):
            bm = bm_port.open_shared("/dev/ttyAMA0", 115200, timeout=0.1)
            d = CommandDaemon(bm, st, topic="bmcam/cmd")
            daemons.append(d)
            return d

        next_id = {"trg": 1000, "ping": 5000, "at_trg": TRG_EVERY_S, "at_ping": PING_EVERY_S}

        def deliver(cmd):
            uart = SoakUart.opened[0]
            uart.inject(make_cmd_frame(cmd))
            deadline = time.monotonic() + 2.0            # the REAL reader thread parses it
            while daemons[0]._inbound.qsize() == 0 and time.monotonic() < deadline:
                time.sleep(0.001)

        def sleep(s):
            t["now"] += s
            if not SoakUart.opened:
                return
            if t["now"] >= next_id["at_trg"]:
                next_id["at_trg"] += TRG_EVERY_S
                next_id["trg"] += 1
                deliver({"id": next_id["trg"], "c": "trg", "v": 2})
            if t["now"] >= next_id["at_ping"]:
                next_id["at_ping"] += PING_EVERY_S
                next_id["ping"] += 1
                deliver({"id": next_id["ping"], "c": "ping"})

        def record(s, vcfg, video_dir, **kw):
            return {"ok": True, "stage": "done", "bytes": 1,
                    "basename": f"clip_{t['now']:.0f}", "mp4": os.path.join(video_dir, "c.mp4")}

        def fit(src, work, **kw):
            return {"payload": payload, "bytes": len(payload), "msgs": 126,
                    "budget_msgs": kw["budget_msgs"], "used_pct": 99.3, "target_kbps": 55.7,
                    "pass2_tries": 1, "frames_trimmed": 0, "frames": 50, "duration_s": 5.0,
                    "keyframe_end": 2236, "prescale_s": 0.0, "encode_s": 0.0}

        probes = []

        def action(boot, settings):
            summary = vtx.run_video_tx_cycle(
                settings, cfg, supervised=boot, transmit=True,
                gate_fn=lambda path, **kw: (True, {"reason": "soak"}),
                record_fn=record, fit_fn=fit,
                tx_open_fn=lambda path: bm_port.get().spotter_tx,
                bm_close_fn=bm_port.close, halt_fn=lambda **kw: halts.append(kw),
                ensure_room_fn=lambda d, st: {"paused": False}, sleep_fn=sleep, clock=clock,
                encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg",
                bm_commands_cfg={"enabled": True, "topic": "bmcam/cmd"}, command_state=state,
                bench_commands=False)
            probes.append({"uart": bm_port.current().uart, "threads": threading.active_count(),
                           "readers": sum(th.name == "bm-cmd-reader"
                                          for th in threading.enumerate()),
                           "fds": fd_count(), "error": summary.get("error")})
            if len(probes) >= ACTIONS:
                sup.STOP["requested"] = True
            return summary

        boot = sup.Boot(dict(base), media="video",
                        bm_commands_cfg={"enabled": True, "topic": "bmcam/cmd"},
                        command_state=state, transmit=True, bench_commands=False,
                        action_log=os.path.join(self.tmp.name, "cron_logs", "actions.jsonl"))
        beats = []
        with mock.patch("builtins.print"):
            code = sup.run_stay_on(
                boot, action, settings_fn=lambda: dict(base), interval_s=600, heartbeat_s=300,
                heartbeat_fn=lambda s: beats.append(bm_port.get().spotter_tx(b"<WS a=idle>\n")),
                clock=clock, sleep_fn=sleep, halt_fn=lambda **kw: halts.append(kw),
                bm_close_fn=bm_port.close, daemon_factory=factory,
                gate_fn=lambda path, **kw: (True, {"utc_time": "soak"}))

        self.assertEqual(code, 0)
        self.assertEqual(len(probes), ACTIONS)
        self.assertEqual([p["error"] for p in probes], [None] * ACTIONS)
        self.assertEqual(len(SoakUart.opened), 1, "a second uart was opened")
        the_uart = SoakUart.opened[0]
        self.assertTrue(all(p["uart"] is the_uart for p in probes), "the shared port changed")
        self.assertEqual({p["readers"] for p in probes}, {1})
        self.assertEqual(probes[-1]["threads"], probes[0]["threads"])
        self.assertEqual(probes[-1]["fds"], probes[0]["fds"])
        self.assertEqual(len(daemons), 1)
        self.assertLessEqual(len(daemons[0].heal_events), 8)
        self.assertFalse(the_uart.is_open)                 # stop: port closed ...
        self.assertEqual(halts, [])                        # ... and never a halt
        with open(boot.action_log, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual([r["action"] for r in lines], list(range(1, ACTIONS + 1)))
        kinds = {r["kind"] for r in lines}
        self.assertEqual(kinds, {"trg", "scheduled"})      # both decision paths exercised
        self.assertGreater(daemons[0].stats["acks_sent"], ACTIONS)   # trg + ping acks
        self.assertGreater(len(beats), 0)                  # idle gaps had heartbeats


if __name__ == "__main__":
    unittest.main()
