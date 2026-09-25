#!/usr/bin/env python3
# filename: test_s4_media_key.py
# description: Sprint25 S4 — rev 5 media key, keyed wire, sent record (RESEND_DEVICE.md §2-3).
"""
Sprint25 S4. Pins:

  key               base-36 seconds since 2026-01-01Z; 2026-09-20T06:10:00Z -> 0dhnso; range
  Spotter UTC only  gate `spotter` read or the daemon's explicit read; `rtc`/`system`/
                    `skipped` never make a key -> legacy wire
  monotonic         a key <= the last persisted key (stale clock) -> legacy; state is atomic
  island            absent = disabled; bad values name the key; media_gid + media_key refused
  rev 5 golden      transmit_video_clip(media_key="0dhnso") reproduces, byte for byte, the
                    backend's keyed vector (nereus-vision-dev
                    backend/tests/fixtures/bm_media_keyed_m0/wire_keyed_as_legacy_complete.txt,
                    copied to tests/vectors/bm_media_h264_rev5/); END == the rev 3 END (P4);
                    longest line 398 B
  stills keyed      START `length: N, key=...`, chunks `<I{key}.{i}>`
  sent record       written before START: sidecar fields, video payload copy, image points at
                    the JPEG (no copy); prune by age (hard cap 30 d)
  video cycle       enabled island + Spotter gate read -> keyed wire + sent record; no Spotter
                    time -> legacy wire, no record

Run (repo root):  python3 -m pytest tests/test_s4_media_key.py -q
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")
    _stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = _stub

import bm_video_tx_loopback as tool  # noqa: E402
import rc_media_key as mk  # noqa: E402
import rc_video_tx as vtx  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from rc_transmit import transmit_progressive_image, transmit_video_clip  # noqa: E402

VEC = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264")
REV5 = os.path.join(REPO_ROOT, "tests", "vectors", "bm_media_h264_rev5")
UTC = timezone.utc
KEY = "0dhnso"
T_KEY = datetime(2026, 9, 20, 6, 10, 0, tzinfo=UTC)


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


PAYLOAD = read(os.path.join(VEC, "payload.h264"))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TestKey(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(mk.key_for_utc(T_KEY), KEY)
        self.assertEqual(mk.decode_key(KEY), 22_659_000)
        self.assertEqual(mk.encode_key(0), "000000")
        self.assertIsNone(mk.encode_key(-1))
        self.assertIsNone(mk.encode_key(36 ** 6))
        self.assertEqual(mk.key_time(KEY), T_KEY)
        for bad in ("0DHNSO", "0dhns", "0dhnso0", "0dh-so", 12):
            with self.assertRaises(ValueError):
                mk.decode_key(bad)

    def test_spotter_utc_only(self):
        spot = {"source_time": "spotter", "utc_time": "2026-09-20T06:10:00+00:00"}
        self.assertEqual(mk.spotter_utc_for_wake(spot), (T_KEY, "spotter"))
        for src in ("rtc", "system", "skipped"):
            dt, why = mk.spotter_utc_for_wake({"source_time": src, "utc_time": "2026-09-20T06:10:00+00:00"})
            self.assertIsNone(dt, src)
            self.assertIn(src, why)
        daemon = types.SimpleNamespace(wait_for_spotter_utc=lambda timeout: T_KEY)
        self.assertEqual(mk.spotter_utc_for_wake({"source_time": "system"}, daemon), (T_KEY, "spotter_explicit"))

        def silent(timeout):
            raise TimeoutError("no UTC on the bus")

        dt, why = mk.spotter_utc_for_wake(None, types.SimpleNamespace(wait_for_spotter_utc=silent))
        self.assertIsNone(dt)
        self.assertIn("spotter_read_failed", why)

    def test_monotonic_state(self):
        with tempfile.TemporaryDirectory() as d:
            st = os.path.join(d, "last.txt")
            self.assertEqual(mk.allocate_key(T_KEY, "spotter", st), (KEY, "spotter"))
            self.assertEqual(open(st).read(), KEY)
            key, why = mk.allocate_key(T_KEY, "spotter", st)              # same second again
            self.assertIsNone(key)
            self.assertIn("stale_clock", why)
            key, why = mk.allocate_key(datetime(2026, 1, 2, tzinfo=UTC), "spotter", st)   # rewound clock
            self.assertIsNone(key)
            later = datetime(2026, 9, 20, 6, 25, 0, tzinfo=UTC)
            self.assertEqual(mk.allocate_key(later, "spotter", st)[0], mk.key_for_utc(later))
            with open(st, "w") as fh:
                fh.write("garbage")                                        # corrupt state is overwritten
            self.assertIsNotNone(mk.allocate_key(later.replace(minute=40), "spotter", st)[0])
            self.assertEqual(mk.allocate_key(None, "no_spotter_time"), (None, "no_spotter_time"))


class TestIsland(unittest.TestCase):
    def test_default_retention_is_14_days(self):
        # Nick 2026-09-24: the heal window is 14 d (sent records kept that long).
        self.assertEqual(mk.DEFAULT_CONFIG["retain_days"], 14.0)

    def yaml(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_absent_disabled_and_bad_values(self):
        cfg = mk.load_media_key_config(self.yaml("capture_mode: \"video\"\n"))
        self.assertEqual((cfg["enabled"], cfg["source"]), (False, "defaults"))
        cfg = mk.load_media_key_config(self.yaml("media_key:\n  enabled: true  # rev 5\n  retain_days: 7\nvideo:\n  fps: 15\n"))
        self.assertEqual((cfg["enabled"], cfg["retain_days"], cfg["source"]), (True, 7.0, "yaml"))
        for body, key in (("enabled: yes", "enabled"), ("retain_days: 0", "retain_days"),
                          ("retain_days: 31", "retain_days"), ("retain_days: x", "retain_days")):
            with self.assertRaisesRegex(ValueError, f"media_key.{key}"):
                mk.load_media_key_config(self.yaml(f"media_key:\n  {body}\n"))

    def test_retired_media_gid_is_warned_and_ignored(self):
        # Sprint26 S1 (DESIGN_supervisor.md §8.3): a stale island never fails a boot.
        import contextlib
        import io
        import rc_progressive_jpeg as rc
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cfg = rc._load_media_key_cfg(self.yaml("media_key:\n  enabled: true\nmedia_gid:\n  enabled: true\n"))
        self.assertTrue(cfg["enabled"])
        self.assertIn("media_gid.enabled is true", out.getvalue())
        self.assertFalse(mk.warn_retired_media_gid(self.yaml("media_gid:\n  enabled: false\n")))

    def test_chunk_prefix(self):
        self.assertEqual(mk.chunk_prefix(7), "<I7>")
        self.assertEqual(mk.chunk_prefix(173), "<I173>")
        self.assertEqual(mk.chunk_prefix(7, KEY), f"<I{KEY}.7>")


class TestRev5Golden(unittest.TestCase):
    def test_video_wire_matches_backend_vector(self):
        clk, wire = Clock(), []
        transmit_video_clip(
            wire.append, CycleBudget(600, 1.0, clock=clk), payload=PAYLOAD, file_name=tool.GOLDEN_FILENAME,
            fps=10, dur=5.0, res="480x270", crop="na", crf=40, keyframe_chunks=8, chunk_b64_chars=384,
            delay_seconds=1.0, start_metadata=tool.GOLDEN_START_METADATA,
            cpu_temp_text=tool.GOLDEN_CPU_TEMP, current_timestamp=tool.GOLDEN_TIMESTAMP,
            sleep_fn=clk.sleep, clock=clk, media_key=KEY)
        golden = read(os.path.join(REV5, "wire_keyed_complete.txt")).splitlines(keepends=True)
        rev3 = read(os.path.join(VEC, "wire_complete.txt")).splitlines(keepends=True)
        self.assertEqual(len(wire), len(golden))
        self.assertEqual(wire[:-1], golden[:-1], "START + keyed chunks + keyed repeat must match the backend vector")
        self.assertEqual(golden[-1], rev3[-1], "END is unchanged in rev 5 (P4)")
        self.assertTrue(wire[-1].startswith(b"<END IMG> filename: 2026-09-20T06-10-00Z_video_5s.h264, fmt: h264"))
        self.assertIn(b"length: 126, key=0dhnso, fmt=h264", wire[0])
        self.assertEqual(max(len(x) for x in wire), 398)

    def test_key_off_is_rev3(self):
        clk, wire = Clock(), []
        transmit_video_clip(
            wire.append, CycleBudget(600, 1.0, clock=clk), payload=PAYLOAD, file_name=tool.GOLDEN_FILENAME,
            fps=10, dur=5.0, res="480x270", crop="na", crf=40, keyframe_chunks=8, chunk_b64_chars=384,
            delay_seconds=1.0, start_metadata=tool.GOLDEN_START_METADATA,
            cpu_temp_text=tool.GOLDEN_CPU_TEMP, current_timestamp=tool.GOLDEN_TIMESTAMP,
            sleep_fn=clk.sleep, clock=clk)
        rev3 = read(os.path.join(VEC, "wire_complete.txt")).splitlines(keepends=True)
        self.assertEqual(wire[:-1], rev3[:-1])


class TestStillsKeyed(unittest.TestCase):
    def send(self, **kw):
        clk, wire = Clock(), []
        transmit_progressive_image(
            wire.append, CycleBudget(600, 1.0, clock=clk), jpeg_data=b"\xff\xd8" + b"x" * 1000,
            compressed_file_name="2026-09-20T06:10:00Z_image_compressed.jpg", quality=60, enc_attempts=1,
            fits=True, chunk_b64_chars=384, delay_seconds=1.0, current_timestamp="2026-09-20T06:10:04Z",
            sleep_fn=clk.sleep, clock=clk, **kw)
        return wire

    def test_keyed_and_exclusive(self):
        wire = self.send(media_key=KEY)
        self.assertIn(b"length: 4, key=0dhnso, fmt=pjpg", wire[0])
        self.assertTrue(all(x.startswith(b"<I0dhnso.%d>" % i) for i, x in enumerate(wire[1:-1])))
        self.assertNotIn(b"key", wire[-1])
        legacy = self.send()
        self.assertTrue(legacy[1].startswith(b"<I0>") and b"key=" not in legacy[0])
        with self.assertRaises(ValueError):
            self.send(media_key="0DHNSO")


class TestSentRecord(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.cfg = {"enabled": True, "retain_days": 3.0, "source": "test",
                    "sent_dir": os.path.join(self.d.name, "sent"),
                    "state_path": os.path.join(self.d.name, "last.txt")}
        self.gate = {"source_time": "spotter", "utc_time": T_KEY.isoformat()}

    def prep(self, gate, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return mk.prepare_keyed_send({"media_key_cfg": self.cfg}, gate_info=gate, daemon=None, **kw)

    def test_video_record(self):
        key = self.prep(self.gate, stem="2026-09-20T06-10-00Z_video_5s", fmt="h264",
                        filename="2026-09-20T06-10-00Z_video_5s.h264", payload=PAYLOAD, chunk_b64_chars=384)
        self.assertEqual(key, KEY)
        rec = mk.find_sent_record(self.cfg["sent_dir"], KEY)
        self.assertEqual((rec["fmt"], rec["msgs"], rec["chunk_b64_chars"]), ("h264", 126, 384))
        self.assertEqual(rec["sha256"], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(read(rec["payload"]), PAYLOAD, "the exact chunked bytes are kept")

    def test_image_record_points_at_the_jpeg(self):
        jpg = os.path.join(self.d.name, "x.jpg")
        with open(jpg, "wb") as fh:
            fh.write(b"\xff\xd8jpeg")
        self.prep(self.gate, stem="x", fmt="pjpg", filename="x.jpg", payload=b"\xff\xd8jpeg",
                  chunk_b64_chars=384, payload_path=jpg)
        self.assertEqual(sorted(os.listdir(self.cfg["sent_dir"])), ["x.sent.json"], "images keep no copy")
        self.assertEqual(mk.find_sent_record(self.cfg["sent_dir"], KEY)["payload"], jpg)

    def test_no_spotter_time_no_key_no_record(self):
        key = self.prep({"source_time": "system"}, stem="s", fmt="h264", filename="s.h264",
                        payload=PAYLOAD, chunk_b64_chars=384)
        self.assertIsNone(key)
        self.assertEqual(os.listdir(self.cfg["sent_dir"]) if os.path.isdir(self.cfg["sent_dir"]) else [], [])

    def test_disabled_touches_nothing(self):
        self.cfg["enabled"] = False
        self.assertIsNone(self.prep(self.gate, stem="s", fmt="h264", filename="s.h264", payload=PAYLOAD,
                                    chunk_b64_chars=384))
        self.assertFalse(os.path.exists(self.cfg["sent_dir"]))

    def test_prune(self):
        sd = self.cfg["sent_dir"]
        os.makedirs(sd)
        now = time.time()
        for name, age_d in (("old.sent", 4), ("old.sent.json", 4), ("new.sent.json", 1), ("keep.txt", 99)):
            p = os.path.join(sd, name)
            open(p, "w").close()
            os.utime(p, (now - age_d * 86400, now - age_d * 86400))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mk.prune_sent(sd, 3, now_ts=now), 2)
            self.assertEqual(mk.prune_sent(sd, 999, now_ts=now + 40 * 86400), 1, "hard cap 30 d")
        self.assertEqual(sorted(os.listdir(sd)), ["keep.txt"])


class TestVideoCycleKeyed(unittest.TestCase):
    def run_vtx(self, gate_info):
        tmp = tempfile.mkdtemp()
        clk, wire = Clock(), []
        s = {"config_path": "x.yaml", "budget_seconds": 600, "pacing_delay_seconds": 1.3,
             "pacing_chunk_b64_chars": 384, "timezone": "UTC", "enforce_time_window": False,
             "capture_backend": "auto", "power_halt_enabled": False, "power_halt_dry_run": True,
             "power_halt_mode": "halt", "power_halt_script_path": "/x", "transmit_phase_cfg": {},
             "media_key_cfg": {"enabled": True, "retain_days": 3.0, "source": "test",
                               "sent_dir": os.path.join(tmp, "sent"), "state_path": os.path.join(tmp, "last")},
             "video": {"dir": tmp, "storage": {}, "clip_minutes": 5.0,
                       "geometry": {"output_wh": (1920, 1080), "fps": 15, "crop_native_xywh": (0, 0, 4608, 2592)}}}
        cfg = vtx.validate_video_tx_config(dict(vtx.DEFAULT_VIDEO_TX_CONFIG, enabled=True, source="test"))

        def fit(src, work, **kw):
            return {"payload": PAYLOAD, "bytes": len(PAYLOAD), "msgs": 126, "budget_msgs": kw["budget_msgs"],
                    "used_pct": 99.3, "target_kbps": 55.7, "pass2_tries": 2, "frames_trimmed": 0, "frames": 50,
                    "duration_s": 5.0, "keyframe_end": 2236, "prescale_s": 1, "encode_s": 1}

        with contextlib.redirect_stdout(io.StringIO()):
            summary = vtx.run_video_tx_cycle(
                s, cfg, transmit=True, gate_fn=lambda p: (True, gate_info),
                record_fn=lambda *a, **k: {"ok": True, "stage": "done", "bytes": 1,
                                           "basename": "2026-09-20T06-10-00Z_video_1920x1080_15fps",
                                           "mp4": os.path.join(tmp, "c.mp4")},
                fit_fn=fit, tx_open_fn=lambda p: wire.append, bm_close_fn=lambda: None,
                halt_fn=lambda **kw: {"action": "recorded"}, ensure_room_fn=lambda d, st: {"paused": False},
                sleep_fn=clk.sleep, clock=clk, encoder_binary="/x", ffmpeg_binary="/x")
        return summary, wire, s["media_key_cfg"]["sent_dir"]

    def test_spotter_read_keys_the_clip(self):
        summary, wire, sent = self.run_vtx({"reason": "t", "source_time": "spotter", "utc_time": T_KEY.isoformat()})
        self.assertIsNone(summary["error"])
        self.assertEqual(summary["media_key"], KEY)
        self.assertIn(b"key=0dhnso", wire[0])
        self.assertTrue(wire[1].startswith(b"<I0dhnso.0>"))
        self.assertEqual(mk.find_sent_record(sent, KEY)["filename"], "2026-09-20T06-10-00Z_video_5s.h264")

    def test_no_spotter_time_sends_legacy(self):
        summary, wire, sent = self.run_vtx({"reason": "t", "source_time": "system"})
        self.assertIsNone(summary["error"])
        self.assertNotIn("media_key", summary)
        self.assertNotIn(b"key=", wire[0])
        self.assertTrue(wire[1].startswith(b"<I0>"))


if __name__ == "__main__":
    unittest.main()
