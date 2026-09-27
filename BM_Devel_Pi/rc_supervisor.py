#!/usr/bin/env python3
# filename: rc_supervisor.py
# description: Sprint26 S3a/S3b — the supervisor runtime: per_boot (one action, halt) and stay_on (loop).
"""
The supervisor (DESIGN_supervisor.md §4; PLAN_S3a.md S3a.5; PLAN_S3b.md).

per_boot (S3a): one action per power-on, the listen tail, then halt.
stay_on (S3b, run_stay_on): the daemon comes up once; the process then idles
(commands every 0.2 s), runs an action on a trg or every mode.interval_s
(window obeyed), sends <WS a=idle> every mode.heartbeat_s of silence, and
never halts. SIGTERM stops it at the next safe point (stay_on only).

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
import rc_stay_on_guard as guard
from rc_port_owner import PortOwner
from rc_time_budget import CycleBudget

# W6 (DESIGN §4 "Time"): after the process's FIRST Spotter read (which always
# steps the clock: a Pi without an RTC boots with a wrong clock), the system
# clock is stepped only when a read is off by at least this much ("a few
# seconds"; filenames have 1 s resolution). per_boot reads once per boot, so
# the rule first bites in stay_on (S3b); tests/test_s3a_supervisor pins it.
CLOCK_STEP_MIN_DRIFT_S = 2.0

# W10 (PLAN_S3b.md H12): an extra per_boot action for a trg heard in the tail
# needs at least this much budget beyond the tail margin (stills: capture +
# encode + a small burst; the ladder sizes the image to what is left).
W10_MIN_STILL_ACTION_S = 60.0
W10_VIDEO_MARGIN_S = 60.0      # video: clip + lead-in + this

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
        self.gate_reads = 0           # W6: schedule-gate time reads this process
        # S3b: set by run_stay_on. per_boot keeps S3a's behaviour exactly.
        self.run = "per_boot"
        self.listen_tail = True       # H4: stay_on has no tail (the idle loop listens)
        self.quiet_skip = False       # H3: stay_on, a window skip after the first of a run
        self.on_process_start = None  # H7: crash-loop fallback's <WS a=crashloop>
        # W10 (O3): the least budget an extra per_boot action needs beyond the
        # tail margin; main() sets the video value (clip + lead-in + 60 s).
        self.min_action_s = W10_MIN_STILL_ACTION_S

    def start(self, summary, *, clock, sleep_fn, halt_fn, bm_close_fn, daemon_factory,
              log_fn, close_warn, end_line):
        """Bind the action's dependencies and bring the action up: its budget,
        then (first action of the process only) the port session and daemon.
        -> (daemon, budget). Raises on a UART failure; run_per_boot still runs
        shutdown -> close -> halt.

        Two scopes (S3b.2): the PROCESS scope (port owner, port session,
        daemon) comes up once and lives until finish(); the ACTION scope
        (budget, summary, end line) is rebuilt for every action. per_boot has
        one action, so the order is exactly S3a's: budget, then daemon (G1).
        stay_on brings the process scope up before its first decision
        (start_process) and gets one budget per action (DESIGN §4 "Budget")."""
        self.summary = summary
        self.close_warn = close_warn
        s = self.settings
        if self.budget is None or self.run == "stay_on":
            # per_boot: ONE budget per boot, never re-anchored, also for a W10
            # extra action (G1, DESIGN §4 Budget); stay_on: one per action.
            self.budget = CycleBudget(s["budget_seconds"], s["pacing_delay_seconds"],
                                      clock=clock)
        if self.owner is None:
            self.start_process(clock=clock, sleep_fn=sleep_fn, halt_fn=halt_fn,
                               bm_close_fn=bm_close_fn, daemon_factory=daemon_factory,
                               log_fn=log_fn)
        self.end_line = end_line      # only once the action's budget exists
        return self.owner.daemon, self.budget

    def start_process(self, *, clock, sleep_fn, halt_fn, bm_close_fn, daemon_factory,
                      log_fn=print):
        """The process scope: port owner, port session, command daemon. Once
        per process; raises on a UART failure (the owner is set first, so
        finish() still stops the reader and closes/halts)."""
        s = self.settings
        self.owner = PortOwner(s, bm_close_fn=bm_close_fn, halt_fn=halt_fn,
                               clock=clock, sleep_fn=sleep_fn, log_fn=log_fn)
        self.owner.begin()
        if cmd_hooks.should_run_daemon(self.bm_commands_cfg, self.command_state,
                                       self.transmit, self.bench_commands):
            self.owner.start_daemon(daemon_factory or cmd_hooks.default_daemon_factory,
                                    self.bm_commands_cfg, self.command_state)
            # W6: every Spotter time read over the shared port is a fresh one.
            self.owner.daemon.fresh_time_reads = True
        if self.on_process_start is not None:
            self.on_process_start()      # never raises (rc_progressive_jpeg._crashloop_notice)
        return self.owner.daemon

    def w10_trigger_fits(self):
        """W10 (O3, Nick 2026-09-25): a trg is armed AND an extra action fits
        this boot's budget: remaining - TAIL_SAFETY_S >= min_action_s. The
        listen tail ends early on it; otherwise the tail runs on and the trg
        stays armed for the next boot. per_boot only."""
        if self.run != "per_boot" or self._pending_trigger() is None:
            return False
        if self.budget is None:
            return False
        return self.budget.remaining_s() - cmd_hooks.TAIL_SAFETY_S >= self.min_action_s

    def _pending_trigger(self):
        return getattr(self.command_state, "pending_trigger", None)

    def gate_kwargs(self, daemon, settings):
        """The schedule gate's kwargs under the supervisor (W6): the legacy set,
        plus the drift-only clock step from the second read of the process on."""
        kwargs = cmd_hooks.gate_kwargs_for(daemon, settings)
        if self.gate_reads > 0:
            kwargs["min_clock_step_s"] = CLOCK_STEP_MIN_DRIFT_S
        self.gate_reads += 1
        return kwargs

    def boot_drain(self, settings, summary, sleep_fn):
        """W4 (DESIGN §4 "drain queued commands"): apply the commands that are
        already here, THIS boot. Non-blocking: one yield to the reader thread
        (sleep_fn(0.0): nothing on the unit; the golden harness's fake sleep
        lets the reader finish what is on the wire), then what the daemon has
        decoded is parsed, persisted and its ack queued (acks keep their
        normal flush after END). If anything was applied, the command overlay
        is re-resolved from the YAML base, so it governs this boot's action.
        The budget is not rebuilt (it was anchored before the daemon, G1).
        Then a pending trg is serviced (it may have just arrived; W5: video
        too). -> (settings, flags)."""
        flags = {"skip_time_window": False, "capture_only": False}
        daemon = self.owner.daemon if self.owner else None
        if daemon is not None:
            sleep_fn(0.0)
            events = daemon.process_pending()
            summary["command_events"].extend(e["action"] for e in events)
            if events and self.reresolve_fn is not None:
                print(f"[SUP] boot drain: {len(events)} command(s) applied this boot")
                settings = self._reresolve(settings, summary)
        if self.command_state is not None:
            # Stills (W4 ordering) and, since W5, video: a trg is serviced at
            # this decision point for both media.
            settings, flags = cmd_hooks.service_pending_trigger(
                settings, self.command_state, transmit=self.transmit)
            if settings.get("trigger"):
                summary["trigger"] = settings["trigger"]
                if self.media == "video" and settings.get("source_image_path"):
                    # trg 3/4 name a stills reference image; a video unit has
                    # none, so it records and sends a clip (window bypassed).
                    print(f"[SUP][WARN] trg {settings['trigger'].get('value')} names a stills "
                          "reference; a video unit records and sends a clip instead")
        self.settings = settings
        return settings, flags

    def _reresolve(self, settings, summary):
        """The overlay re-read after the drain. Everything built from the
        pre-drain settings follows it: the budget's limits (txd/win; same start,
        G1), the halt settings the owner uses (hlt) and the summary's budget.
        A failed re-resolve keeps the pre-drain settings (one loud line): a
        bad override must not cost the capture."""
        try:
            fresh = self.reresolve_fn(settings)
        except Exception as exc:
            print(f"[SUP][ERR] boot drain re-resolve failed ({type(exc).__name__}: {exc}); "
                  "keeping the settings this boot started with")
            return settings
        if (fresh["budget_seconds"], fresh["pacing_delay_seconds"]) != (
                self.budget.budget_seconds, self.budget.seconds_per_message):
            print(f"[SUP] boot drain: budget {self.budget.budget_seconds:g}s @ "
                  f"{self.budget.seconds_per_message:g}s/msg -> {fresh['budget_seconds']}s @ "
                  f"{fresh['pacing_delay_seconds']}s/msg (same start)")
            self.budget.resize(fresh["budget_seconds"], fresh["pacing_delay_seconds"])
        if "budget_seconds" in summary:
            summary["budget_seconds"] = fresh["budget_seconds"]
        self.owner.settings = fresh
        return fresh

    def finish(self, halt=True):
        """shutdown -> close -> halt (never raises). An action that failed before
        start() gets an owner on the production defaults, so it still halts.
        halt=False (stay_on stop, H6): shutdown -> close, no halt."""
        if self.summary is None:
            self.summary = {}
        if self.owner is None:
            import bm_port
            from rc_power_halt import perform_power_halt
            self.owner = PortOwner(self.settings, bm_close_fn=bm_port.close,
                                   halt_fn=perform_power_halt, clock=time.monotonic,
                                   sleep_fn=time.sleep)
        self.owner.finish(self.summary, close_port=True, close_warn=self.close_warn, halt=halt)
        if self.end_line is not None:
            try:
                print(self.end_line())
            except Exception as exc:
                print(f"[SUP][WARN] cycle end line: {exc}")


def action_record(boot, error=None, n=None, kind=None):
    """The one JSON line per action (DESIGN §4 "Action log"). stay_on adds the
    action's number in the process and why it ran (trg | scheduled)."""
    s = boot.summary or {}
    tr = s.get("transmit_result") or {}
    trig = s.get("trigger") or (boot.settings.get("trigger"))
    extra = {} if n is None else {"action": n, "kind": kind,
                                  "skipped": s.get("schedule_allowed") is False}
    return {
        **extra,
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime": "supervisor", "run": boot.run, "media": boot.media,
        "output": "transmit" if boot.transmit else "none",
        "trigger_id": (trig or {}).get("id") if isinstance(trig, dict) else None,
        "stage": s.get("stage"), "media_key": s.get("media_key"),
        "sent": tr.get("sent"), "planned": tr.get("planned"),
        "complete": tr.get("complete_send"),
        "elapsed_s": round(boot.budget.elapsed_s(), 1) if boot.budget else None,
        "error": error or s.get("error"),
        "rss_kb": _peak_rss_kb(),
        "rss_now_kb": guard.current_rss_kb(),      # S3b H9: can fall; the peak cannot
    }


def _peak_rss_kb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // 1024 if os.uname().sysname == "Darwin" else peak    # macOS: bytes


# H10: supervisor_actions.jsonl rotates to .1 at this many lines (both modes).
ACTION_LOG_MAX_LINES = 2000
_action_log_lines = {}       # path -> lines counted this process (read once)


def _rotate_action_log(path):
    """Keep the action log bounded: at ACTION_LOG_MAX_LINES it becomes
    <path>.1 (the previous .1 is dropped), as config_journal rotates."""
    if path not in _action_log_lines:
        try:
            with open(path, "rb") as fh:
                _action_log_lines[path] = sum(1 for _ in fh)
        except FileNotFoundError:
            _action_log_lines[path] = 0
    if _action_log_lines[path] >= ACTION_LOG_MAX_LINES:
        os.replace(path, path + ".1")
        _action_log_lines[path] = 0
        print(f"[SUP] action log rotated: {os.path.basename(path)} -> .1")


def write_action_log(boot, error=None, n=None, kind=None):
    """Append the action line; a logging failure never costs the halt."""
    try:
        rec = action_record(boot, error, n=n, kind=kind)
        os.makedirs(os.path.dirname(boot.action_log), exist_ok=True)
        _rotate_action_log(boot.action_log)
        with open(boot.action_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        _action_log_lines[boot.action_log] += 1
        print(f"[SUP] action: media={rec['media']} stage={rec['stage']} "
              f"sent={rec['sent']}/{rec['planned']} elapsed={rec['elapsed_s']}s")
    except Exception as exc:
        print(f"[SUP][WARN] action log not written: {exc}")


def run_per_boot(boot, action_fn):
    """One action, then shutdown -> close -> halt. Returns the last action's
    summary; re-raises an action's exception AFTER the halt ran (main maps it
    to exit 1).

    W10 (O3): a trg heard in the listen tail that fits this boot's budget
    (Boot.w10_trigger_fits; the tail ended early on it) fires as another
    action on the SAME budget (G1), with its own tail; the budget bounds the
    chain. A trg that does not fit stays armed for the next boot."""
    print(f"[SUP] per_boot: media={boot.media} transmit={boot.transmit}")
    error = None
    try:
        summary = action_fn(boot)
        n = 1
        while boot.w10_trigger_fits():
            write_action_log(boot)          # the finished action's line
            n += 1
            trg = boot._pending_trigger()
            print(f"[SUP] W10: trg id={trg.get('id')} heard in the listen tail fires this "
                  f"boot (action {n}; {boot.budget.remaining_s():.0f}s of budget left)")
            summary = action_fn(boot)
        if boot._pending_trigger() is not None:
            left = boot.budget.remaining_s() if boot.budget else 0.0
            print(f"[SUP] W10: trg stays armed for the next boot ({left:.0f}s of budget left; "
                  f"an extra action needs {cmd_hooks.TAIL_SAFETY_S + boot.min_action_s:.0f}s)")
        return summary
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        write_action_log(boot, error)
        boot.finish()


# ---------------------------------------------------------------------------
# stay_on (Sprint26 S3b; DESIGN_supervisor.md §4, PLAN_S3b.md H2-H6)
# ---------------------------------------------------------------------------

IDLE_TICK_S = 0.2            # §4: commands processed every 0.2 s while idle
IDLE_HEAL_S = 600.0          # O5 (Nick 2026-09-25): pending heals go out after 10 min idle
EXIT_ARGS = 2                # stay_on cannot run as invoked (no daemon: no --transmit)
EXIT_CRASH = 70              # H7: stay_on failed (watchdog, error); wrapper restarts, backoff
EXIT_RSS = 71                # H9: RSS ceiling reached; clean exit, wrapper restarts in 5 s

# H6: SIGTERM sets a flag only (stay_on). Raising inside subprocess.run would
# kill a capture or the halt script mid-way; the loop stops at its next safe
# point instead. per_boot never installs this (tools rely on "SIGTERM = no
# finally = no halt" there).
STOP = {"requested": False, "halting": False, "signal": None}


def _on_sigterm(signum, _frame):
    if STOP["halting"]:
        return                   # ignored once halting has started
    STOP["requested"] = True
    STOP["signal"] = signum


def install_stop_flag():
    import signal
    import rc_capture
    STOP.update(requested=False, halting=False, signal=None)
    signal.signal(signal.SIGTERM, _on_sigterm)
    rc_capture.stop_check = stop_requested      # H6: bounded wait in a capture retry
    print("[SUP] SIGTERM -> stop at the next safe point (no halt)")


def stop_requested():
    return STOP["requested"]


def _idle_tick(daemon, clock, sleep_fn):
    """One pass of the listen loop without the sleep: apply what arrived,
    send paced acks, flush console lines (the listen tail's body,
    CommandDaemon.listen_window). -> command events."""
    events = daemon.process_pending()
    daemon.drain_acks(clock=clock)
    daemon.drain_console(sleep_fn=sleep_fn)
    return events


def _reader_health(daemon):
    """H8: the watchdog, every idle tick. A daemon without the check (older
    fakes) counts as healthy."""
    check = getattr(daemon, "reader_health", None)
    if check is None:
        return True, ""
    try:
        return check()
    except Exception as exc:
        return False, f"health check failed ({type(exc).__name__}: {exc})"


def _run_action(boot, action_fn, settings, n, kind, quiet_skip):
    """One stay_on action on fresh settings (YAML base + current overlay, so
    a one-shot trg key never leaks into the next action). -> summary."""
    boot.settings = settings
    boot.quiet_skip = quiet_skip
    boot.summary = None
    print(f"[SUP] ===== action {n} ({kind}) utc="
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} =====")
    error = None
    try:
        summary = action_fn(boot, settings)
    except Exception as exc:
        # One failed action is not a failed process: logged, and the loop
        # goes on (the next trg or slot may well succeed).
        error = f"{type(exc).__name__}: {exc}"
        print(f"[SUP][ERR] action {n} failed: {error}")
        summary = boot.summary or {}
    write_action_log(boot, error, n=n, kind=kind)
    print(f"[SUP] ===== action {n} done: stage={summary.get('stage')} "
          f"skipped={summary.get('schedule_allowed') is False} =====")
    return summary


def _boot_time_read(boot, daemon, settings, gate_fn):
    """§4: the Spotter UTC read after the drain, so a trigger-only unit has a
    right clock (no RTC) before its first action. Through the schedule gate
    (the process's first read always steps the clock, W6); never raises."""
    try:
        _allowed, info = gate_fn(settings["config_path"], **boot.gate_kwargs(daemon, settings))
        print(f"[SUP] stay_on boot time read: {info.get('utc_time', 'n/a')} "
              f"(clock: {info.get('set_system_clock', 'not stepped')})")
    except Exception as exc:
        print(f"[SUP][WARN] stay_on boot time read failed ({type(exc).__name__}: {exc})")


def heal_pass(boot, daemon, settings, tx_open_fn, clock, sleep_fn):
    """O5 (PLAN_S3b.md H11): send pending heals with no capture. The heal part
    of a transmitting action on its own: plan (<= HEAL_CAP_PER_WAKE chunks,
    newest first), the lane wait when the unit uses the transmit phase, the
    chunks (paced, pump-only), then one <HL> per key. A fresh budget per pass;
    the pass counts as a wake for wakes_left. -> its summary. Never raises."""
    import rc_heal
    import rc_transmit_phase
    summary = {"command_events": [], "stage": "heal_pass"}
    boot.summary = summary
    budget = CycleBudget(settings["budget_seconds"], settings["pacing_delay_seconds"],
                         clock=clock)
    boot.budget = budget
    try:
        heals = rc_heal.begin_wake(daemon, settings, summary,
                                   pump_fn=cmd_hooks.make_pending_pump_fn(daemon, summary))
        if heals is None:
            return summary
        delay = settings["pacing_delay_seconds"]
        phase_cfg = settings.get("transmit_phase_cfg") or {}
        if phase_cfg.get("enabled") and heals.planned_msgs:
            burst_s = rc_transmit_phase.burst_seconds_for(heals.planned_msgs, delay)
            grid = rc_transmit_phase.acquire_grid_clock(None, None, daemon=daemon, clock=clock)
            plan = rc_transmit_phase.plan_from_clock(grid, burst_s, phase_cfg)
            print(rc_transmit_phase.describe_plan(plan, burst_s))
            if plan["wait_s"] > 0 and budget.has_time_for(plan["wait_s"] + burst_s):
                sleep_fn(plan["wait_s"])
        tx = tx_open_fn(settings["config_path"])
        heals.send_before_start(tx, budget, reserve_msgs=0, delay_seconds=delay,
                                sleep_fn=sleep_fn)
        heals.send_status_after_end(tx, budget, wake_key=None, delay_seconds=delay,
                                    sleep_fn=sleep_fn)
        summary["stage"] = "done"
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[SUP][ERR] idle heal pass failed: {summary['error']}")
    return summary


def run_stay_on(boot, action_fn, *, settings_fn, interval_s, heartbeat_s, heartbeat_fn,
                clock, sleep_fn, halt_fn, bm_close_fn, daemon_factory, gate_fn=None,
                heal_tx_open_fn=None):
    """The stay_on loop (DESIGN §4). Returns the process exit code:
    0 stopped (SIGTERM), EXIT_ARGS cannot run, EXIT_CRASH failed (restart).

      boot: daemon (process scope, once) -> drain -> Spotter UTC read
      loop: idle tick every IDLE_TICK_S (commands, acks, console)
            pending trg            -> action now (window bypassed, D-S12-4)
            interval_s > 0 and due -> scheduled action (window obeyed)
            heartbeat_s > 0 and heartbeat_s since the last uplink -> <WS a=idle>
            pending heals and IDLE_HEAL_S since the last send -> heal pass (O5;
            needs heal_tx_open_fn)
      stop: SIGTERM flag -> shutdown -> close; never a halt

    settings_fn() -> fresh settings per action; action_fn(boot, settings) ->
    summary; heartbeat_fn(settings) sends one <WS a=idle>."""
    boot.run, boot.listen_tail = "stay_on", False
    if gate_fn is None:
        from spotter_time_sync import should_transmit_now_from_schedule as gate_fn
    settings = settings_fn()
    boot.settings = settings
    print(f"[SUP] stay_on: media={boot.media} interval_s={interval_s} "
          f"({'trigger-only' if interval_s == 0 else 'scheduled + trg'}) "
          f"heartbeat_s={heartbeat_s}{' (off)' if heartbeat_s == 0 else ''}")
    if settings.get("power_halt_enabled"):
        print("[SUP][WARN] stay_on: power.halt is IGNORED (a stay_on process never halts)")
    install_stop_flag()
    guard.marker_set()
    guard.prune_logs(os.path.dirname(boot.action_log))
    boot.summary = {"command_events": []}
    code = 0
    try:
        daemon = boot.start_process(clock=clock, sleep_fn=sleep_fn, halt_fn=halt_fn,
                                    bm_close_fn=bm_close_fn, daemon_factory=daemon_factory)
        if daemon is None:
            print("[SUP][ERR] stay_on needs the command daemon (commands.enabled and "
                  "--transmit); nothing to do")
            return EXIT_ARGS
        _idle_tick(daemon, clock, sleep_fn)         # drain what is already queued
        _boot_time_read(boot, daemon, settings, gate_fn)
        code = _loop(boot, daemon, action_fn, settings_fn, interval_s, heartbeat_s,
                     heartbeat_fn, clock, sleep_fn, heal_tx_open_fn)
    except Exception as exc:
        print(f"[SUP][ERR] stay_on failed ({type(exc).__name__}: {exc}); exit {EXIT_CRASH}")
        code = EXIT_CRASH
    finally:
        if STOP["requested"]:
            print(f"[SUP] stop requested (signal {STOP['signal']}): shutdown -> close, no halt")
        boot.finish(halt=False)
        guard.marker_clear()
        print(f"[SUP] stay_on exit {code}")
    return code


def _loop(boot, daemon, action_fn, settings_fn, interval_s, heartbeat_s, heartbeat_fn,
          clock, sleep_fn, heal_tx_open_fn=None):
    state = boot.command_state
    n = 0
    next_due = clock() if interval_s > 0 else None
    last_uplink = clock()
    last_send = clock()          # O5: the last action or heal pass (heartbeats do not count)
    skip_run = False             # H3: the last scheduled action was a window skip
    stuck_trg = None             # a trg whose consume could not be persisted
    while not stop_requested():
        _idle_tick(daemon, clock, sleep_fn)
        ok, why = _reader_health(daemon)
        if not ok:
            print(f"[SUP][ERR] watchdog: {why}; exit {EXIT_CRASH} (the wrapper restarts)")
            return EXIT_CRASH
        now = clock()
        trg = state.pending_trigger if state is not None else None
        if trg is not None and trg == stuck_trg:
            trg = None           # already failed to consume: never a capture loop
        kind = "trg" if trg is not None else (
            "scheduled" if next_due is not None and now >= next_due else None)
        if kind is not None:
            n += 1
            quiet = kind == "scheduled" and skip_run
            summary = _run_action(boot, action_fn, settings_fn(), n, kind, quiet)
            skipped = summary.get("schedule_allowed") is False
            if kind == "scheduled":
                next_due = now + interval_s
                if next_due <= clock():
                    print(f"[SUP] action {n} overran its {interval_s}s slot; "
                          "next scheduled action now (missed slots are not caught up)")
                    next_due = clock()
                skip_run = skipped
            elif state is not None and state.pending_trigger == trg:
                stuck_trg = trg
                print(f"[SUP][ERR] trg id={trg.get('id')} is still armed after its action "
                      "(consume not persisted); not re-firing it in this process")
            if not (skipped and quiet):
                last_uplink = clock()
            last_send = clock()
            guard.rotate_stdout_if_big()
            rss = guard.current_rss_kb()
            if guard.rss_over_ceiling(rss):
                print(f"[SUP][ERR] RSS {rss} kB over the {guard.RSS_CEILING_KB} kB ceiling "
                      f"after action {n}; clean exit {EXIT_RSS} (the wrapper restarts)")
                return EXIT_RSS
            continue
        if (heal_tx_open_fn is not None and state is not None and state.pending_heals
                and now - last_send >= IDLE_HEAL_S):
            n += 1
            print(f"[SUP] ===== action {n} (heal) after {now - last_send:.0f}s idle: "
                  f"{len(state.pending_heals)} pending heal(s) (O5) =====")
            settings = settings_fn()
            boot.settings = settings
            summary = heal_pass(boot, daemon, settings, heal_tx_open_fn, clock, sleep_fn)
            write_action_log(boot, summary.get("error"), n=n, kind="heal")
            last_uplink = last_send = clock()
            guard.rotate_stdout_if_big()
            continue
        if heartbeat_s > 0 and now - last_uplink >= heartbeat_s:
            try:
                heartbeat_fn(settings_fn())
                print(f"[SUP] heartbeat <WS a=idle> after {now - last_uplink:.0f}s idle")
            except Exception as exc:
                print(f"[SUP][WARN] heartbeat failed ({type(exc).__name__}: {exc})")
            last_uplink = clock()
            guard.rotate_stdout_if_big()
            continue
        sleep_fn(IDLE_TICK_S)
    return 0
