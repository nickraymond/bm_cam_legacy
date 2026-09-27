#!/usr/bin/env python3
# filename: rc_port_owner.py
# description: Sprint26 S3a — the port owner: daemon start, then shutdown -> close -> halt.
"""
The one owner of the Spotter UART for a runtime (DESIGN_supervisor.md §4
"Port", REVIEW_20260925.md K3).

Before S3a each cycle body started the command daemon itself and ran
"shutdown -> close the port -> halt" in its own `finally`. Those three steps
now live here, in that order, so the legacy cycles and the supervisor run the
exact same code for them:

  owner = PortOwner(settings, bm_close_fn=..., halt_fn=..., clock=..., sleep_fn=...)
  owner.begin()                                                 # new bm_port session
  owner.start_daemon(factory, bm_commands_cfg, command_state)   # optional
  ... the action ...
  owner.finish(summary, close_port=..., close_warn=...)         # never raises

begin() starts a new bm_port session (bm_port.new_session): from then on a
lazy reopen after close, or a second descriptor, is refused. Constructing an
owner never fails, so finish() (and the halt) is always reachable.

Inputs are injected (clock, sleep_fn, halt_fn, bm_close_fn) so the golden
harness's fakes reach the owner exactly as they reached the cycle bodies.
"""

import bm_port
import rc_command_hooks as cmd_hooks


class PortOwner:
    def __init__(self, settings, *, bm_close_fn, halt_fn, clock, sleep_fn, log_fn=print):
        self.settings = settings
        self.daemon = None
        self._bm_close_fn = bm_close_fn
        self._halt_fn = halt_fn
        self._clock = clock
        self._sleep_fn = sleep_fn
        self._log_fn = log_fn

    def begin(self):
        """Start this runtime's port session (raises bm_port.PortRefused if a
        port from an earlier session is still open)."""
        bm_port.new_session()

    def start_daemon(self, factory, bm_commands_cfg, command_state):
        """Build and start the command daemon (it opens the shared port).
        self.daemon is set BEFORE start(), so a start() failure still gets the
        reader stopped in finish() (the video cycle's behaviour before S3a)."""
        self.daemon = factory(self.settings, bm_commands_cfg, command_state)
        self.daemon.start()
        cmd_hooks.boot_mark("cmd_subscribed")
        return self.daemon

    def finish(self, summary, *, close_port, close_warn, halt=True):
        """Final command pickup + paced ack flush + reader stop -> close the
        shared port (if close_port) -> halt. The halt runs on every path and
        never raises; its result goes into summary["halt_result"].
        halt=False (Sprint26 S3b, a stay_on process stopping on SIGTERM or for
        a restart): everything but the halt."""
        s = self.settings
        cmd_hooks.shutdown(self.daemon, summary, self._log_fn,
                           clock=self._clock, sleep_fn=self._sleep_fn)
        if close_port:
            try:
                self._bm_close_fn()
            except Exception as exc:
                close_warn(exc)
        if not halt:
            summary["halt_result"] = {"action": "none", "reason": "stay_on: never halts"}
            return summary["halt_result"]
        cmd_hooks.boot_mark("halt")
        summary["halt_result"] = self._halt_fn(
            enabled=s["power_halt_enabled"], dry_run=s["power_halt_dry_run"],
            mode=s["power_halt_mode"], script_path=s["power_halt_script_path"])
        return summary["halt_result"]
