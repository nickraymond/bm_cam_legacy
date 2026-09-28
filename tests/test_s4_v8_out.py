#!/usr/bin/env python3
# filename: test_s4_v8_out.py
# description: Sprint26 S4 c.2 — the supervisor's v9 path never loads the v8 settings bindings or the v8 help renderer.
"""
Sprint26 S4 commit c.2 (PLAN_S4.md G1, R24).

In a fresh interpreter: import every module the supervisor's v9 path uses,
service a pending trigger through rc_command_hooks (the path every action
takes), answer a v9 `help`, and build the daemon through the production
factory with a V9State. `command_bindings` and `command_help` must never be
loaded (`command_tables` still is: the v8 fold and the trigger table; the
v8 files go with the legacy runtime after S5). The legacy path still loads
them (control).

Run (repo root):
  python3 -m unittest tests.test_s4_v8_out -v
"""

import os
import subprocess
import sys
import textwrap
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO_ROOT, "BM_Devel_Pi")

V9_PATH = textwrap.dedent('''
    import json, os, sys, tempfile
    from unittest import mock
    sys.path.insert(0, APP)
    import rc_progressive_jpeg, rc_supervisor, rc_video_tx, rc_heal, rc_command_hooks
    import command_v9, command_state_v9, command_replies, supervisor_config
    import config_registry as R
    d = tempfile.mkdtemp()
    path = os.path.join(d, "bm_command_state_v2.json")
    st = command_state_v9.V9State(path)
    st.transaction(lambda s: s.arm_trigger(1000001, 2))
    settings, flags = rc_command_hooks.service_pending_trigger({"x": 1}, st, transmit=True)
    assert settings["trigger"]["id"] == 1000001 and flags["skip_time_window"]
    class Uart:
        timeout = 0.1
    class Bm:
        uart = Uart()
    with mock.patch("bm_port.open_shared", lambda *a, **k: Bm()), \\
            mock.patch.object(rc_command_hooks, "load_uart_config", lambda p: ("/dev/x", 1)):
        daemon = rc_command_hooks.default_daemon_factory(
            {"config_path": "/nonexistent"}, {"topic": "bmcam/cmd"}, st)
    assert daemon.query_render_fn is None
    base = R.defaults(); base.update({"mode.media": "still", "commands.state_path": path})
    daemon.v9 = command_replies.V9Replies("h", lambda: "00000000")
    daemon.v9_dispatch = command_v9.Dispatcher(daemon, st, base)
    daemon._inbound.put(json.dumps({"id": 7, "c": "help"}).encode())
    daemon.process_pending()
    loaded = [m for m in ("command_bindings", "command_help") if m in sys.modules]
    print("LOADED", loaded)
''')

LEGACY = textwrap.dedent('''
    import sys
    sys.path.insert(0, APP)
    import rc_progressive_jpeg, rc_command_hooks
    from command_state import CommandState
    import tempfile, os
    st = CommandState(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    rc_command_hooks.apply_command_overlay({"config_path": "/nonexistent"}, st,
                                           lambda p: None)
    print("LOADED", [m for m in ("command_bindings",) if m in sys.modules])
''')


def run(code):
    r = subprocess.run([sys.executable, "-c", f"APP = {APP!r}\n" + code],
                       capture_output=True, text=True, timeout=120, cwd=REPO_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    return [line for line in r.stdout.splitlines() if line.startswith("LOADED")][-1]


class V8Out(unittest.TestCase):
    def test_v9_path_never_loads_the_v8_bindings(self):
        self.assertEqual(run(V9_PATH), "LOADED []")

    def test_legacy_path_still_does(self):
        self.assertEqual(run(LEGACY), "LOADED ['command_bindings']")


if __name__ == "__main__":
    unittest.main()
