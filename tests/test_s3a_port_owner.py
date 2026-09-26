#!/usr/bin/env python3
# filename: test_s3a_port_owner.py
# description: Sprint26 S3a.3 — the one port accessor refuses a reopen/second descriptor; PortOwner order.
"""
Sprint26 S3a.3 (DESIGN_supervisor.md §4 "Port", PLAN_S3a.md S3a.3).

Pins:
  - bm_port.get() after close() is REFUSED (the review BLOCKER: a silent lazy
    reopen made a second, timeout-less descriptor); new_session() re-allows it.
  - a second concurrent descriptor is refused: open_shared()/install() while a
    handle is held, private_read() while a handle is held.
  - private_read() with nothing held opens and closes its own descriptor.
  - PortOwner.finish(): shutdown -> close -> halt, halt never skipped by a close
    failure; start_daemon() keeps the daemon even when start() fails.
  - a refused port session in the video cycle still halts.

No hardware: serial.Serial is replaced by a recorder.

Run (repo root):
  python3 -m unittest tests.test_s3a_port_owner -v
"""

import os
import sys
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    stub = types.ModuleType("serial")
    stub.Serial = lambda *a, **k: None
    sys.modules["serial"] = stub

import bm_port  # noqa: E402
import rc_port_owner  # noqa: E402
import rc_video_tx as vtx  # noqa: E402


class FakeUart:
    opened = []

    def __init__(self, port=None, baudrate=None, timeout=None, **_kw):
        self.port, self.timeout, self.is_open = port, timeout, True
        FakeUart.opened.append(self)

    def close(self):
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeBm:
    def __init__(self, uart=None, **_kw):
        self.uart = uart or FakeUart("/dev/lazy")


class PortGuardTests(unittest.TestCase):
    def setUp(self):
        FakeUart.opened = []
        bm_port._bm, bm_port._closed, bm_port._shared = None, False, False
        self.patches = [mock.patch.object(bm_port.serial, "Serial", FakeUart),
                        mock.patch.object(bm_port, "BristlemouthSerial", FakeBm)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        bm_port._bm, bm_port._closed, bm_port._shared = None, False, False

    def test_lazy_get_opens_once_then_reopen_after_close_is_refused(self):
        a = bm_port.get()
        self.assertIs(bm_port.get(), a)
        bm_port.close()
        self.assertFalse(a.uart.is_open)
        with self.assertRaises(bm_port.PortRefused):
            bm_port.get()
        self.assertEqual(len(FakeUart.opened), 1)

    def test_new_session_allows_the_next_runtime_to_open(self):
        bm_port.get()
        bm_port.close()
        bm_port.new_session()
        bm_port.get()
        self.assertEqual(len(FakeUart.opened), 2)

    def test_new_session_closes_a_lazy_port_opened_before_it(self):
        # debug_print with BM_CAMERA_LOG_TO_SPOTTER=1 can open lazily before the
        # owner starts; that handle is closed (it used to leak), never fatal.
        lazy = bm_port.get()
        bm_port.new_session()
        self.assertFalse(lazy.uart.is_open)
        self.assertIsNone(bm_port.current())

    def test_new_session_refused_while_the_shared_port_is_open(self):
        bm_port.open_shared("/dev/ttyAMA0", 115200, timeout=0.1)
        with self.assertRaises(bm_port.PortRefused):
            bm_port.new_session()

    def test_shared_open_replaces_a_lazy_handle(self):
        lazy = bm_port.get()
        bm = bm_port.open_shared("/dev/ttyAMA0", 115200, timeout=0.1)
        self.assertFalse(lazy.uart.is_open)
        self.assertIs(bm_port.current(), bm)
        self.assertEqual(sum(u.is_open for u in FakeUart.opened), 1)

    def test_second_shared_open_refused(self):
        bm = bm_port.open_shared("/dev/ttyAMA0", 115200, timeout=0.1)
        self.assertEqual(bm.uart.timeout, 0.1)
        self.assertIs(bm_port.get(), bm)        # the lazy path reuses the shared port
        with self.assertRaises(bm_port.PortRefused):
            bm_port.open_shared("/dev/ttyAMA0", 115200, timeout=0.1)
        with self.assertRaises(bm_port.PortRefused):
            bm_port.install(FakeBm(uart=types.SimpleNamespace()))
        bm_port.install(bm)                     # the same handle again is harmless
        self.assertEqual(len(FakeUart.opened), 1)

    def test_shared_open_after_close_refused(self):
        bm_port.close()
        with self.assertRaises(bm_port.PortRefused):
            bm_port.open_shared("/dev/ttyAMA0", 115200, timeout=0.1)

    def test_private_read_opens_and_closes_its_own_descriptor(self):
        with bm_port.private_read("/dev/ttyAMA0", 115200, 0.1) as ser:
            self.assertTrue(ser.is_open)
        self.assertFalse(ser.is_open)
        self.assertIsNone(bm_port.current())
        bm_port.get()                            # commands off: gate read, then the lazy handle
        self.assertEqual(len(FakeUart.opened), 2)

    def test_private_read_refused_while_the_shared_port_is_held(self):
        bm_port.get()
        with self.assertRaises(bm_port.PortRefused):
            with bm_port.private_read("/dev/ttyAMA0", 115200, 0.1):
                pass
        self.assertEqual(len(FakeUart.opened), 1)


SETTINGS = {"power_halt_enabled": True, "power_halt_dry_run": True,
            "power_halt_mode": "tuned", "power_halt_script_path": "/x"}


class PortOwnerTests(unittest.TestCase):
    def make(self, calls, close_raises=False):
        def close():
            calls.append("close")
            if close_raises:
                raise OSError("boom")

        def halt(**kw):
            calls.append("halt")
            return {"action": "dry_run"}

        return rc_port_owner.PortOwner(SETTINGS, bm_close_fn=close, halt_fn=halt,
                                       clock=lambda: 0.0, sleep_fn=lambda s: None)

    def test_finish_order_is_shutdown_close_halt(self):
        calls, summary = [], {}
        owner = self.make(calls)
        with mock.patch.object(rc_port_owner.cmd_hooks, "shutdown",
                               lambda *a, **k: calls.append("shutdown")):
            owner.finish(summary, close_port=True, close_warn=calls.append)
        self.assertEqual(calls, ["shutdown", "close", "halt"])
        self.assertEqual(summary["halt_result"], {"action": "dry_run"})

    def test_close_failure_still_halts_and_no_close_when_not_asked(self):
        calls = []
        owner = self.make(calls, close_raises=True)
        with mock.patch.object(rc_port_owner.cmd_hooks, "shutdown", lambda *a, **k: None):
            owner.finish({}, close_port=True, close_warn=lambda e: calls.append(f"warn:{e}"))
            self.assertEqual(calls, ["close", "warn:boom", "halt"])
            calls.clear()
            owner.finish({}, close_port=False, close_warn=calls.append)
        self.assertEqual(calls, ["halt"])

    def test_start_daemon_keeps_the_daemon_when_start_fails(self):
        class D:
            def start(self):
                raise OSError("uart gone")
        owner = self.make([])
        d = D()
        with self.assertRaises(OSError):
            owner.start_daemon(lambda *a: d, {}, None)
        self.assertIs(owner.daemon, d)          # finish() will still stop its reader


class VideoCycleRefusedSessionTests(unittest.TestCase):
    def test_refused_session_still_halts(self):
        halts = []
        settings = dict(SETTINGS, budget_seconds=600, pacing_delay_seconds=1.3,
                        config_path="/nonexistent.yaml")
        with mock.patch.object(bm_port, "new_session",
                               side_effect=bm_port.PortRefused("held")):
            summary = vtx.run_video_tx_cycle(
                settings, {}, transmit=True, clock=lambda: 0.0, sleep_fn=lambda s: None,
                halt_fn=lambda **kw: halts.append(kw) or {"action": "dry_run"},
                bm_close_fn=lambda: None)
        self.assertIn("PortRefused", summary["error"])
        self.assertEqual(len(halts), 1)


if __name__ == "__main__":
    unittest.main()
