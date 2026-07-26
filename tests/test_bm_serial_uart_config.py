#!/usr/bin/env python3
# filename: test_bm_serial_uart_config.py
# description: Off-device tests for Sprint09 §1 — BristlemouthSerial uart_port/baudrate from YAML.
"""
Sprint09 §1 off-device tests.

Covers bm_serial.load_uart_config(), the constructor's replacement for the
hardcoded serial.Serial('/dev/ttyAMA0', 115200):
  - repo camera_schedule.yaml and every device profile resolve to the
    committed values (/dev/ttyAMA0 @ 115200 — no behavior change)
  - missing file / missing keys / invalid values fall back to the old
    hardcoded defaults
  - explicit alternate values are honored

Run (repo root, works without pyserial — serial is stubbed):
  python3 -m unittest tests.test_bm_serial_uart_config -v

Assumptions: PyYAML importable (bm_serial degrades to defaults without it,
which would vacuously pass the fallback tests but skip the parse tests).
"""

import os
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_DIR = os.path.join(REPO_ROOT, "BM_Devel_Pi")
REPO_YAML = os.path.join(PI_DIR, "camera_schedule.yaml")
PROFILE_YAMLS = [
    os.path.join(REPO_ROOT, "device_profiles", name, "camera_schedule.yaml")
    for name in ("bmcam000", "bmcam001", "bmcam002", "rc_field_template")
]

# Off-device (Mac) pyserial may be absent; stub it (same pattern as
# test_rc_progressive_config.py). The stub raises on actual UART open.
try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

sys.path.insert(0, PI_DIR)

import bm_serial  # noqa: E402

DEFAULTS = (bm_serial.DEFAULT_UART_PORT, bm_serial.DEFAULT_UART_BAUDRATE)


def _write_yaml(text):
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    f.write(text)
    f.close()
    return f.name


class TestCommittedYamls(unittest.TestCase):
    """Repo YAML + all device profiles resolve to the pre-change constants."""

    def test_repo_yaml(self):
        self.assertEqual(bm_serial.load_uart_config(REPO_YAML), DEFAULTS)

    def test_device_profiles(self):
        for path in PROFILE_YAMLS:
            with self.subTest(profile=path):
                self.assertTrue(os.path.exists(path), path)
                self.assertEqual(bm_serial.load_uart_config(path), DEFAULTS)


class TestFallbacks(unittest.TestCase):
    """Anything missing or invalid must behave like the old hardcoded open."""

    def test_missing_file(self):
        self.assertEqual(
            bm_serial.load_uart_config("/nonexistent/camera_schedule.yaml"),
            DEFAULTS,
        )

    def test_missing_keys(self):
        path = _write_yaml("timezone: America/Los_Angeles\n")
        try:
            self.assertEqual(bm_serial.load_uart_config(path), DEFAULTS)
        finally:
            os.unlink(path)

    def test_invalid_values(self):
        path = _write_yaml('uart_port: ""\nbaudrate: -5\n')
        try:
            self.assertEqual(bm_serial.load_uart_config(path), DEFAULTS)
        finally:
            os.unlink(path)

    def test_unparsable_baudrate(self):
        path = _write_yaml("baudrate: not-a-number\n")
        try:
            self.assertEqual(bm_serial.load_uart_config(path), DEFAULTS)
        finally:
            os.unlink(path)


@unittest.skipIf(bm_serial.yaml is None, "PyYAML unavailable off-device")
class TestExplicitValues(unittest.TestCase):
    """Alternate YAML values are honored (the point of the change)."""

    def test_alternate_port_and_baud(self):
        path = _write_yaml('uart_port: /dev/ttyS0\nbaudrate: "57600"\n')
        try:
            self.assertEqual(
                bm_serial.load_uart_config(path), ("/dev/ttyS0", 57600)
            )
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
