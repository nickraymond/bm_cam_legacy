#!/usr/bin/env python3
# filename: rc_supervisor.py
# description: Sprint26 S3a — the supervisor runtime, per_boot: one action per power-on, then halt.
"""
The supervisor (DESIGN_supervisor.md §4; PLAN_S3a.md S3a.5), per_boot only.

Selected by `commands.runtime: supervisor` or `--runtime supervisor` (G3).
The legacy runtime stays selectable until S5; in S3a.5 both produce the same
wire, byte for byte (tests/golden runs every scenario under both).

What the supervisor owns (and the legacy cycle bodies owned before):
  - the ONE CycleBudget of the boot, anchored right before the daemon starts
    (G1: never re-anchored; it sizes the ladder / clip and clamps the tail);
  - the port owner: daemon start, then shutdown -> close -> halt, ALWAYS, even
    when the daemon or the action fails (G5: legacy stills skipped both when the
    UART failed at daemon start);
  - one JSON line per action in cron_logs/supervisor_actions.jsonl, written
    before the halt (a real halt can end the process).

The actions are today's cycle bodies (rc_progressive_jpeg.still_action,
rc_video_tx.video_action), still reached through run_cycle /
run_video_tx_cycle with `supervised=` so their injected dependencies (clock,
sleep, halt, the golden harness's fakes) bind to this boot on first use.

Scope (G4): still x transmit and video x transmit (video_tx.enabled). The
recorder, --capture-only and heic paths run the legacy code (S3c).

Example (what main() does):
  boot = rc_supervisor.Boot(settings, media="still", bm_commands_cfg=cfg,
                            command_state=state, transmit=True, bench_commands=False)
  summary = rc_supervisor.run_per_boot(boot, lambda b: run_cycle(settings, ..., supervised=b))
"""

import json
import os
import resource
import time
from datetime import datetime, timezone

import rc_command_hooks as cmd_hooks
from rc_port_owner import PortOwner
from rc_time_budget import CycleBudget

ACTION_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cron_logs", "supervisor_actions.jsonl")


class Boot:
    """One per_boot run: the budget, the port owner and the action's summary."""

    def __init__(self, settings, *, media, bm_commands_cfg, command_state, transmit,
                 bench_commands, action_log=ACTION_LOG, reresolve_fn=None):
        self.settings = settings
        self.reresolve_fn = reresolve_fn    # W4: settings -> settings with the overlay re-read
        self.media = media
        self.bm_commands_cfg = bm_commands_cfg
        self.command_state = command_state
        self.transmit = transmit
        self.bench_commands = bench_commands
        self.action_log = action_log
        self.owner = None
        self.budget = None
        self.summary = None
        self.close_warn = print
        self.end_line = None          # set by the action adapter: its "cycle end" line

    def start(self, summary, *, clock, sleep_fn, halt_fn, bm_close_fn, daemon_factory,
              log_fn, close_warn, end_line):
        """Bind the action's dependencies and bring the boot up: budget (G1),
        port session, daemon. -> (daemon, budget). Raises on a UART failure;
        run_per_boot still runs shutdown -> close -> halt."""
        self.summary = summary
        self.close_warn = close_warn
        s = self.settings
        self.owner = PortOwner(s, bm_close_fn=bm_close_fn, halt_fn=halt_fn,
                               clock=clock, sleep_fn=sleep_fn, log_fn=log_fn)
        self.budget = CycleBudget(s["budget_seconds"], s["pacing_delay_seconds"], clock=clock)
        self.owner.begin()
        if cmd_hooks.should_run_daemon(self.bm_commands_cfg, self.command_state,
                                       self.transmit, self.bench_commands):
            self.owner.start_daemon(daemon_factory or cmd_hooks.default_daemon_factory,
                                    self.bm_commands_cfg, self.command_state)
        self.end_line = end_line      # only once the action's budget exists
        return self.owner.daemon, self.budget

    def boot_drain(self, settings, summary, sleep_fn):
        """W4 (DESIGN §4 "drain queued commands"): apply the commands that are
        already here, THIS boot. Non-blocking: one yield to the reader thread
        (sleep_fn(0.0): nothing on the unit; the golden harness's fake sleep
        lets the reader finish what is on the wire), then what the daemon has
        decoded is parsed, persisted and its ack queued (acks keep their
        normal flush after END). If anything was applied, the command overlay
        is re-resolved from the YAML base, so it governs this boot's action.
        The budget is not rebuilt (it was anchored before the daemon, G1).
        Then, for stills, a pending trg is serviced (it may have just
        arrived). -> (settings, flags)."""
        flags = {"skip_time_window": False, "capture_only": False}
        daemon = self.owner.daemon if self.owner else None
        if daemon is not None:
            sleep_fn(0.0)
            events = daemon.process_pending()
            summary["command_events"].extend(e["action"] for e in events)
            if events and self.reresolve_fn is not None:
                print(f"[SUP] boot drain: {len(events)} command(s) applied this boot")
                settings = self.reresolve_fn(settings)
        if self.media == "still" and self.command_state is not None:
            settings, flags = cmd_hooks.service_pending_trigger(
                settings, self.command_state, transmit=self.transmit)
            if settings.get("trigger"):
                summary["trigger"] = settings["trigger"]
        self.settings = settings
        return settings, flags

    def finish(self):
        """shutdown -> close -> halt (never raises). An action that failed before
        start() gets an owner on the production defaults, so it still halts."""
        if self.summary is None:
            self.summary = {}
        if self.owner is None:
            import bm_port
            from rc_power_halt import perform_power_halt
            self.owner = PortOwner(self.settings, bm_close_fn=bm_port.close,
                                   halt_fn=perform_power_halt, clock=time.monotonic,
                                   sleep_fn=time.sleep)
        self.owner.finish(self.summary, close_port=True, close_warn=self.close_warn)
        if self.end_line is not None:
            try:
                print(self.end_line())
            except Exception as exc:
                print(f"[SUP][WARN] cycle end line: {exc}")


def action_record(boot, error=None):
    """The one JSON line per action (DESIGN §4 "Action log")."""
    s = boot.summary or {}
    tr = s.get("transmit_result") or {}
    trig = s.get("trigger") or (boot.settings.get("trigger"))
    return {
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime": "supervisor", "run": "per_boot", "media": boot.media,
        "output": "transmit" if boot.transmit else "none",
        "trigger_id": (trig or {}).get("id") if isinstance(trig, dict) else None,
        "stage": s.get("stage"), "media_key": s.get("media_key"),
        "sent": tr.get("sent"), "planned": tr.get("planned"),
        "complete": tr.get("complete_send"),
        "elapsed_s": round(boot.budget.elapsed_s(), 1) if boot.budget else None,
        "error": error or s.get("error"),
        "rss_kb": _peak_rss_kb(),
    }


def _peak_rss_kb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // 1024 if os.uname().sysname == "Darwin" else peak    # macOS: bytes


def write_action_log(boot, error=None):
    """Append the action line; a logging failure never costs the halt."""
    try:
        rec = action_record(boot, error)
        os.makedirs(os.path.dirname(boot.action_log), exist_ok=True)
        with open(boot.action_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        print(f"[SUP] action: media={rec['media']} stage={rec['stage']} "
              f"sent={rec['sent']}/{rec['planned']} elapsed={rec['elapsed_s']}s")
    except Exception as exc:
        print(f"[SUP][WARN] action log not written: {exc}")


def run_per_boot(boot, action_fn):
    """One action, then shutdown -> close -> halt. Returns the action's summary;
    re-raises the action's exception AFTER the halt ran (main maps it to exit 1)."""
    print(f"[SUP] per_boot: media={boot.media} transmit={boot.transmit}")
    error = None
    try:
        return action_fn(boot)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        write_action_log(boot, error)
        boot.finish()
