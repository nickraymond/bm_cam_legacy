#!/usr/bin/env python3
# filename: test_rc_video_tx.py
# description: Sprint22 Phase 2 — video transmit loop + budget-fitting encoder.
"""
Sprint22 Phase 2 — tests for the on-camera video send.

Pins: transmit_video_clip reproduces the committed golden wire BYTE-FOR-BYTE
from the golden payload (so the camera's wire is the contract's wire); refusal
before START when the budget cannot hold the clip; an honest END on a mid-send
stall; the 2-pass correction loop, tail trim and payload checks in
rc_video_clip; and (when ffmpeg is installed) a real encode that fits its
budget. Everything is zero-sleep; no hardware.

Run (repo root):
  python3 -m unittest tests.test_rc_video_tx -v
"""

import os
import shutil
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:                       # same stub pattern as test_rc_transmit
    stub = types.ModuleType("serial")
    stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = stub

import rc_video_clip as clip  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from rc_transmit import VIDEO_ENVELOPE_MSGS, transmit_video_clip, video_burst_messages  # noqa: E402
from rc_uplink_messages import build_rc_video_start_message, format_crop  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")
GOLD_META = {"timezone": "America/New_York", "software_sha": "0123456789ab", "hostname": "bmcamGOLD"}


def _read(name):
    with open(os.path.join(VEC, name), "rb") as fh:
        return fh.read()


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def send(payload, *, budget_s=1000.0, **kw):
    clk, wire = FakeClock(), []
    budget = CycleBudget(budget_s, 1.0, clock=clk)
    args = dict(payload=payload, file_name="2026-09-20T06-10-00Z_video_5s.h264", fps=10, dur=5.0,
                res="480x270", crop="na", crf=40, chunk_b64_chars=384, delay_seconds=1.0,
                start_metadata=GOLD_META, cpu_temp_text="41.2",
                current_timestamp="2026-09-20T06:10:04Z", sleep_fn=clk.sleep, clock=clk)
    args.update(kw)
    return transmit_video_clip(wire.append, budget, **args), wire


class TestTransmitVideoClip(unittest.TestCase):
    def test_golden_wire_byte_for_byte(self):
        golden = _read("wire_complete.txt")
        # The golden END carries uart_duration_sec 129.0; pin the clock to it.
        ticks = iter([0.0, 129.0, 129.0])
        clk = FakeClock()
        wire = []
        result = transmit_video_clip(
            wire.append, CycleBudget(1000.0, 1.0, clock=clk), payload=_read("payload.h264"),
            file_name="2026-09-20T06-10-00Z_video_5s.h264", fps=10, dur=5.0, res="480x270",
            crop="na", crf=40, keyframe_chunks=clip.keyframe_end_offset(_read("payload.h264")) // 288 + 1,
            chunk_b64_chars=384, delay_seconds=1.0, start_metadata=GOLD_META,
            cpu_temp_text="41.2", current_timestamp="2026-09-20T06:10:04Z",
            sleep_fn=clk.sleep, clock=lambda: next(ticks))
        self.assertEqual(b"".join(wire), golden)
        self.assertEqual((result["planned"], result["sent"], result["complete_send"],
                          result["repeated"], result["repeat_sent"]), (126, 126, True, 8, True))

    def test_shape_and_pacing(self):
        result, wire = send(_read("payload.h264"), keyframe_chunks=8)
        self.assertTrue(wire[0].startswith(b"<START IMG> ") and b"length: 126, fmt=h264" in wire[0])
        self.assertTrue(wire[1].startswith(b"<I0>") and wire[126].startswith(b"<I125>"))
        self.assertEqual(wire[127:135], wire[1:9])                 # keyframe chunks 0-7 repeated, identical
        self.assertTrue(wire[135].startswith(b"<END IMG> ") and b"sent_buffers: 126" in wire[135])
        self.assertEqual(len(wire), video_burst_messages(_read("payload.h264"), 384, keyframe_chunks=8))
        self.assertEqual(result["uart_duration_sec"], 135.0)       # no sleep after END

    def test_default_repeat_is_chunk_zero_and_repeat_is_clamped(self):
        _, wire = send(_read("payload.h264"))
        self.assertEqual((len(wire), wire[127]), (129, wire[1]))
        _, wire = send(_read("payload.h264")[:500], keyframe_chunks=99)    # 2-chunk clip
        self.assertEqual(len(wire), 2 + 2 + VIDEO_ENVELOPE_MSGS)

    def test_repeat_stops_early_but_end_still_goes_out(self):
        result, wire = send(_read("payload.h264"), keyframe_chunks=8, budget_s=126 + 8 + 2)
        self.assertTrue(result["complete_send"])
        result, wire = send(_read("payload.h264"), keyframe_chunks=8, budget_s=1000)
        self.assertEqual(result["repeated"], 8)

    def test_refused_before_start_when_budget_cannot_hold_it(self):
        result, wire = send(_read("payload.h264"), keyframe_chunks=8,
                            budget_s=126 + 8 + VIDEO_ENVELOPE_MSGS - 1)
        self.assertEqual(wire, [])
        self.assertFalse(result["started"])
        self.assertIn("budget: clip needs 136", result["refused_reason"])

    def test_stall_mid_send_closes_with_an_honest_end(self):
        clk, wire = FakeClock(), []
        budget = CycleBudget(1000.0, 1.0, clock=clk)

        def tx(msg):
            wire.append(msg)
            if msg.startswith(b"<I40>"):
                clk.t += 900.0                                      # the cycle stalls
        result = transmit_video_clip(
            tx, budget, payload=_read("payload.h264"), file_name="a_video_5s.h264", fps=10,
            dur=5.0, res="480x270", crop="na", br=40, chunk_b64_chars=384, delay_seconds=1.0,
            sleep_fn=clk.sleep, clock=clk)
        self.assertTrue(result["started"] and not result["complete_send"] and not result["repeat_sent"])
        self.assertLess(result["sent"], 126)
        self.assertIn(f"sent_buffers: {result['sent']}".encode(), wire[-1])
        self.assertTrue(wire[-1].startswith(b"<END IMG> "))

    def test_empty_payload_refused(self):
        result, wire = send(b"")
        self.assertEqual((wire, result["refused_reason"]), ([], "empty_payload"))

    def test_br_key_and_crop_format(self):
        self.assertEqual(format_crop((1504, 846, 1600, 900)), "1600x900+1504+846")
        self.assertEqual(format_crop(None), "na")
        msg = build_rc_video_start_message("a.h264", "2026-09-20T06:10:04Z", 88, fps=10, dur=5,
                                           res="480x270", crop=format_crop((0, 0, 4608, 2592)), br=38.9)
        self.assertIn(", crop=4608x2592+0+0, br=39, cmp=1", msg)
        self.assertLessEqual(len(msg.encode("ascii")), 285)


SPS, PPS = b"\x00\x00\x00\x01\x67\x64\x00\x1e", b"\x00\x00\x00\x01\x68\xee\x3c"
IDR = b"\x00\x00\x01\x65" + b"\xaa" * 40


def stream(n_p=4, p_len=20, sei=False):
    body = (b"\x00\x00\x01\x06\x05\x10x264" if sei else b"") + SPS + PPS + IDR
    return body + b"".join(b"\x00\x00\x01\x41" + bytes([i]) * p_len for i in range(n_p))


class TestPayloadHelpers(unittest.TestCase):
    def test_nal_units_and_check(self):
        self.assertEqual([t for _, t in clip.nal_units(stream(2))], [7, 8, 5, 1, 1])
        clip.check_payload(stream(2))
        with self.assertRaisesRegex(clip.ClipError, "does not start with an SPS"):
            clip.check_payload(stream(2, sei=True))
        with self.assertRaisesRegex(clip.ClipError, "outside chunk 0"):
            clip.check_payload(stream(2), raw_bytes_per_msg=10)

    def test_trim_cuts_whole_frames_only(self):
        full = stream(n_p=4, p_len=20)
        self.assertEqual(clip.trim_to_budget(full, len(full)), (full, 0))
        trimmed, dropped = clip.trim_to_budget(full, len(full) - 1)
        self.assertEqual(dropped, 1)
        self.assertEqual(trimmed, stream(n_p=3, p_len=20))
        clip.check_payload(trimmed)
        with self.assertRaisesRegex(clip.ClipError, "even the keyframe alone"):
            clip.trim_to_budget(full, 30)


class TestFitLoop(unittest.TestCase):
    """The correction loop, with a fake ffmpeg whose pass-2 size = f(target kbps)."""

    y4m_frames = 50

    def run_fit(self, size_for_kbps, budget_msgs=10):
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "src.mp4")
        open(src, "wb").write(b"x")
        targets = []

        def fake_run(argv, timeout_s):
            out = argv[-1]
            if out.endswith(".y4m"):
                with open(out, "wb") as fh:
                    fh.write(b"YUV4MPEG2 W4 H2 F10:1\n" + b"FRAME\n" + b"\x00" * 12)
                    fh.write((b"FRAME\n" + b"\x00" * 12) * (self.y4m_frames - 1))
            elif "-pass" in argv and argv[argv.index("-pass") + 1] == "2":
                kbps = float(argv[argv.index("-b:v") + 1].rstrip("k"))
                targets.append(kbps)
                size = size_for_kbps(kbps)
                head = SPS + PPS + IDR
                n_p = max(0, (size - len(head)) // 24)
                open(out, "wb").write(head + b"".join(b"\x00\x00\x01\x41" + b"\x11" * 20 for _ in range(n_p)))
            return 0, ""
        try:
            res = clip.fit_clip_to_budget(src, os.path.join(tmp, "w"), width=480, height=270, fps=10,
                                          duration_s=5, budget_msgs=budget_msgs, source_wh=(1920, 1080),
                                          run_fn=fake_run, log_fn=lambda *_: None)
            self.assertEqual(os.listdir(os.path.join(tmp, "w")), [])      # temp files removed
            return res, targets
        finally:
            shutil.rmtree(tmp)

    def test_undershoot_is_corrected_with_pass2_only(self):
        # Calm-scene behaviour measured on bmcam004: lands at ~75 % of target.
        res, targets = self.run_fit(lambda kbps: int(kbps * 1000 * 5 / 8 * 0.75))
        self.assertGreaterEqual(res["pass2_tries"], 2)
        self.assertGreater(targets[1], targets[0])
        self.assertTrue(93.0 <= res["used_pct"] <= 100.0, res)
        self.assertLessEqual(res["msgs"], 10)

    def test_steep_rate_response_does_not_oscillate(self):
        # bmcam004 2026-09-21: 55.7k -> 77 %, 69.4k -> 131 %. Size ~ kbps^2.4.
        res, targets = self.run_fit(lambda kbps: int(28116 * (kbps / 55.7) ** 2.4), budget_msgs=126)
        self.assertEqual(len(targets), 3)
        self.assertTrue(targets[0] < targets[2] < targets[1], targets)   # 3rd try lands BETWEEN
        self.assertTrue(90.0 <= res["used_pct"] <= 100.0, res)
        self.assertEqual(res["frames_trimmed"], 0)
        self.assertAlmostEqual(res["target_kbps"], targets[2], places=1)  # the kbps actually used

    def test_keeps_the_best_fit_not_the_last_try(self):
        sizes = iter([int(0.90 * 2880), int(1.20 * 2880), int(0.60 * 2880)])
        res, _ = self.run_fit(lambda kbps: next(sizes))
        self.assertGreaterEqual(res["used_pct"], 88.0, res)               # the 90 % try, not the 60 % one

    def test_short_source_sends_a_shorter_clip_and_says_so(self):
        self.y4m_frames = 49
        try:
            res, _ = self.run_fit(lambda kbps: int(kbps * 1000 * 4.9 / 8))
        finally:
            self.y4m_frames = 50
        self.assertEqual((res["frames"], res["duration_s"]), (49, 4.9))

    def test_on_target_first_try(self):
        res, targets = self.run_fit(lambda kbps: int(kbps * 1000 * 5 / 8))
        self.assertEqual((res["pass2_tries"], len(targets), res["frames_trimmed"]), (1, 1, 0))

    def test_stubborn_overshoot_is_trimmed_never_over_budget(self):
        res, _ = self.run_fit(lambda kbps: 10 * 288 + 200)         # ignores the target
        self.assertEqual(res["pass2_tries"], clip.MAX_PASS2_TRIES)
        self.assertGreater(res["frames_trimmed"], 0)
        self.assertLessEqual(res["bytes"], 10 * 288)

    def test_refuses_upscale_missing_source_and_tiny_budget(self):
        kw = dict(width=480, height=270, fps=10, duration_s=5, budget_msgs=10, run_fn=lambda *a: (0, ""))
        with self.assertRaisesRegex(clip.ClipError, "UPSCALE"):
            clip.fit_clip_to_budget(__file__, "/tmp/x", source_wh=(320, 180), **kw)
        with self.assertRaisesRegex(clip.ClipError, "source clip missing"):
            clip.fit_clip_to_budget("/nonexistent.mp4", "/tmp/x", **kw)
        with self.assertRaisesRegex(clip.ClipError, "cannot hold a clip"):
            clip.fit_clip_to_budget(__file__, "/tmp/x", **{**kw, "budget_msgs": 1})


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class TestRealEncode(unittest.TestCase):
    def test_real_clip_fits_its_budget_and_meets_the_contract(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src.mp4")
            os.system(f"ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=15 "
                      f"-t 7 -c:v libx264 -preset ultrafast -pix_fmt yuv420p {src}")
            for budget in (60, 126):
                res = clip.fit_clip_to_budget(src, os.path.join(tmp, "w"), width=480, height=270, fps=10,
                                              duration_s=5, budget_msgs=budget, source_wh=(1280, 720),
                                              preset="veryfast", log_fn=lambda *_: None)
                self.assertLessEqual(res["msgs"], budget, res)
                self.assertEqual((res["frames"], res["duration_s"]), (50, 5.0), res)   # the 49-frame bug
                self.assertGreater(res["keyframe_end"], 100)
                self.assertGreaterEqual(res["used_pct"], 85.0, res)
                clip.check_payload(res["payload"])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
