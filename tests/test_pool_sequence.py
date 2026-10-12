#!/usr/bin/env python3
# filename: test_pool_sequence.py
# description: Pool test 2026-10-13 — preview wire round trip, command parsing/de-dup, phases, auto-start, guards, effective config, pool_mode.sh.
"""
Fake camera + fake link (no hardware, no UART). Pins the frozen preview wire
format (the nereus000 reassembler codes against it), the command contract
(OK/ERR id=N, de-dup by id), the auto-start fallback, the storage guards, the
read-only effective config, and the reversible crontab swap.

Run (repo root):  .venv-dev/bin/python -m pytest tests/test_pool_sequence.py -q
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_wire  # noqa: E402
import pool_sequence as P  # noqa: E402
import rc_preview as V  # noqa: E402

HOST = "bmcam004"


def noisy_image(w=1600, h=900, seed=1):
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), (10, 70, 90))
    d = ImageDraw.Draw(img)
    for _ in range(400):
        x, y = rnd.randrange(w), rnd.randrange(h)
        d.ellipse([x, y, x + rnd.randrange(5, 80), y + rnd.randrange(5, 80)],
                  fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    return img


class PreviewWire(unittest.TestCase):
    def setUp(self):
        self.jpeg, self.q, self.attempts = V.render_preview_jpeg(noisy_image())
        self.lines = V.build_lines(HOST, HOST, "143005", self.jpeg, 640, 360, self.q)

    def test_progressive_and_size_target(self):
        self.assertIn(b"\xff\xc2", self.jpeg[:2000], "SOF2 = progressive JPEG")
        self.assertLessEqual(len(self.jpeg), V.DEFAULT_MAX_BYTES)
        self.assertEqual(self.attempts[-1]["q"], self.q)

    def test_header_data_end_shape(self):
        n = len(self.lines) - 2
        h = self.lines[0].split(" ")
        self.assertEqual(h[:3], ["PVH", HOST, "143005"])
        self.assertIn(f"n={n}", h)
        self.assertIn(f"bytes={len(self.jpeg)}", h)
        self.assertIn("w=640", h)
        self.assertIn(f"q={self.q}", h)
        first = self.lines[1].split(" ")
        self.assertEqual(first[:4], ["PV", HOST, "143005", f"1/{n}"])
        self.assertEqual(first[4], V.crc8hex(first[5]))
        self.assertEqual(len(first[5]), V.DEFAULT_CHUNK_CHARS)
        self.assertEqual(self.lines[-1], f"PVE {HOST} 143005")

    def test_every_console_line_fits(self):
        for text in self.lines:
            line = command_wire.console_line(HOST, text)
            self.assertLessEqual(len(line), command_wire.MAX_CONSOLE_CHARS)
            self.assertFalse(line.endswith("..."))

    def test_round_trip(self):
        out = V.reassemble([command_wire.console_line(HOST, t) for t in self.lines])
        self.assertEqual(out["jpeg"], self.jpeg)
        self.assertTrue(out["sha_ok"])

    def test_dropped_and_corrupt_lines_then_resend(self):
        got = [t for i, t in enumerate(self.lines) if i not in (2, 5)]
        corrupt = self.lines[7].split(" ")
        corrupt[5] = ("A" if corrupt[5][0] != "A" else "B") + corrupt[5][1:]
        got[got.index(self.lines[7])] = " ".join(corrupt)
        out = V.reassemble(got)
        self.assertIsNone(out["jpeg"])
        self.assertEqual(out["missing"], [2, 5, 7])
        self.assertEqual(out["bad"], [7])
        seqs = V.parse_line_spec("2,5,7", len(self.lines) - 2)
        again = V.reassemble(got + V.resend_lines(self.lines, seqs))
        self.assertEqual(again["jpeg"], self.jpeg)

    def test_chunk_too_long_refused(self):
        limit = V.max_chunk_chars(HOST, HOST, "143005", 999)
        self.assertGreaterEqual(limit, 180)
        with self.assertRaises(ValueError):
            V.build_lines(HOST, HOST, "143005", self.jpeg, 640, 360, self.q, chunk_chars=limit + 8)

    def test_line_spec(self):
        self.assertEqual(V.parse_line_spec("3-5,1,4", 10), [1, 3, 4, 5])
        for bad in ("0", "11", "5-3", "a", "1,,2", "1 2", ""):
            with self.assertRaises(ValueError, msg=bad):
                V.parse_line_spec(bad, 10)

    def test_img_id_collision(self):
        now = datetime(2026, 10, 13, 18, 30, 5, tzinfo=timezone.utc)
        self.assertEqual(V.new_img_id(set(), now), "183005")
        self.assertEqual(V.new_img_id({"183005"}, now), "183005b")

    def test_expected_seconds(self):
        self.assertEqual(V.expected_seconds(250, 0.031), 7.8)


class ParseCommand(unittest.TestCase):
    def p(self, obj):
        return P.parse_command(json.dumps(obj, separators=(",", ":")).encode())

    def test_valid(self):
        self.assertEqual(self.p({"id": 7, "c": "pool", "v": "cal", "duration_s": 120,
                                 "interval_s": 10}),
                         (7, "cal", {"duration_s": 120, "interval_s": 10}))
        self.assertEqual(self.p({"id": 8, "c": "pool", "v": "preview", "lines": "3-7,12"})[2],
                         {"lines": "3-7,12"})

    def test_rejections(self):
        cases = [
            ({"id": 1, "c": "get", "k": ["x"]}, "only c=pool"),
            ({"id": 1, "c": "pool", "v": "zap"}, "unknown verb"),
            ({"id": 1, "c": "pool", "v": "cal", "duration_s": 5}, "integer 10..7200"),
            ({"id": 1, "c": "pool", "v": "cal", "speed": 5}, "key speed"),
            ({"id": 1, "c": "pool", "v": "mark", "note": "card 2m"}, "use _ for spaces"),
            ({"id": 1, "c": "pool", "v": "mark"}, "needs note"),
            ({"id": 1, "c": "pool", "v": "loop", "iterations": True}, "integer"),
            ({"id": 1, "c": "pool", "v": "mark", "note": "x" * 240}, "B>248B"),
        ]
        for obj, why in cases:
            with self.assertRaises(P.CmdError, msg=obj) as cm:
                self.p(obj)
            self.assertEqual(cm.exception.cid, 1)
            self.assertIn(why, cm.exception.reason)

    def test_unanswerable(self):
        for raw in (b"{nope", b'{"c":"pool"}', b'{"id":true,"c":"pool"}', b'{"id":1,"id":2}'):
            with self.assertRaises(P.CmdError) as cm:
                P.parse_command(raw)
            self.assertIsNone(cm.exception.cid)


def cmd(cid, verb, **kw):
    return dict(id=cid, c="pool", v=verb, **kw)


class Harness:
    def __init__(self, case, script, argv=(), drop=None, camera=None, free=None):
        self.dir = tempfile.mkdtemp(prefix="pool_")
        case.addCleanup(shutil.rmtree, self.dir, True)
        self.args = P.build_parser().parse_args(
            ["--preview-line-s", "0", "--min-free-mb", "10"] + list(argv))
        self.args.preview_wh = (self.args.preview_w, self.args.preview_h)
        self.link = P.FakeLink(script, drop=drop)
        self.camera = camera or P.FakeCamera(capture_s=0.01, video_s=0.01)
        settings = {"crop_native_xywh": (1504, 846, 1600, 900), "output_width": 1600,
                    "quality_ladder": [85, 70, 55, 40, 30], "message_cap": 195,
                    "pacing_chunk_b64_chars": 384, "pacing_delay_seconds": 1.3}
        self.cfg = {"hash": "test1234", "settings": settings, "raw_cfg": {"layout": "rgb"}}
        self.manifest = P.Manifest(os.path.join(self.dir, "run_manifest.json"),
                                   {"events": [], "commands": [], "marks": [],
                                    "calibration": [], "loop": [], "previews": []})
        self.ctl = P.PoolController(self.args, self.link, self.camera, self.cfg, self.dir,
                                    self.manifest, HOST, nrjxl_fn=P.fake_nrjxl)
        if free is not None:
            self.ctl.free = free
        self.link.start()

    def run(self, seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.ctl.tick()
            if until and until(self):
                break
            time.sleep(0.002)
        for _ in range(5000):
            if not self.ctl.pump_console():
                break

    def acks(self, cid):
        return [ln for ln in self.link.lines if f" id={cid} " in ln + " "]

    def wait_idle(self, seconds=20):
        self.run(seconds, until=lambda h: h.ctl.phase == "idle" and not h.ctl.preview_busy
                 and (h.ctl.worker is None or not h.ctl.worker.is_alive()))

    def manifest_json(self):
        with open(os.path.join(self.dir, "run_manifest.json")) as fh:
            return json.load(fh)


class Controller(unittest.TestCase):
    def test_status_and_dedup(self):
        h = Harness(self, [(0, cmd(5, "mark", note="card_1m")), (0.05, cmd(5, "mark", note="card_1m")),
                           (0.1, cmd(6, "status"))])
        h.run(0.5)
        self.assertEqual(h.ctl.marks, 1, "a replayed id must not run twice")
        five = h.acks(5)
        self.assertEqual(len(five), 2)
        self.assertTrue(five[0].startswith(f"[{HOST}] OK id=5 pool mark #1 card_1m"))
        self.assertEqual(five[1], five[0] + " dup")
        self.assertIn("phase=idle", h.acks(6)[0])
        self.assertIn("ssid=", h.acks(6)[0])
        m = h.manifest_json()
        self.assertEqual(len(m["marks"]), 1)
        self.assertEqual(sum(1 for c in m["commands"] if c.get("dup")), 1)

    def test_errors_answered_with_id(self):
        h = Harness(self, [(0, {"id": 9, "c": "get", "k": ["x"]}), (0, b"garbage"),
                           (0, cmd(10, "preview", lines="1-2"))])
        h.run(0.3)
        self.assertTrue(h.acks(9)[0].startswith(f"[{HOST}] ERR id=9 pool get pool_mode"))
        self.assertIn("ERR id=10 pool preview no_last_preview", h.acks(10)[0])
        self.assertEqual(len(h.link.lines), 2, "garbage gets no answer")
        self.assertTrue(any(e["kind"] == "cmd_unanswerable" for e in h.manifest_json()["events"]))

    def test_calibration_then_autostart_loop(self):
        h = Harness(self, [(0, cmd(1, "cal", duration_s=10, interval_s=2))],
                    argv=["--loop-autostart-min", "0.002", "--loop-iterations", "2",
                          "--loop-period-s", "30"])
        h.ctl.args.cal_duration_s = 10
        # 10 s / 2 s = 5 frames; shrink the grid by patching interval through the command
        h.run(0.2)
        self.assertIn("OK id=1 pool cal start frames=5 every=2s", h.acks(1)[0])
        h.ctl.stop_evt.set()          # don't wait 10 s: stop after the current frame
        h.run(5, until=lambda h: h.ctl.loop_started)
        self.assertTrue(h.ctl.cal_done)
        self.assertTrue(h.ctl.loop_started, "auto-start fired")
        h.run(3, until=lambda h: h.ctl.loop_count >= 1)
        h.ctl.stop_evt.set()
        h.wait_idle()
        m = h.manifest_json()
        kinds = [e["kind"] for e in m["events"]]
        self.assertIn("loop_autostart", kinds)
        self.assertTrue(any(e["kind"] == "loop_start" and e["auto"] for e in m["events"]))
        self.assertTrue(any("AUTOSTART ok" in ln for ln in h.link.lines))
        it = m["loop"][0]
        self.assertTrue(it["pjpg"]["path"].endswith("_still_compressed.jpg"))
        self.assertTrue(it["nrjxl"]["path"].endswith("_still_compressed.nrjxl"))
        self.assertTrue(it["video"]["path"].endswith("_video.mp4"))
        self.assertGreater(it["pjpg"]["bytes"], 1000)
        self.assertLessEqual(it["pjpg"]["msgs"], 195)
        self.assertEqual(it["still"]["applied"]["LensPosition"], 1.82)
        for name in (it["pjpg"]["path"], it["nrjxl"]["path"], it["video"]["path"]):
            self.assertTrue(os.path.getsize(os.path.join(h.dir, "loop", name)) > 0)
        cal = m["calibration"][0]
        self.assertTrue(cal["dng"].endswith(".dng") and cal["jpg"].endswith(".jpg"))
        self.assertEqual(cal["applied"]["AnalogueGain"], 1.12)
        self.assertIn("free_mb", cal)

    def test_stop_while_idle_cancels_autostart(self):
        h = Harness(self, [(0, cmd(1, "cal", duration_s=10, interval_s=10))],
                    argv=["--loop-autostart-min", "0.02"])
        h.run(3, until=lambda h: h.ctl.cal_done)
        self.assertIsNotNone(h.ctl.autostart_at)
        h.link.script.append((0, cmd(2, "stop")))
        h.run(2)
        self.assertIn("autostart=cancelled", h.acks(2)[0])
        self.assertFalse(h.ctl.loop_started)

    def test_busy_and_space_guards(self):
        h = Harness(self, [(0, cmd(1, "cal", duration_s=600, interval_s=10)),
                           (0.05, cmd(2, "loop")), (0.1, cmd(3, "cal")), (0.15, cmd(4, "stop"))])
        h.run(0.5)
        self.assertIn("ERR id=2 pool loop busy phase=cal", h.acks(2)[0])
        self.assertIn("ERR id=3 pool cal busy phase=cal", h.acks(3)[0])
        self.assertIn("OK id=4 pool stop phase=cal", h.acks(4)[0])
        h.wait_idle()
        small = Harness(self, [(0, cmd(1, "cal")), (0, cmd(2, "loop"))], free=lambda: 500)
        small.run(0.3)
        self.assertRegex(small.acks(1)[0], r"ERR id=1 pool cal space need=\d+MB free=500MB")
        self.assertRegex(small.acks(2)[0], r"ERR id=2 pool loop space need=\d+MB free=500MB")
        self.assertFalse(small.ctl.cal_started)

    def test_low_space_mid_phase_stops(self):
        free = {"mb": 100000}
        h = Harness(self, [(0, cmd(1, "cal", duration_s=20, interval_s=2))],
                    free=lambda: free["mb"])
        h.run(3, until=lambda h: h.ctl.cal_count >= 1)
        free["mb"] = 5                      # below --min-free-mb 10
        h.wait_idle()
        self.assertIn("low_space", h.ctl.last_error)
        self.assertTrue(any("STOPPED low_space" in ln for ln in h.link.lines))
        end = [e for e in h.manifest_json()["events"] if e["kind"] == "cal_end"][0]
        self.assertTrue(end["stopped"])

    def test_capture_failure_recorded_phase_continues(self):
        cam = P.FakeCamera(capture_s=0.01, fail_on={1})
        h = Harness(self, [(0, cmd(1, "cal", duration_s=20, interval_s=2))], camera=cam)
        h.run(5, until=lambda h: h.ctl.cal_count >= 1)
        h.ctl.stop_evt.set()
        h.wait_idle()
        cal = h.manifest_json()["calibration"]
        self.assertIn("error", cal[0])
        self.assertNotIn("error", cal[1])

    def test_preview_wire_with_drops_and_resend(self):
        dropped_seqs = {2, 5}

        def drop(line):
            parts = line.split(" ")
            return len(parts) > 4 and parts[1] == "PV" and int(parts[4].split("/")[0]) in dropped_seqs

        h = Harness(self, [(0, cmd(1, "preview"))], drop=drop)
        h.wait_idle(15)
        ok = h.acks(1)[0]
        self.assertRegex(ok, r"OK id=1 pool preview img=\w+ n=\d+ bytes=\d+ q=\d+ eta=")
        first = V.reassemble(h.link.lines)
        self.assertEqual(first["missing"], [2, 5])
        dropped_seqs.clear()
        h.link.script.append((0, cmd(2, "preview", lines="2,5")))
        h.run(1)
        self.assertIn("resend", h.acks(2)[0])
        done = V.reassemble(h.link.lines)
        self.assertTrue(done["sha_ok"])
        self.assertLessEqual(len(done["jpeg"]), 30000)
        rec = h.manifest_json()["previews"][0]
        self.assertEqual(rec["wh"], [640, 360])
        self.assertTrue(os.path.exists(os.path.join(h.dir, "preview", os.path.basename(rec["native"]))))
        self.assertFalse([f for f in os.listdir(os.path.join(h.dir, "preview")) if f.endswith(".dng")])

    def test_acks_jump_the_preview_queue(self):
        h = Harness(self, [(0, cmd(1, "preview"))], argv=["--preview-line-s", "0.01"])
        h.run(10, until=lambda h: h.ctl.last_preview is not None)
        h.link.script.append((0, cmd(2, "status")))
        h.run(3, until=lambda h: not h.ctl.bulk and not h.ctl.prio)
        idx_status = next(i for i, ln in enumerate(h.link.lines) if " id=2 " in ln)
        idx_end = next(i for i, ln in enumerate(h.link.lines) if " PVE " in ln)
        self.assertLess(idx_status, idx_end, "acks go out between preview lines")


class MainSmoke(unittest.TestCase):
    def test_fake_main_writes_run_folder(self):
        root = tempfile.mkdtemp(prefix="pool_main_")
        self.addCleanup(shutil.rmtree, root, True)
        script = os.path.join(root, "script.json")
        with open(script, "w") as fh:
            json.dump([[0.2, cmd(1, "status")], [0.3, cmd(2, "cal", duration_s=10, interval_s=5)],
                       [0.5, cmd(3, "mark", note="card_1m")], [0.6, cmd(4, "preview")],
                       [11, cmd(5, "loop", iterations=2, period_s=30)]], fh)
        real_stdout = sys.stdout
        try:
            rc = P.main(["--fake", "--fake-script", script, "--run-root", root,
                         "--exit-when-done", "--max-runtime-s", "90", "--preview-line-s", "0",
                         "--min-free-mb", "10", "--loop-period-s", "30"], host=HOST)
        finally:
            sys.stdout = real_stdout
        # loop period 30 s x 2 would take 30 s: the second iteration starts at +30 s
        self.assertEqual(rc, 0)
        run = [d for d in os.listdir(root) if d.endswith(HOST)][0]
        path = os.path.join(root, run)
        for name in ("run_manifest.json", "pool.log", "console.log"):
            self.assertGreater(os.path.getsize(os.path.join(path, name)), 0, name)
        m = json.load(open(os.path.join(path, "run_manifest.json")))
        self.assertEqual(len(m["calibration"]), 2)
        self.assertEqual(len(m["loop"]), 2)
        self.assertEqual(len(m["marks"]), 1)
        self.assertEqual(len(m["previews"]), 1)
        self.assertIn("ended_utc", m)
        self.assertEqual(len([f for f in os.listdir(os.path.join(path, "calibration"))
                              if f.endswith(".dng")]), 2)


class EffectiveConfig(unittest.TestCase):
    def test_overlay_reaches_capture_settings_read_only(self):
        from tests.test_config_v2 import Unit
        u = Unit(self)
        state_path = u.values["commands.state_path"]
        overlay = {"camera.focus.mode": "manual", "camera.focus.lens_position": 1.82,
                   "camera.exposure.profile": "low_gain", "camera.exposure.max_shutter_us": 60000,
                   "camera.exposure.max_gain": 1.12}
        with open(state_path, "w") as fh:
            json.dump({"schema": "bm_command_state_v2", "overlay": overlay,
                       "overlay_ids": {k: 1 for k in overlay}}, fh)
        before = {p: open(p, "rb").read() for p in (u.v2, state_path, u.v1)}
        run_dir = tempfile.mkdtemp(prefix="pool_cfg_")
        self.addCleanup(shutil.rmtree, run_dir, True)
        cfg = P.load_effective(u.dir, run_dir)
        self.assertEqual(sorted(cfg["overlay"]), sorted(overlay))
        self.assertNotEqual(cfg["hash"], cfg["base_hash"])
        focus = cfg["capture_settings"]["camera_controls"]["focus"]
        self.assertEqual(str(focus.get("mode")), "manual")
        self.assertAlmostEqual(float(focus.get("lens_position")), 1.82)
        prof = cfg["capture_settings"]["exposure_profile"]
        self.assertEqual(prof.get("profile"), "low_gain")
        self.assertTrue(cfg["render"].startswith(run_dir))
        after = {p: open(p, "rb").read() for p in before}
        self.assertEqual(before, after, "the pool never writes config or state")


class RealCameraArgv(unittest.TestCase):
    """The Pi path with subprocess faked: the still argv is the production one
    (+ --raw), the clip argv is wide_720p @10 fps 2 Mbit/s with --mode and the
    tuning file, and a refused control retries once without controls."""

    def setUp(self):
        from unittest import mock
        from tests.test_config_v2 import Unit
        u = Unit(self)
        overlay = {"camera.focus.mode": "manual", "camera.focus.lens_position": 1.82,
                   "camera.exposure.profile": "low_gain",
                   "camera.exposure.max_shutter_us": 60000, "camera.exposure.max_gain": 1.12}
        with open(u.values["commands.state_path"], "w") as fh:
            json.dump({"schema": "bm_command_state_v2", "overlay": overlay,
                       "overlay_ids": {k: 1 for k in overlay}}, fh)
        self.run_dir = tempfile.mkdtemp(prefix="pool_real_")
        self.addCleanup(shutil.rmtree, self.run_dir, True)
        self.cfg = P.load_effective(u.dir, self.run_dir)
        import rc_capture
        import rc_exposure_profile
        import video_recorder
        patches = [
            mock.patch.object(rc_exposure_profile, "still_args",
                              return_value=(["--tuning-file", "/tmp/lowgain.json"],
                                            {"exposure_profile": "low_gain",
                                             "exposure_profile_applied": True})),
            mock.patch.object(rc_capture, "_select_camera_command",
                              return_value=("/usr/bin/rpicam-still", "rpicam")),
            mock.patch.object(video_recorder, "_select_video_command",
                              return_value=("/usr/bin/rpicam-vid", "rpicam")),
            mock.patch.object(P.shutil, "which", return_value=None),
        ]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)
        self.calls = []
        self.fail_first_video = False

        def fake_run(argv, **kw):
            self.calls.append(list(argv))
            out = argv[argv.index("-o") + 1]
            rc = 0
            if "rpicam-vid" in argv[0] and self.fail_first_video and len(self.calls) == 1:
                rc = 255
            else:
                with open(out, "wb") as fh:
                    fh.write(b"x" * 1000)
                if "--raw" in argv:
                    with open(os.path.splitext(out)[0] + ".dng", "wb") as fh:
                        fh.write(b"d" * 2000)
                if "--metadata" in argv:
                    with open(argv[argv.index("--metadata") + 1], "w") as fh:
                        json.dump({"LensPosition": 1.82, "AnalogueGain": 1.12,
                                   "ExposureTime": 59000, "ScalerCrop": [0, 0, 4608, 2592]}, fh)
            return subprocess.CompletedProcess(argv, rc)

        for mod in (rc_capture, P):
            pt = mock.patch.object(mod.subprocess, "run", side_effect=fake_run)
            pt.start()
            self.addCleanup(pt.stop)
        self.cam = P.RealCamera(self.cfg, 30, 10, "wide_720p", 2.0)

    def test_still_argv_is_production_plus_raw(self):
        cap = self.cam.capture_still(self.run_dir, "s1")
        argv = self.calls[-1]
        self.assertEqual(argv[0], "/usr/bin/rpicam-still")
        for flag, value in (("--width", "4608"), ("--height", "2592"), ("--lens-position", "1.82"),
                            ("--autofocus-mode", "manual"), ("--tuning-file", "/tmp/lowgain.json")):
            self.assertEqual(argv[argv.index(flag) + 1], value, flag)
        self.assertIn("--raw", argv)
        self.assertTrue(cap["dng"].endswith("s1.dng"))
        self.assertEqual(P.applied_from_metadata(cap["metadata"])["ExposureTime"], 59000)

    def test_video_argv(self):
        res = self.cam.record_video(self.run_dir, "v1")
        argv = self.calls[-1]
        self.assertEqual(argv[0], "/usr/bin/rpicam-vid")
        for flag, value in (("-t", "30000"), ("--width", "1280"), ("--height", "720"),
                            ("--framerate", "10"), ("--bitrate", "2000000"),
                            ("--mode", "2304:1296:10:P"), ("--tuning-file", "/tmp/lowgain.json"),
                            ("--lens-position", "1.82")):
            self.assertEqual(argv[argv.index(flag) + 1], value, flag)
        self.assertTrue(res["path"].endswith("v1.h264"), "no ffmpeg here: raw stream kept")
        self.assertIsNone(res["fallback"])

    def test_video_retries_without_controls(self):
        self.fail_first_video = True
        res = self.cam.record_video(self.run_dir, "v2")
        self.assertEqual(len(self.calls), 2)
        self.assertIn("--tuning-file", self.calls[0])
        self.assertNotIn("--tuning-file", self.calls[1])
        self.assertNotIn("--lens-position", self.calls[1])
        self.assertIn("retried without", res["fallback"])


@unittest.skipUnless(shutil.which("bash"), "bash needed")
class PoolModeTool(unittest.TestCase):
    TOOL = os.path.join(REPO_ROOT, "tools", "pool_mode.sh")

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pool_mode_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.dst = os.path.join(self.d, "BM_Devel_Pi")
        os.makedirs(self.dst)
        for f in ("pool_sequence.py", "camera_config.yaml", "bm_command_state_v2.json"):
            with open(os.path.join(self.dst, f), "w") as fh:
                fh.write(f"# {f}\n")
        self.tab = os.path.join(self.d, "crontab.txt")
        self.prod = ("@reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock "
                     "/home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh\n")
        with open(self.tab, "w") as fh:
            fh.write(self.prod)
        self.fake = os.path.join(self.d, "crontab")
        with open(self.fake, "w") as fh:
            fh.write(f"""#!/usr/bin/env bash
T={self.tab}
case "$1" in
  -l) [[ -f $T ]] && cat $T || exit 1 ;;
  -r) rm -f $T ;;
  *) cp "$1" $T ;;
esac
""")
        os.chmod(self.fake, 0o755)

    def tool(self, *args):
        env = dict(os.environ, BMCAM_DST=self.dst, POOL_BACKUP_DIR=os.path.join(self.d, "bk"),
                   POOL_RUN_ROOT=os.path.join(self.d, "runs"), CRONTAB=self.fake,
                   POOL_MODE_NO_HASH="1", POOL_PYTHON=sys.executable)
        r = subprocess.run(["bash", self.TOOL, *args], env=env, capture_output=True, text=True,
                           timeout=60)
        return r.returncode, r.stdout + r.stderr

    def test_on_off_round_trip(self):
        rc, out = self.tool("on", "--loop-autostart-min", "30")
        self.assertEqual(rc, 0, out)
        tab = open(self.tab).read()
        self.assertIn("# POOL_MODE_OFF @reboot", tab)
        self.assertIn("pool_sequence.py --run-root", tab)
        self.assertIn("--loop-autostart-min 30", tab)
        self.assertIn("# POOL_MODE_LINE", tab)
        self.assertEqual([ln for ln in tab.splitlines() if not ln.startswith("#")
                          and "rc_run_capture_cycle" in ln], [])
        rc, out = self.tool("on")
        self.assertNotEqual(rc, 0, "a second on must refuse (backup would be the pool crontab)")
        rc, out = self.tool("status")
        self.assertIn("mode        : POOL", out)
        rc, out = self.tool("off")
        self.assertEqual(rc, 0, out)
        self.assertEqual(open(self.tab).read(), self.prod)
        self.assertIn("byte-identical", out)
        self.assertIn("bit-identical", out)

    def test_off_warns_on_changed_config(self):
        self.assertEqual(self.tool("on")[0], 0)
        with open(os.path.join(self.dst, "camera_config.yaml"), "a") as fh:
            fh.write("changed: true\n")
        rc, out = self.tool("off")
        self.assertEqual(rc, 0, out)
        self.assertIn("changed since pool mode went on", out)
        self.assertEqual(open(self.tab).read(), self.prod)


if __name__ == "__main__":
    unittest.main()
