#!/usr/bin/env python3
# filename: test_rc_uplink_messages.py
# description: Sprint08 P4 — emitted-string asserts for M4 (rc_uplink_messages).
"""
Sprint08 P4 — tests for the RC uplink message builders.

Pins the exact wire strings (backend handoff contract), the payload budgets
under hostile inputs, the never-drop guarantee for RC fields, and — as the
off-repo stand-in for the backend parser — a probe-style key/value
extraction proving every new field parses back out of every message.
(Actual backend parsing lands in the separate nereus-vision-dev/backend PR.)

Run (repo root; serial stubbed, no UART):
  python3 -m unittest tests.test_rc_uplink_messages -v
  # or: python3 tests/test_rc_uplink_messages.py
"""

import os
import re
import sys
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

from rc_uplink_messages import (  # noqa: E402
    build_rc_end_message,
    build_rc_incomplete_message,
    build_rc_start_message,
    reason_code,
)

FILENAME = "2026-07-25T12:00:00Z_image_compressed.jpg"
TIMESTAMP = "2026-07-25T12:03:00Z"


def probe_extract(message):
    """Backend-probe-style key/value extraction: k=v and 'k: v' pairs."""
    pairs = {}
    for k, v in re.findall(r"(\w+)=([^\s>,]+)", message):
        pairs[k] = v
    for k, v in re.findall(r"(\w+): ([^,\n>]+)", message):
        pairs.setdefault(k, v.strip())
    return pairs


class TestStartMessage(unittest.TestCase):
    def test_exact_string_complete(self):
        msg = build_rc_start_message(
            FILENAME, TIMESTAMP, 75,
            quality=13, enc_attempts=1, complete=True,
        )
        self.assertEqual(
            msg,
            f"<START IMG> filename: {FILENAME}, timestamp: {TIMESTAMP}, "
            "length: 75, fmt=pjpg, q=13, att=1, cmp=1\n",
        )

    def test_exact_string_incomplete_carries_reason(self):
        msg = build_rc_start_message(
            FILENAME, TIMESTAMP, 37,
            quality=9, enc_attempts=4, complete=False, reason="budget",
        )
        self.assertEqual(
            msg,
            f"<START IMG> filename: {FILENAME}, timestamp: {TIMESTAMP}, "
            "length: 37, fmt=pjpg, q=9, att=4, cmp=0, rsn=budget\n",
        )

    def test_complete_has_no_reason_key(self):
        msg = build_rc_start_message(
            FILENAME, TIMESTAMP, 75,
            quality=13, enc_attempts=2, complete=True, reason="budget",
        )
        self.assertNotIn("rsn=", msg)

    def test_metadata_rides_along_and_q_never_duplicates(self):
        metadata = {
            "image_res_key": "1000x562",
            "image_quality": 20,  # HEIC-style q must NOT appear — RC q owns the key
            "timezone": "America/Los_Angeles",
            "software_sha": "abc123def456",
            "hostname": "bmcam000",
            "sd_free_bytes": 10 * 1024 * 1024 * 1024,
        }
        msg = build_rc_start_message(
            FILENAME, TIMESTAMP, 75,
            quality=13, enc_attempts=1, complete=True, start_metadata=metadata,
        )
        self.assertEqual(msg.count("q="), 1)
        self.assertIn("q=13", msg)
        self.assertIn("rk=1000x562", msg)
        self.assertIn("sha=abc123def456", msg)
        self.assertIn("sf=10240", msg)
        self.assertLessEqual(len(msg.encode("ascii")), 285)

    def test_budget_held_and_rc_fields_survive_drops(self):
        # Hostile: long filename + full storage metadata forces drops; the
        # RC fields and base fields must survive, budget must hold.
        metadata = {
            "image_res_key": "1000x562",
            "timezone": "America/Argentina/ComodRivadavia",
            "software_sha": "abc123def456",
            "hostname": "bmcam000-bench-unit-long-name",
            "window_start": "08:00",
            "window_end": "15:00",
            "sd_total_bytes": 31 * 1024**3,
            "sd_used_bytes": 12 * 1024**3,
            "sd_free_bytes": 19 * 1024**3,
            "sd_used_pct": 38.7,
            "images_dir_bytes": 900 * 1024**2,
            "buffer_dir_bytes": 42 * 1024,
            "cron_logs_dir_bytes": 77 * 1024,
            "zero_byte_heic_count": 0,
        }
        long_name = "X" * 90 + ".jpg"
        msg = build_rc_start_message(
            long_name, TIMESTAMP, 188,
            quality=15, enc_attempts=1, complete=True, start_metadata=metadata,
        )
        self.assertLessEqual(len(msg.encode("ascii")), 285)
        for required in ("fmt=pjpg", "q=15", "att=1", "cmp=1", "length: 188"):
            self.assertIn(required, msg)
        # Lowest-value keys drop first (lg is first in the drop order).
        self.assertNotIn("lg=", msg)

    def test_ascii_only(self):
        msg = build_rc_start_message(
            "imagé_compressed.jpg", TIMESTAMP, 10,
            quality=11, enc_attempts=3, complete=True,
        )
        msg.encode("ascii")  # must not raise
        self.assertTrue(msg.startswith("<START IMG> "))


class TestEndMessage(unittest.TestCase):
    """P4 revision: END carries NO RC fields — it must be byte-identical to
    the HEIC END so the camera-metadata headroom is unchanged."""

    def test_exact_string(self):
        msg = build_rc_end_message(
            FILENAME,
            uart_duration_sec=632.4,
            sent_buffers=75,
            cpu_temp_text="51.2",
        )
        self.assertEqual(
            msg,
            f"<END IMG> filename: {FILENAME}, uart_duration_sec: 632.4, "
            "sent_buffers: 75, cpu_temp_c: 51.2\n",
        )

    def test_byte_identical_to_heic_end(self):
        from process_image_v2 import _build_end_image_message

        meta = {"ExposureTime": 39994, "AnalogueGain": 3.98, "Lux": 402.1}
        rc = build_rc_end_message(
            FILENAME, uart_duration_sec=632.4, sent_buffers=75,
            cpu_temp_text="51.2", capture_metadata=meta,
        )
        heic = _build_end_image_message(
            FILENAME,
            [
                ("filename", FILENAME),
                ("uart_duration_sec", "632.4"),
                ("sent_buffers", 75),
                ("cpu_temp_c", "51.2"),
            ],
            capture_metadata=meta,
        )
        self.assertEqual(rc, heic)

    def test_no_rc_fields_in_end(self):
        msg = build_rc_end_message(
            FILENAME, uart_duration_sec=190.0, sent_buffers=37, cpu_temp_text="na",
        )
        for absent in ("fmt", "att", "cmp", "rsn"):
            self.assertNotIn(f"{absent}:", msg)
            self.assertNotIn(f"{absent}=", msg)

    def test_camera_metadata_still_budgeted(self):
        capture_metadata = {
            "ExposureTime": 39994,
            "AnalogueGain": 3.98,
            "DigitalGain": 1.19,
            "ColourGains": [1.83, 2.11],
            "ColourTemperature": 4650,
            "LensPosition": 0.47,
            "AfState": 2,
            "AfMode": 0,
            "FocusFoM": 12345,
            "Lux": 402.1,
            "FrameDuration": 40000,
            "SensorTemperature": 41.0,
            "requested_focus_mode": "manual",
            "requested_lens_position": 0.47,
            "requested_white_balance_mode": "custom",
            "requested_colour_gains": [1.83, 2.11],
            "requested_exposure_mode": "manual",
            "requested_shutter_us": 39994,
            "requested_analogue_gain": 3.98,
        }
        msg = build_rc_end_message(
            FILENAME,
            uart_duration_sec=632.4,
            sent_buffers=75,
            cpu_temp_text="51.2",
            capture_metadata=capture_metadata,
        )
        self.assertLessEqual(len(msg.encode("ascii")), 295)
        # Camera metadata rides with production headroom (RC fields removed);
        # with this hostile 19-field set the tail drops exactly as the HEIC
        # END would (budget logic is the same shared function).
        self.assertIn("et_us: 39994", msg)
        self.assertIn("rfm: manual", msg)


class TestIncompleteMessage(unittest.TestCase):
    def test_exact_string(self):
        msg = build_rc_incomplete_message(
            quality=9, enc_attempts=4, reason="budget",
            planned_msgs=128, send_msgs=37,
            cpu_temp_text="49.8", software_sha="abc123def456", hostname="bmcam000",
        )
        self.assertEqual(
            msg,
            "<WS v=1 a=inc fmt=pjpg q=9 att=4 rsn=budget pln=128 snd=37 "
            "ct=49.8 sha=abc123def456 hn=bmcam000>\n",
        )

    def test_optional_telemetry_omitted_when_none(self):
        msg = build_rc_incomplete_message(
            quality=9, enc_attempts=4, reason="cap", planned_msgs=207, send_msgs=195,
        )
        self.assertEqual(
            msg, "<WS v=1 a=inc fmt=pjpg q=9 att=4 rsn=cap pln=207 snd=195>\n"
        )

    def test_zero_send_allowed(self):
        # Budget died before any chunk could go: snd=0 is a valid, parseable state.
        msg = build_rc_incomplete_message(
            quality=None, enc_attempts=0, reason="enc", planned_msgs=None, send_msgs=0,
        )
        self.assertIn("a=inc", msg)
        self.assertIn("att=0", msg)
        self.assertIn("snd=0", msg)
        self.assertNotIn("q=", msg.replace("pjpg", ""))  # no quality when none encoded
        self.assertLessEqual(len(msg.encode("ascii")), 280)


class TestReasonCodes(unittest.TestCase):
    def test_selector_reasons_map(self):
        self.assertEqual(reason_code("no_fit_budget"), "budget")
        self.assertEqual(reason_code("no_fit_cap"), "cap")
        self.assertEqual(reason_code("no_time_for_encode"), "enc")

    def test_unknown_maps_to_err_never_raises(self):
        self.assertEqual(reason_code("something_new"), "err")
        self.assertEqual(reason_code(None), "err")


class TestBackendProbeParseability(unittest.TestCase):
    """Stand-in for the separate backend PR: every new field must extract with
    plain probe-style key/value parsing from every message type."""

    def test_start_fields_parse_back(self):
        msg = build_rc_start_message(
            FILENAME, TIMESTAMP, 37,
            quality=9, enc_attempts=4, complete=False, reason="budget",
            start_metadata={"image_res_key": "1000x562", "software_sha": "abc123def456"},
        )
        got = probe_extract(msg)
        self.assertEqual(got["fmt"], "pjpg")
        self.assertEqual(got["q"], "9")
        self.assertEqual(got["att"], "4")
        self.assertEqual(got["cmp"], "0")
        self.assertEqual(got["rsn"], "budget")
        self.assertEqual(got["length"], "37")

    def test_end_fields_parse_back(self):
        # END is HEIC-identical; the backend reads actual-vs-planned from
        # sent_buffers here vs length in START.
        msg = build_rc_end_message(
            FILENAME, uart_duration_sec=190.0, sent_buffers=37, cpu_temp_text="na",
        )
        got = probe_extract(msg)
        self.assertEqual(got["sent_buffers"], "37")
        self.assertNotIn("fmt", got)

    def test_incomplete_fields_parse_back(self):
        msg = build_rc_incomplete_message(
            quality=9, enc_attempts=4, reason="budget", planned_msgs=128, send_msgs=37,
        )
        got = probe_extract(msg)
        self.assertEqual(got["a"], "inc")
        self.assertEqual(got["fmt"], "pjpg")
        self.assertEqual(got["q"], "9")
        self.assertEqual(got["att"], "4")
        self.assertEqual(got["rsn"], "budget")
        self.assertEqual(got["pln"], "128")
        self.assertEqual(got["snd"], "37")


if __name__ == "__main__":
    unittest.main(verbosity=2)
