#!/usr/bin/env python3
# filename: test_trg_budget_reserve.py
# description: R1 G4 finding 5 — a budget-sized video clip (trg in the listen tail) must be sent.
"""
R1 G4 finding 5 (runs/g4_outdoor12h_20261002/RESULTS.md, branch
feature/r1-hil-test-engineer): a `trg` heard in the post-transmit listen tail
fires in the same boot with what is left of the budget. rc_video_tx sized the
clip to `max_messages_now() - 32` (START/END + keyframe_repeat_max), then the
x264 fit ran (prescale + pass-2 encode, ~5-10 s on a Pi Zero 2W), then
transmit_video_clip demanded the FULL reserve again on the smaller budget and
refused the clip: `clip needs 136 paced messages, 130 fit in the 169s left`
(bmcam003) and `needs 66 paced messages, 61 fit in the 79s left` (bmcam004).
The recording stayed on the SD and nothing told the backend. 2 of 6 G4
triggers were lost this way.

Pins:
  sized-to-fit is sent  a clip sized to the remaining budget is sent even when
                        the encode ate part of the reserve; only the optional
                        keyframe repeat shrinks
  honest refusal        a clip that genuinely cannot be sent (START + chunks +
                        END do not fit) is refused before START AND reported on
                        the existing <WS> status line (a=skip_err r=budget) on a
                        supervised action, so the trg does not vanish silently
  trg outcome           a trg-fired action reports `tr=<cmd id>:<sent|budget|fail>`
                        on a <WS> line (wire contract addition, Nick 2026-10-03):
                        a=trg after a send, the a=skip_err line on a refusal or
                        a failed action; nothing for an untriggered action

Everything is zero-sleep, fake clock; no hardware, no ffmpeg.

Run (repo root):
  python3 -m pytest tests/test_trg_budget_reserve.py -q
"""

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:                       # same stub pattern as test_rc_transmit
    _stub = types.ModuleType("serial")
    _stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = _stub

import rc_video_tx as vtx  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")
with open(os.path.join(VEC, "payload.h264"), "rb") as _fh:
    PAYLOAD = _fh.read()            # 126 chunks at 384 b64 chars (288 raw B/msg)

CHUNK_B64 = 384
RAW_PER_MSG = 288
SPM = 1.0                           # seconds per paced message (field: 1.3)
KEYFRAME_REPEAT_MAX = 30            # the field value (G4 logs: "after reserving 32")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += float(s)


def _settings(tmp):
    return {"config_path": "x.yaml", "budget_seconds": 480, "pacing_delay_seconds": SPM,
            "pacing_chunk_b64_chars": CHUNK_B64, "timezone": "UTC", "enforce_time_window": False,
            "window_start": "00:00", "window_end": "00:00",
            "capture_backend": "auto", "transmit_phase_cfg": {},
            "video": {"dir": tmp, "storage": {}, "clip_minutes": 5.0,
                      "geometry": {"output_wh": (1920, 1080), "fps": 15,
                                   "crop_native_xywh": (0, 0, 4608, 2592)}}}


def _supervised():
    """The fields of rc_supervisor.Supervisor that video_action reads on a
    transmit (not save_local) per_boot action."""
    return types.SimpleNamespace(run="per_boot", save_local=False, storage_reason=None,
                                 gate_kwargs=lambda daemon, settings: {},
                                 resend_recent_acks=lambda daemon, summary, sleep_fn: None)


def run_trg_action(*, remaining_s, encode_s, supervised=None, trigger_id=None):
    """One video_action on a budget with `remaining_s` left at the sizing
    point; the fit (encode) takes `encode_s` and fills the budget it is given
    exactly, the way a budget-bound trg clip does in the field."""
    tmp = tempfile.TemporaryDirectory()
    clk = Clock()
    budget = CycleBudget(remaining_s, SPM, clock=clk)
    cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True,
                                            keyframe_repeat_max=KEYFRAME_REPEAT_MAX,
                                            source="test"))
    wire, wake_calls = [], []

    def record(s, vcfg, video_dir, **kw):
        return {"ok": True, "stage": "done", "bytes": 1,
                "basename": "2026-10-03T11-05-15Z_video_1920x1080_15fps",
                "mp4": os.path.join(video_dir, "c.mp4")}

    def fit(src, work, **kw):
        msgs = kw["budget_msgs"]
        clk.t += encode_s                                  # prescale + 2-pass x264
        payload = PAYLOAD[:msgs * RAW_PER_MSG]
        return {"payload": payload, "bytes": len(payload), "msgs": msgs,
                "budget_msgs": msgs, "used_pct": 100.0, "target_kbps": 40.0,
                "pass2_tries": 2, "frames_trimmed": 0, "frames": 50, "duration_s": 5.0,
                "keyframe_end": KEYFRAME_REPEAT_MAX * RAW_PER_MSG,
                "prescale_s": encode_s / 2, "encode_s": encode_s / 2}

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            summary = {"command_events": []}
            if trigger_id is not None:          # what the boot drain sets for a trg
                summary["trigger"] = {"id": trigger_id, "value": 2}
            vtx.video_action(
                _settings(tmp.name), cfg, summary, None, budget, {"opened": False},
                transmit=True, skip_time_window=False,
                gate_fn=lambda path, **kw: (True, {"reason": "test gate"}),
                record_fn=record, fit_fn=fit, tx_open_fn=lambda p: wire.append,
                ensure_room_fn=lambda d, st: {"paused": False}, sleep_fn=clk.sleep,
                clock=clk, now_fn=lambda: __import__("datetime").datetime(2026, 10, 3, 11, 5, 15),
                encoder_binary="/usr/bin/rpicam-vid", ffmpeg_binary="/usr/bin/ffmpeg",
                bm_commands_cfg=None, bench_commands=False, bench_drop_chunks=None,
                supervised=supervised, wake_fn=lambda **kw: wake_calls.append(kw))
    finally:
        tmp.cleanup()
    return summary, wire, wake_calls, out.getvalue()


class TestBudgetSizedClipIsSent(unittest.TestCase):
    def test_g4_trg_clip_sized_to_the_budget_is_sent(self):
        # bmcam004 14:06Z shape: ~85 s left at sizing -> 85 - 32 = 53 chunk
        # msgs; the encode takes 9 s, so 76 s are left at the send check.
        summary, wire, _, out = run_trg_action(remaining_s=85.0, encode_s=9.0)
        res = summary["transmit_result"]
        self.assertEqual(summary["fit"]["msgs"], 53, out)
        self.assertIsNone(res["refused_reason"], out)
        self.assertNotIn("NOT sent", out)
        self.assertTrue(res["started"] and res["complete_send"], res)
        self.assertEqual(res["sent"], 53)
        self.assertTrue(wire[0].startswith(b"<START IMG> ") and b"length: 53" in wire[0])
        self.assertTrue(wire[-1].startswith(b"<END IMG> ") and b"sent_buffers: 53" in wire[-1])
        # Only the optional keyframe repeat gave way: 76 s - START - 53 chunks
        # leaves 22 slots, END keeps one -> 21 of the 30 repeats.
        self.assertEqual(res["repeated"], 21)
        self.assertFalse(res["repeat_sent"])
        self.assertEqual(summary["stage"], "done")

    def test_no_encode_delay_still_sends_the_whole_repeat(self):
        summary, _, _, out = run_trg_action(remaining_s=85.0, encode_s=0.0)
        res = summary["transmit_result"]
        self.assertTrue(res["complete_send"] and res["repeat_sent"], out)
        self.assertEqual(res["repeated"], KEYFRAME_REPEAT_MAX)


class TestUnsendableClipIsReported(unittest.TestCase):
    def test_refusal_is_reported_on_the_ws_line(self):
        # Pathological: the encode eats more than the whole reserve, so
        # START + 53 chunks + END (55 msgs) no longer fit in 85 - 40 = 45 s.
        summary, wire, wake_calls, out = run_trg_action(
            remaining_s=85.0, encode_s=40.0, supervised=_supervised())
        res = summary["transmit_result"]
        self.assertEqual(wire, [])
        self.assertFalse(res["started"])
        self.assertIn("budget: clip needs 55 paced messages", res["refused_reason"])
        self.assertIn("NOT sent", out)
        self.assertEqual(len(wake_calls), 1, wake_calls)
        self.assertEqual((wake_calls[0]["action"], wake_calls[0]["reason"]),
                         ("skip_err", "budget"))
        self.assertEqual(summary["stage"], "done")

    def test_unsupervised_legacy_cycle_sends_no_status(self):
        # The pre-supervisor cycle stays byte-identical (as for W3 skip_win).
        _, _, wake_calls, out = run_trg_action(remaining_s=85.0, encode_s=40.0)
        self.assertIn("NOT sent", out)
        self.assertEqual(wake_calls, [])



class TestAckResendPlacement(unittest.TestCase):
    def test_video_action_resends_after_the_gate_before_recording(self):
        # R1 review: the re-send needs the gate's Spotter time read (lane guard)
        # and must still go before the recording.
        calls = []

        def resend(daemon, summary, sleep_fn):
            calls.append(("resend", summary["stage"]))
        sup = types.SimpleNamespace(
            run="per_boot", save_local=False, storage_reason=None,
            gate_kwargs=lambda daemon, settings: calls.append(("gate", None)) or {},
            resend_recent_acks=resend)
        run_trg_action(remaining_s=85.0, encode_s=0.0, supervised=sup)
        self.assertEqual(calls, [("gate", None), ("resend", "time_gate")])


class TestTriggerOutcome(unittest.TestCase):
    def setUp(self):
        import rc_telemetry
        patcher = mock.patch.object(rc_telemetry, "WS_EXTRA_FN", lambda: [("cfg", "1910bbef")])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sent_trg_reports_tr_sent_after_end(self):
        summary, wire, wake_calls, out = run_trg_action(
            remaining_s=85.0, encode_s=9.0, supervised=_supervised(), trigger_id=1000059)
        self.assertTrue(summary["transmit_result"]["started"], out)
        self.assertEqual(len(wake_calls), 1, wake_calls)
        self.assertEqual((wake_calls[0]["action"], wake_calls[0]["reason"],
                          wake_calls[0]["trigger_result"]), ("trg", None, "1000059:sent"))
        self.assertEqual(summary["trigger_outcome"], "sent")

    def test_refused_trg_reports_tr_budget_on_the_skip_err_line(self):
        summary, _, wake_calls, _ = run_trg_action(
            remaining_s=85.0, encode_s=40.0, supervised=_supervised(), trigger_id=1000025)
        self.assertEqual(len(wake_calls), 1, wake_calls)
        self.assertEqual((wake_calls[0]["action"], wake_calls[0]["reason"],
                          wake_calls[0]["trigger_result"]), ("skip_err", "budget", "1000025:budget"))
        self.assertEqual(summary["trigger_outcome"], "budget")

    def test_untriggered_action_has_no_tr(self):
        _, _, wake_calls, _ = run_trg_action(remaining_s=85.0, encode_s=0.0,
                                             supervised=_supervised())
        self.assertEqual(wake_calls, [])                 # a normal send: no <WS> added
        _, _, wake_calls, _ = run_trg_action(remaining_s=85.0, encode_s=40.0,
                                             supervised=_supervised())
        self.assertNotIn("trigger_result", wake_calls[0])

    def test_failed_trg_action_reports_tr_fail(self):
        class Sup:
            run, save_local, storage_reason, quiet_skip = "per_boot", False, None, False
            one_shot_vtx = current_vtx = None

            def gate_kwargs(self, daemon, settings):
                return {}

            def resend_recent_acks(self, daemon, summary, sleep_fn):
                pass

            def start(self, summary, **kw):
                return None, CycleBudget(480, SPM, clock=Clock())

            def boot_drain(self, settings, summary, sleep_fn):
                summary["trigger"] = {"id": 1000048, "value": 2}
                return settings, {"skip_time_window": False, "capture_only": False}

        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True,
                                                source="test"))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("rc_telemetry.send_wake_status") as ws, \
                contextlib.redirect_stdout(io.StringIO()):
            summary = vtx.run_video_tx_cycle(
                _settings(tmp), cfg, transmit=True, supervised=Sup(),
                gate_fn=lambda path, **kw: (True, {"reason": "test gate"}),
                record_fn=lambda *a, **kw: {"ok": False, "stage": "encode"},
                ensure_room_fn=lambda d, st: {"paused": False}, sleep_fn=lambda s: None,
                clock=Clock(), encoder_binary="/usr/bin/rpicam-vid",
                ffmpeg_binary="/usr/bin/ffmpeg")
        self.assertIn("recording failed", summary["error"])
        ws.assert_called_once()
        kw = ws.call_args.kwargs
        self.assertEqual((kw["action"], kw["reason"], kw["trigger_result"]),
                         ("skip_err", "fail", "1000048:fail"))

    def test_no_trg_line_without_cfg(self):
        # R1 review: the backend drops tr= without cfg= (a non-v9 supervisor).
        import rc_telemetry
        with mock.patch.object(rc_telemetry, "WS_EXTRA_FN", None):
            summary, _, wake_calls, _ = run_trg_action(
                remaining_s=85.0, encode_s=9.0, supervised=_supervised(), trigger_id=600)
        self.assertTrue(summary["transmit_result"]["complete_send"])
        self.assertEqual(wake_calls, [])

    def test_stalled_partial_send_is_fail_not_sent(self):
        # R1 review: a send that started but stalled reports fail (r=partial).
        real = vtx.transmit_video_clip

        def stalled(tx, budget, **kw):
            res = real(tx, budget, **kw)
            return dict(res, complete_send=False, sent=res["planned"] - 3)
        with mock.patch.object(vtx, "transmit_video_clip", stalled):
            summary, _, wake_calls, _ = run_trg_action(
                remaining_s=85.0, encode_s=0.0, supervised=_supervised(), trigger_id=1000060)
        self.assertEqual((wake_calls[0]["action"], wake_calls[0]["reason"],
                          wake_calls[0]["trigger_result"]), ("trg", "partial", "1000060:fail"))
        self.assertEqual(summary["trigger_outcome"], "fail")

    def test_trg_line_skipped_when_the_budget_is_spent(self):
        # R1 review: the +1 <WS> is budget-checked. 55 s: START + 53 chunks +
        # END = 55 msgs, nothing left for the pacing lead + the line.
        summary, _, wake_calls, out = run_trg_action(
            remaining_s=85.0, encode_s=30.0, supervised=_supervised(), trigger_id=1000061)
        self.assertTrue(summary["transmit_result"]["complete_send"], out)
        self.assertEqual(wake_calls, [])
        self.assertIn("no budget left for the <WS> status line", out)

    def test_ws_drops_tr_without_cfg(self):
        import rc_telemetry
        sent = []
        with mock.patch.object(rc_telemetry, "send_compact_text_message", sent.append), \
                mock.patch.object(rc_telemetry, "get_cpu_temperature", lambda: 45.0), \
                mock.patch.object(rc_telemetry, "WS_EXTRA_FN", None):
            rc_telemetry.send_wake_status("skip_err", reason="budget",
                                          trigger_result="600:budget")
        self.assertNotIn("tr=", sent[0])

    def test_ws_line_carries_tr_right_after_the_extras(self):
        import rc_telemetry
        sent = []
        with mock.patch.object(rc_telemetry, "send_compact_text_message", sent.append), \
                mock.patch.object(rc_telemetry, "get_cpu_temperature", lambda: 45.0), \
                mock.patch.object(rc_telemetry, "get_software_sha", lambda: "abc"), \
                mock.patch.object(rc_telemetry, "get_hostname", lambda: "bmcam003"), \
                mock.patch.object(rc_telemetry, "WS_EXTRA_FN", lambda: [("cfg", "1910bbef")]):
            rc_telemetry.send_wake_status("trg", timezone_name="UTC", image_res_key="480x270",
                                          trigger_result="1000059:sent")
            rc_telemetry.send_wake_status("cap", timezone_name="UTC", image_res_key="480x270")
        self.assertTrue(sent[0].startswith("<WS v=1 a=trg cfg=1910bbef tr=1000059:sent tz=UTC "),
                        sent[0])
        self.assertNotIn("tr=", sent[1])


if __name__ == "__main__":
    unittest.main()
