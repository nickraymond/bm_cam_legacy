#!/usr/bin/env python3
# filename: test_rc_power_halt.py
# description: Sprint08 P6 — dry-run/off-device tests for M6 (rc_power_halt).
"""
Sprint08 P6 — off-device tests for the power-halt wrapper.

Everything runs with an injected fake runner — no sudo, no systemctl, no
halt. The real-halt half of the P6 row happens on bmcam000 with Nick at the
bench (SSH drop = success; recovery = physical power cycle).

Run (repo root; pure stdlib):
  python3 -m unittest tests.test_rc_power_halt -v
  # or: python3 tests/test_rc_power_halt.py
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

from rc_power_halt import (  # noqa: E402
    DEFAULT_HALT_SCRIPT,
    build_halt_command,
    perform_power_halt,
)


class FakeProc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


class RecordingRunner:
    def __init__(self, returncode=0, stderr="", raises=None):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr
        self.raises = raises

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.raises is not None:
            raise self.raises
        return FakeProc(self.returncode, self.stderr)


class RecordingLog:
    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(line)


def existing_script():
    f = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
    f.write("#!/bin/bash\necho halt\n")
    f.close()
    return f.name


class TestCommandConstruction(unittest.TestCase):
    def test_halt_command(self):
        self.assertEqual(
            build_halt_command("/home/pi/BM_Devel_Pi/tuned_halt.sh", "halt"),
            ["sudo", "-n", "/bin/bash", "/home/pi/BM_Devel_Pi/tuned_halt.sh"],
        )

    def test_poweroff_appends_flag(self):
        self.assertEqual(
            build_halt_command("/x.sh", "poweroff"),
            ["sudo", "-n", "/bin/bash", "/x.sh", "--poweroff"],
        )

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            build_halt_command("/x.sh", "reboot")

    def test_default_script_path(self):
        self.assertEqual(DEFAULT_HALT_SCRIPT, "/home/pi/BM_Devel_Pi/tuned_halt.sh")


class TestDisabled(unittest.TestCase):
    def test_disabled_is_noop(self):
        runner, log = RecordingRunner(), RecordingLog()
        result = perform_power_halt(
            enabled=False, dry_run=False, runner=runner, log=log
        )
        self.assertEqual(result["action"], "disabled")
        self.assertEqual(runner.calls, [])
        self.assertTrue(any("skipping halt" in l for l in log.lines))


class TestDryRun(unittest.TestCase):
    def test_dry_run_logs_intent_and_executes_nothing(self):
        path = existing_script()
        try:
            runner, log = RecordingRunner(), RecordingLog()
            result = perform_power_halt(
                enabled=True, dry_run=True, mode="halt",
                script_path=path, runner=runner, log=log,
            )
            self.assertEqual(result["action"], "dry_run")
            self.assertEqual(runner.calls, [])  # NOTHING executed
            self.assertTrue(result["script_exists"])
            joined = "\n".join(log.lines)
            self.assertIn("DRY RUN", joined)
            self.assertIn(f"sudo -n /bin/bash {path}", joined)
        finally:
            os.unlink(path)

    def test_dry_run_warns_when_script_missing(self):
        runner, log = RecordingRunner(), RecordingLog()
        result = perform_power_halt(
            enabled=True, dry_run=True,
            script_path="/nonexistent/tuned_halt.sh", runner=runner, log=log,
        )
        self.assertEqual(result["action"], "dry_run")
        self.assertFalse(result["script_exists"])
        self.assertTrue(any("WARNING" in l and "missing" in l for l in log.lines))
        self.assertEqual(runner.calls, [])


class TestRealRun(unittest.TestCase):
    def test_success_reports_halt_initiated(self):
        path = existing_script()
        try:
            runner, log = RecordingRunner(returncode=0), RecordingLog()
            result = perform_power_halt(
                enabled=True, dry_run=False, mode="poweroff",
                script_path=path, runner=runner, log=log,
            )
            self.assertEqual(result["action"], "halt_initiated")
            self.assertEqual(len(runner.calls), 1)
            cmd, kwargs = runner.calls[0]
            self.assertEqual(cmd, ["sudo", "-n", "/bin/bash", path, "--poweroff"])
            self.assertEqual(kwargs.get("timeout"), 60)
        finally:
            os.unlink(path)

    def test_nonzero_exit_fails_loud_without_raising(self):
        path = existing_script()
        try:
            runner, log = RecordingRunner(
                returncode=1, stderr="sudo: a password is required"
            ), RecordingLog()
            result = perform_power_halt(
                enabled=True, dry_run=False, script_path=path, runner=runner, log=log,
            )
            self.assertEqual(result["action"], "failed")
            self.assertIn("a password is required", result["detail"])
            self.assertTrue(any("ERROR" in l for l in log.lines))
        finally:
            os.unlink(path)

    def test_runner_exception_never_propagates(self):
        path = existing_script()
        try:
            runner = RecordingRunner(raises=RuntimeError("killed during shutdown"))
            log = RecordingLog()
            result = perform_power_halt(
                enabled=True, dry_run=False, script_path=path, runner=runner, log=log,
            )
            self.assertEqual(result["action"], "failed")
            self.assertIn("killed during shutdown", result["detail"])
        finally:
            os.unlink(path)

    def test_missing_script_fails_without_running(self):
        runner, log = RecordingRunner(), RecordingLog()
        result = perform_power_halt(
            enabled=True, dry_run=False,
            script_path="/nonexistent/tuned_halt.sh", runner=runner, log=log,
        )
        self.assertEqual(result["action"], "failed")
        self.assertEqual(runner.calls, [])
        self.assertTrue(any("ERROR" in l for l in log.lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
