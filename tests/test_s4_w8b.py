#!/usr/bin/env python3
# filename: test_s4_w8b.py
# description: Sprint26 S4 c.1 (W8b) — START cfg + tg/r/m/d and <WS> cfg/up: worst-case START <= 285 B, core fields never lost, hooks off = byte-identical.
"""
Sprint26 S4 commit c.1 (DESIGN §6.2, §8.2 W8, §9; PLAN_S4.md c.1).

Pins: with the hooks unset both START builders and <WS> are byte-identical
to S4b; with them set, cfg (and tg/r/m/d on a triggered action) are core
START fields right after key; a worst-case still START (90-char filename,
key, cfg, tg, r, m, every optional field, an incomplete send) stays <= 285 B
with cfg kept; a video START that cannot hold tg/m/d sheds them (never
raises, never loses cfg); <WS> carries cfg and up right after `a`.

Run (repo root):
  python3 -m unittest tests.test_s4_w8b -v
"""

import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_telemetry  # noqa: E402
import rc_uplink_messages as U  # noqa: E402

META = {"tz": "America/Los_Angeles", "ws": "10:00", "we": "15:00", "rk": "1000x562",
        "sha": "0123456789ab", "hn": "bmcam003", "st": 119000, "su": 90000, "sf": 29000,
        "sp": 75.6, "im": 12000, "lg": 5000}
EXTRA = [("cfg", "a41c09e2"), ("tg", 4_294_967_295), ("r", "4608x2592+4608+2592"),
         ("m", 2000), ("d", 30.0)]


class Extras(unittest.TestCase):
    def tearDown(self):
        U.START_EXTRA_FN = None
        rc_telemetry.WS_EXTRA_FN = None

    def still(self, name_len=90, **kw):
        return U.build_rc_start_message("x" * name_len, "2026-09-24T21:00:00Z", 999,
                                        quality=95, enc_attempts=99, complete=False,
                                        reason="budget", start_metadata=META, key="abcdef", **kw)

    def video(self, name_len=40):
        return U.build_rc_video_start_message(
            "v" * name_len, "2026-09-24T21:00:00Z", 999, fps=10, dur=30.0, res="480x270",
            crop="4608x2592+4608+2592", br=36, start_metadata=META, key="abcdef")

    def test_off_is_byte_identical(self):
        a = self.still()
        U.START_EXTRA_FN = lambda: []
        self.assertEqual(self.still(), a)

    def test_worst_case_still_start(self):
        U.START_EXTRA_FN = lambda: EXTRA[:4]
        msg = self.still()
        self.assertLessEqual(len(msg.encode()), 285)
        for core in ("key=abcdef", "cfg=a41c09e2", "tg=4294967295", "r=4608x2592+4608+2592",
                     "fmt=pjpg", "cmp=0"):
            self.assertIn(core, msg)
        self.assertLess(msg.index("cfg="), msg.index("fmt="))

    def test_a_capped_filename_still_fits_every_core_field(self):
        U.START_EXTRA_FN = lambda: EXTRA[:4]
        msg = self.still(name_len=160)                      # the builder caps it at 96
        self.assertLessEqual(len(msg.encode()), 285)
        for core in ("cfg=a41c09e2", "tg=4294967295", "m=2000", "rsn=budget"):
            self.assertIn(core, msg)

    def test_video_start_never_raises_on_extras(self):
        U.START_EXTRA_FN = lambda: [EXTRA[0], EXTRA[1], EXTRA[3], EXTRA[4]]
        msg = self.video(name_len=40)
        self.assertLessEqual(len(msg.encode()), 285)
        self.assertIn("d=30.0", msg)
        long = self.video(name_len=96)
        self.assertIn("cfg=a41c09e2", long)
        self.assertLessEqual(len(long.encode()), 285)

    def test_ws_fields_after_a(self):
        sent = []
        rc_telemetry.WS_EXTRA_FN = lambda: [("cfg", "a41c09e2"), ("up", 550)]
        with mock.patch.object(rc_telemetry, "send_compact_text_message", sent.append), \
                mock.patch.object(rc_telemetry, "get_cpu_temperature", lambda: 45.0), \
                mock.patch.object(rc_telemetry, "get_software_sha", lambda: "golden0"), \
                mock.patch.object(rc_telemetry, "get_hostname", lambda: "bmcam003"):
            rc_telemetry.send_wake_status("idle", timezone_name="America/New_York")
        self.assertTrue(sent[0].startswith("<WS v=1 a=idle cfg=a41c09e2 up=550 "))
        rc_telemetry.WS_EXTRA_FN = lambda: 1 / 0             # never fails the heartbeat
        with mock.patch.object(rc_telemetry, "send_compact_text_message", sent.append), \
                mock.patch.object(rc_telemetry, "get_cpu_temperature", lambda: 45.0):
            rc_telemetry.send_wake_status("idle")
        self.assertTrue(sent[1].startswith("<WS v=1 a=idle "))


class ConfigErrors(unittest.TestCase):
    def test_cf_err_lines_go_cellular(self):
        from tests.test_s4_dispatch import Rig
        r = Rig(self)
        n = r.daemon.v9_dispatch.report_errors([
            ("overlay", "still.crop", "does not fit the native frame"),
            ("level", None, "running lkg")])
        self.assertEqual(n, 2)
        self.assertEqual(len(r.daemon._acks), 2)
        self.assertIn("err=overlay k=still.crop", r.daemon._acks[0])
        self.assertIn("err=level", r.daemon._acks[1])
        self.assertIn("CONFIG OVERLAY: still.crop", r.daemon._console[0])
        self.assertEqual(r.daemon.v9_dispatch.report_errors([]), 0)


if __name__ == "__main__":
    unittest.main()
