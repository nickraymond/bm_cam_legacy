#!/usr/bin/env python3
# filename: test_network_config.py
# description: Sprint16 — network island parsing + boot-default dispatch.
"""
Sprint16 network island tests (D-S16-3/4), no hardware and no network
side effects — subprocess dispatch is injected/mocked everywhere.

Covers:
  - island parsing: valid values, defaults filled, absent island -> None
    (pre-Sprint16 no-op contract), loud failures on nonsense
  - apply_boot_default: exact argv, None no-op, Popen failure non-fatal
  - the GUI join helper (_default_join): PSK travels via a 0600 file in
    nmcli passwd-file format and NEVER appears in any argv element

Run: python3 -m unittest tests.test_network_config -v
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_DIR = os.path.join(REPO_ROOT, "BM_Devel_Pi")
if PI_DIR not in sys.path:
    sys.path.insert(0, PI_DIR)

import network_config as nc  # noqa: E402


def write_yaml(dirname, body):
    path = os.path.join(dirname, "camera_schedule.yaml")
    with open(path, "w") as f:
        f.write(body)
    return path


BASE = "capture_mode: \"video\"\n"


class TestLoadNetworkConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_absent_island_is_none(self):
        path = write_yaml(self.tmp.name, BASE)
        self.assertIsNone(nc.load_network_config(path))

    def test_valid_island_with_defaults_filled(self):
        path = write_yaml(self.tmp.name,
                          BASE + "network:\n  default: ap\n")
        cfg = nc.load_network_config(path)
        self.assertEqual(cfg["default"], "ap")
        self.assertEqual(cfg["ap_fallback_s"], 90)
        self.assertEqual(cfg["ap_timeout_min"], 60)

    def test_full_island(self):
        path = write_yaml(self.tmp.name, BASE + (
            "network:\n  default: nereus_hq\n  ap_fallback_s: 120\n"
            "  ap_timeout_min: 30\n"))
        cfg = nc.load_network_config(path)
        self.assertEqual(cfg, {"default": "nereus_hq",
                               "ap_fallback_s": 120,
                               "ap_timeout_min": 30})

    def test_bad_default_raises(self):
        path = write_yaml(self.tmp.name,
                          BASE + "network:\n  default: hotspot\n")
        with self.assertRaises(ValueError):
            nc.load_network_config(path)

    def test_missing_default_raises(self):
        # A present island MUST say what the default is — that is its job.
        path = write_yaml(self.tmp.name,
                          BASE + "network:\n  ap_fallback_s: 90\n")
        with self.assertRaises(ValueError):
            nc.load_network_config(path)

    def test_out_of_range_values_raise(self):
        for island in ("network:\n  default: ap\n  ap_fallback_s: 3\n",
                       "network:\n  default: ap\n  ap_fallback_s: 9999\n",
                       "network:\n  default: ap\n  ap_timeout_min: 0\n",
                       "network:\n  default: ap\n  ap_timeout_min: fast\n"):
            path = write_yaml(self.tmp.name, BASE + island)
            with self.assertRaises(ValueError, msg=island):
                nc.load_network_config(path)

    def test_non_mapping_island_raises(self):
        path = write_yaml(self.tmp.name, BASE + "network: ap\n")
        with self.assertRaises(ValueError):
            nc.load_network_config(path)

    def test_print_settings_never_raises(self):
        with contextlib.redirect_stdout(io.StringIO()):
            nc.print_network_settings(None)
            nc.print_network_settings({"default": "ap", "ap_fallback_s": 90,
                                       "ap_timeout_min": 60})


class TestApplyBootDefault(unittest.TestCase):
    def test_none_is_noop(self):
        popen = mock.Mock()
        self.assertIsNone(nc.apply_boot_default(None, popen_fn=popen))
        popen.assert_not_called()

    def test_argv_exact(self):
        popen = mock.Mock()
        cfg = {"default": "nereus_hq", "ap_fallback_s": 90,
               "ap_timeout_min": 60}
        with contextlib.redirect_stdout(io.StringIO()):
            argv = nc.apply_boot_default(cfg, script_path="/x/net.sh",
                                         popen_fn=popen)
        self.assertEqual(argv, ["sudo", "-n", "/x/net.sh", "default",
                                "nereus_hq", "90"])
        popen.assert_called_once_with(argv)

    def test_popen_failure_is_loud_but_nonfatal(self):
        def boom(argv):
            raise OSError("no sudo")
        cfg = {"default": "ap", "ap_fallback_s": 90, "ap_timeout_min": 60}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            nc.apply_boot_default(cfg, popen_fn=boom)  # must not raise
        self.assertIn("WARN", out.getvalue())


class TestJoinPskHygiene(unittest.TestCase):
    """The customer PSK must never ride argv (D-S16-1/D-S16-4)."""

    def test_psk_in_file_never_argv(self):
        import videoui_server
        psk = "sup3r-secret-psk"
        with mock.patch.object(videoui_server.subprocess, "Popen") as popen:
            videoui_server._default_join("CustomerNet", psk,
                                         script_path="/x/net.sh")
        argv = popen.call_args.args[0]
        for element in argv:
            self.assertNotIn(psk, element)
        self.assertIn("CustomerNet", argv)
        # The last argv element is the psk file path: right format, 0600.
        psk_path = argv[-1]
        try:
            with open(psk_path) as f:
                content = f.read()
            self.assertEqual(content,
                             f"802-11-wireless-security.psk:{psk}\n")
            self.assertEqual(os.stat(psk_path).st_mode & 0o777, 0o600)
        finally:
            os.unlink(psk_path)


if __name__ == "__main__":
    unittest.main()
