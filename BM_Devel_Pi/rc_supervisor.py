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

Scope: still and video (video_tx.enabled) x transmit (S3a) and x save_local
(S3c: Boot.output, set by main from mode.output), --capture-only (S3c). The
recorder (mode.media video_logger) still runs the legacy code: it moves in its
own follow-up (PLAN_S3c.md §5 R1). heic has nothing left to run.

Example (what main() does):
  boot = rc_supervisor.Boot(settings, media="still", bm_commands_cfg=cfg,
                            command_state=state, transmit=True, bench_commands=False)
  summary = rc_supervisor.run_per_boot(boot, lambda b: run_cycle(settings, ..., supervised=b))
"""

import copy
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


class AltActionUnavailable(RuntimeError):
    """The other media's action could not be prepared (its config does not
    resolve); raised BEFORE the action starts (b.6b)."""


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
        self.media_key = None         # this action's media key (stills; video: summary)
        self.gate_reads = 0           # W6: schedule-gate time reads this process
        # S3b: set by run_stay_on. per_boot keeps S3a's behaviour exactly.
        self.run = "per_boot"
        self.listen_tail = True       # H4: stay_on has no tail (the idle loop listens)
        self.quiet_skip = False       # H3: stay_on, a window skip after the first of a run
        self.on_process_start = None  # H7: crash-loop fallback's <WS a=crashloop>
        # W10 (O3): the least budget an extra per_boot action needs beyond the
        # tail margin; main() sets the video value (clip + lead-in + 60 s).
        self.min_action_s = W10_MIN_STILL_ACTION_S
        self.w10_stuck = None         # a trg whose consume could not be persisted
        # S3c (PLAN_S3c.md §5 C2): the output and its knobs live HERE, read once
        # from the v2 values by main(). Never in `settings`: the overlay
        # re-resolve rebuilds settings from the YAML base (boot drain, W10,
        # every stay_on action), so a key put there would be lost on the first
        # applied command and a save_local action would silently transmit.
        self.output = "transmit"      # or "save_local"
        self.save_quality = 85        # still.save.quality
        self.storage_cfg = None       # storage.* (the one SD limit pair, §5 C3)
        self.storage_reason = None    # "storage_full" while the SD is over its limit
        self.save_time_reads = 0      # save_local_time_read calls that read Spotter time
        # S4 W8a (PLAN_S4.md G1): the v9 reply policy on a migrated unit
        # (command_replies.V9Replies), handed to the daemon at process start.
        self.v9_replies = None
        self.v9_dispatch_factory = None   # b.2c: daemon -> command_v9.Dispatcher
        self.guard_state = None           # b.5: the V9State whose guarded keys count actions
        # b.6a (§9, G6): a trg kv applies to ONE action. one_shot_fn(kv) -> the
        # action's settings from a per-action render copy (video: it also sets
        # one_shot_vtx); the output and save quality are one-action overrides.
        self.one_shot_fn = None
        self.one_shot_vtx = None
        self.one_shot_output = None
        self.one_shot_kv = None
        self._save_quality_base = None
        # b.6b (§6.1 "media can be overridden one-shot"): the other media's action,
        # {media: fn(boot, settings=None)}, chosen at a decision point when the
        # pending trg's kv names that media; and its W10 minimum.
        self.alt_actions = {}
        self.alt_min_action_s = {}
        self.video_duration_s = None      # the configured clip length (W10 sizing)
        self.current_vtx = None           # S4b review R2-4: the clip config as of now
        self._process_mark = None         # W8b: up= counts from the process start
        self.config_errors = []           # K7: [(kind, key, why)] -> <CF err> at start
        # b.6c (DESIGN §4 keep-alive / hld): limits from the effective config
        # (commands.keepalive_s, keepalive_max_s, hold_max_min,
        # power.bus_always_on); hold and keep-alive live in memory only.
        self.v9_limits = {}
        self.hold_until = None
        self.last_command_at = None
        self._normal_end = None
        self._sleep_fn = None
        self._guard_mark = None           # clock() of the last guard note (uptime accrual)
        self._guard_clock = None

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
        self.media_key = None          # set by the stills action once it has one
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
        self._guard_clock, self._guard_mark = clock, clock()
        self._sleep_fn = sleep_fn
        self.owner = PortOwner(s, bm_close_fn=bm_close_fn, halt_fn=halt_fn,
                               clock=clock, sleep_fn=sleep_fn, log_fn=log_fn)
        self.owner.begin()
        if cmd_hooks.should_run_daemon(self.bm_commands_cfg, self.command_state,
                                       self.transmit, self.bench_commands):
            self.owner.start_daemon(daemon_factory or cmd_hooks.default_daemon_factory,
                                    self.bm_commands_cfg, self.command_state)
            # W6: every Spotter time read over the shared port is a fresh one.
            self.owner.daemon.fresh_time_reads = True
            if self.v9_replies is not None:
                self.owner.daemon.v9 = self.v9_replies
            if self.v9_dispatch_factory is not None:
                # b.8: every small cellular send shares the lane guard (§6.1)
                self.owner.daemon.lane_cfg = dict(s.get("transmit_phase_cfg") or {})
                self.owner.daemon.lane_room_fn = self._lane_room
                self.owner.daemon.v9_dispatch = self.v9_dispatch_factory(self.owner.daemon)
                self.owner.daemon.v9_dispatch.boot = self          # b.6c: hld + keep-alive
                if self.guard_state is not None:                    # b.7: mid-burst inbox
                    import command_inbox
                    self.owner.daemon.v9_inbox = command_inbox.Inbox(
                        command_inbox.path_beside(self.guard_state.path))
                self.owner.daemon.v9_dispatch.flush_notes()     # G10f: <CF reverted=..>
                self._install_wire_extras()                         # W8b
                self.owner.daemon.v9_dispatch.report_errors(self.config_errors)   # K7
        if self.v9_replies is not None and self._process_mark is None:
            # W8b on a migrated unit even with commands off (S4c review NIT 8):
            # the heartbeat and START carry cfg= whether or not a daemon runs
            self._install_wire_extras()
        if self.on_process_start is not None:
            self.on_process_start()      # never raises (rc_progressive_jpeg._crashloop_notice)
        return self.owner.daemon

    # ------------------------------------------------ b.6c: hld + keep-alive
    def _now(self):
        return self._guard_clock() if self._guard_clock is not None else time.monotonic()

    def _awake_limit(self):
        """The latest monotonic time this per_boot unit may stay up: the budget
        minus the halt margin (the Spotter cuts the bus on its own schedule),
        or None (no limit) when power.bus_always_on."""
        if self.v9_limits.get("power.bus_always_on") or self.budget is None:
            return None
        return self._now() + self.budget.remaining_s() - cmd_hooks.TAIL_SAFETY_S

    # ------------------------------------------------------------ W8b (S4c)
    def _install_wire_extras(self):
        """W8b: cfg=<hash8> + up=<s> on every <WS>; START cfg (core) and, on a
        triggered action, tg/r/m/d (DESIGN §9: the resolved one-shot values).
        v9 path only; cleared by finish()."""
        import rc_telemetry
        import rc_uplink_messages
        self._process_mark = self._now()
        rc_telemetry.WS_EXTRA_FN = self._ws_extra
        rc_uplink_messages.START_EXTRA_FN = self._start_extra

    def _config_hash(self):
        replies = self.v9_replies
        return replies._hash() if replies is not None else None

    def _ws_extra(self):
        return [("cfg", self._config_hash()),
                ("up", int(self._now() - (self._process_mark or self._now())))]

    def _start_extra(self):
        pairs = [("cfg", self._config_hash())]
        trig = (self.summary or {}).get("trigger")
        if isinstance(trig, dict) and trig.get("id") is not None:
            s = self.settings or {}
            vtx = self.one_shot_vtx or self.current_vtx
            pairs.append(("tg", trig["id"]))
            if self.media == "still":
                import rc_uplink_messages
                crop = None if s.get("source_image_path") else s.get("crop_native_xywh")
                pairs.append(("r", rc_uplink_messages.format_crop(crop)))   # na: a stored ref
                pairs.append(("m", s.get("message_cap")))
            elif self.media == "video" and vtx:
                pairs += [("m", vtx.get("message_cap")), ("d", vtx.get("duration_s"))]
        return pairs

    def _lane_room(self):
        """Seconds a lane wait may add before the per_boot halt margin, or None
        (no limit: stay_on never halts; power.bus_always_on)."""
        if self.run != "per_boot" or self.v9_limits.get("power.bus_always_on") \
                or self.budget is None:
            return None
        return self.budget.remaining_s() - cmd_hooks.TAIL_SAFETY_S

    def note_command(self):
        """Keep-alive: every command received pushes the halt back (§4)."""
        self.last_command_at = self._now()

    def request_hold(self, minutes):
        """hld v: hold awake `minutes` (0 releases). -> the minutes granted:
        at most commands.hold_max_min, and on per_boot at most what the budget
        allows (unless power.bus_always_on). Never persisted (§4)."""
        if minutes <= 0:
            self.hold_until = None
            return 0
        granted = min(int(minutes), int(self.v9_limits.get("commands.hold_max_min", 120)))
        if self.run == "per_boot":
            limit = self._awake_limit()
            if limit is not None:
                granted = max(0, min(granted, int((limit - self._now()) // 60)))
        self.hold_until = self._now() + granted * 60.0 if granted > 0 else None
        return granted

    def awake_until(self):
        """per_boot: the time the unit must stay up for hld / keep-alive, or
        None. Keep-alive = the last command + keepalive_s, at most
        keepalive_max_s past the normal end; both clamped to the budget."""
        if self._normal_end is None:
            self._normal_end = self._now()
        ends = []
        if self.hold_until is not None:
            ends.append(self.hold_until)
        ka = float(self.v9_limits.get("commands.keepalive_s", 0) or 0)
        if ka > 0 and self.last_command_at is not None:
            cap = self._normal_end + float(self.v9_limits.get("commands.keepalive_max_s", 0))
            ends.append(min(self.last_command_at + ka, cap))
        if not ends:
            return None
        until = max(ends)
        limit = self._awake_limit()
        return until if limit is None else min(until, limit)

    def stay_awake(self, daemon, sleep_fn):
        """b.6c: after the action and its tail, stay up while a hold or the
        keep-alive asks (commands, acks and console keep flowing). Returns
        "trigger" when a trg now fits this boot (W10), else "done". Commands
        applied meanwhile govern the halt (the overlay is re-read)."""
        if daemon is None or self.run != "per_boot" or not self.v9_limits:
            return "done"
        applied = 0
        reason = "done"
        announced = False
        while True:
            until = self.awake_until()
            if until is None or self._now() >= until:
                break
            if not announced:
                print(f"[SUP] staying awake {until - self._now():.0f}s "
                      f"(hld={self.hold_until is not None}, keep-alive after a command)")
                announced = True
            applied += len(_idle_tick(daemon, self._now, sleep_fn))
            if self.w10_trigger_fits():
                reason = "trigger"
                break
            sleep_fn(IDLE_TICK_S)
        if applied and self.reresolve_fn is not None:
            self.settings = self._reresolve(self.settings, self.summary or {})
        return reason

    def _trigger_media(self):
        trg = self._pending_trigger()
        if not trg:
            return None
        return (trg.get("kv") or {}).get("mode.media")

    def pick_action(self, default_fn):
        """b.6b: the action to run at this decision point. A pending trg whose kv
        names the OTHER media runs that media's action (from a per-action
        render) with this boot's port, daemon and budget; the boot's media is
        swapped for that one action. Otherwise `default_fn`."""
        media = self._trigger_media()
        alt = self.alt_actions.get(media) if media and media != self.media else None
        if alt is None:
            return default_fn

        def run_alt(boot, settings=None):
            own_media, own_min = self.media, self.min_action_s
            self.media = media
            self.min_action_s = self.alt_min_action_s.get(media, own_min)
            print(f"[SUP] trg media override: one {media} action on a {own_media} unit")
            try:
                return alt(boot, settings)
            except AltActionUnavailable as exc:
                # S4b review R2-2: the other media's config does not resolve. The
                # trg is cancelled loudly (never re-picked every boot, which would
                # leave the unit without a capture or a daemon); this action runs.
                print(f"[SUP][ERR] media override to {media} unavailable ({exc}); "
                      "the trg is cancelled and the normal action runs")
                if self.command_state is not None:
                    self.command_state.consume_trigger()
            finally:
                self.media, self.min_action_s = own_media, own_min
            return default_fn(boot) if settings is None else default_fn(boot, settings)
        return run_alt

    def w10_trigger_fits(self):
        """W10 (O3, Nick 2026-09-25): a trg is armed AND an extra action fits
        this boot's budget: remaining - TAIL_SAFETY_S >= min_action_s. The
        listen tail ends early on it; otherwise the tail runs on and the trg
        stays armed for the next boot. per_boot only."""
        if self.run != "per_boot" or not self.transmit or self._pending_trigger() is None:
            return False                 # (no --transmit: the trg is never consumed)
        if self._pending_trigger() is self.w10_stuck:
            return False                 # its consume failed once: never a capture loop
        if self.budget is None:
            return False
        media = self._trigger_media()
        need = self.alt_min_action_s.get(media, self.min_action_s) \
            if media and media != self.media else self.min_action_s
        kv = (self._pending_trigger() or {}).get("kv") or {}
        d = kv.get("video.send.duration_s")
        if d is not None and (media or self.media) == "video":
            # S4b review R2-8: a one-shot clip longer than the configured one
            need += max(0.0, float(d) - float(self.video_duration_s or d))
        return self.budget.remaining_s() - cmd_hooks.TAIL_SAFETY_S >= need

    def note_guards(self, summary=None):
        """S4 b.5 (DESIGN §6.3): after an action (or an idle heartbeat), count
        it toward the guarded keys: a transmitting action toward tx2, the time
        since the last note toward save_local's 2 h. A revert is announced at
        once (<CF reverted>) and, on stay_on, a reverted next-boot key restarts
        the process (exit 72). Never raises."""
        st = self.guard_state
        if st is None or not st.guarded or self._guard_clock is None:
            return []
        try:
            import command_guards
            now = self._guard_clock()
            up = now - (self._guard_mark if self._guard_mark is not None else now)
            self._guard_mark = now
            s = summary or {}
            sent = (s.get("transmit_result") or {}).get("sent")
            transmitted = bool(sent) and s.get("uplinked", not self.save_local) is not False
            reverted = command_guards.count_action(st, transmitted, up)
        except Exception as exc:
            print(f"[GUARD][ERR] action count failed ({type(exc).__name__}: {exc})")
            return []
        dispatch = getattr(getattr(self.owner, "daemon", None), "v9_dispatch", None)
        if reverted and dispatch is not None:
            dispatch.flush_notes()
            import config_registry as R
            for path, _lim in reverted:
                key = R.BY_PATH.get(path)
                if self.run == "stay_on" and key is not None and key.apply == R.NEXT_BOOT:
                    dispatch.restart_requested.append(path)
        return reverted

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

    @property
    def save_local(self):
        return (self.one_shot_output or self.output) == "save_local"

    def save_local_time_read(self, daemon, settings):
        """S3c (PLAN_S3c.md §5 C9): a save_local action always reads Spotter
        time, even when the window is disabled (the gate then reads nothing):
        filenames are capture time and a Pi has no RTC. Same clock rule as the
        gate (W6: the process's first read steps the clock, later reads only on
        drift). A failed read saves anyway on the Pi clock. -> the time source
        the saved files carry: "spotter", or "system" (read failed / no daemon),
        or the configured time_source when it is not spotter_utc."""
        if daemon is None:        # config_v2: save_local needs commands.enabled (a daemon)
            return "system"
        from spotter_time_sync import load_camera_schedule, set_system_clock_utc
        try:
            cfg = load_camera_schedule(settings["config_path"])
            if cfg.time_source != "spotter_utc":
                return cfg.time_source
            utc = daemon.wait_for_spotter_utc(cfg.spotter_time_timeout_seconds)
        except Exception as exc:
            print(f"[SUP][WARN] save_local time read failed ({type(exc).__name__}: {exc}); "
                  "saving on the Pi clock (time_source: system)")
            return "system"
        drift = (utc - datetime.now(timezone.utc)).total_seconds()
        # W6 rule on this function's own reads (a window-off unit's gate reads
        # nothing, so the gate counter says nothing about the clock).
        if not cfg.set_system_clock_from_spotter:
            print(f"[SUP] save_local time read {utc.isoformat()} (clock stepping off in config)")
        elif self.save_time_reads > 0 and abs(drift) < CLOCK_STEP_MIN_DRIFT_S:
            print(f"[SUP] save_local time read {utc.isoformat()}: drift {drift:+.1f}s, "
                  "clock not stepped")
        else:
            try:
                set_system_clock_utc(utc)
                print(f"[SUP] save_local time read {utc.isoformat()}: clock set")
            except Exception as exc:
                print(f"[SUP][WARN] save_local clock step failed: {exc}")
        self.save_time_reads += 1
        return "spotter"

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
        # b.6a: a one-shot override lives for one action only.
        if self._save_quality_base is None:
            self._save_quality_base = self.save_quality
        self.save_quality = self._save_quality_base
        self.one_shot_vtx = self.one_shot_output = self.one_shot_kv = None
        daemon = self.owner.daemon if self.owner else None
        if daemon is not None:
            sleep_fn(0.0)
            events = daemon.process_pending()
            summary["command_events"].extend(e["action"] for e in events)
            if events and self.reresolve_fn is not None:
                print(f"[SUP] boot drain: {len(events)} command(s) applied this boot")
                settings = self._reresolve(settings, summary)
                self.save_quality = self._save_quality_base   # a drained set applies now
        other = self._trigger_media()
        if other and other != self.media:
            # b.6b: a trg for the OTHER media is not this action's; it stays armed
            # and the next decision point runs that media's action (pick_action).
            print(f"[SUP] pending trg names media {other}: not serviced by this "
                  f"{self.media} action (next decision point)")
        elif self.command_state is not None:
            # Stills (W4 ordering) and, since W5, video: a trg is serviced at
            # this decision point for both media.
            settings, flags = cmd_hooks.service_pending_trigger(
                settings, self.command_state, transmit=self.transmit)
            if settings.get("trigger"):
                settings = self._apply_one_shot(settings, daemon)
                summary["trigger"] = settings["trigger"]
                if self.media == "video" and settings.get("source_image_path"):
                    # trg 3/4 name a stills reference image; a video unit has
                    # none, so it records and sends a clip (window bypassed).
                    print(f"[SUP][WARN] trg {settings['trigger'].get('value')} names a stills "
                          "reference; a video unit records and sends a clip instead")
        self.settings = settings
        return settings, flags

    def _apply_one_shot(self, settings, daemon):
        """b.6a: the trigger's kv, re-validated against the config as it is NOW
        (it may have changed since the trg), applied to a per-action copy.
        A kv that no longer validates is dropped loudly and the trigger runs
        without it; the overlay and the state file never see it (§9)."""
        trig = settings["trigger"]
        kv = trig.get("kv") or {}
        if not kv or self.one_shot_fn is None:
            return settings
        dispatch = getattr(daemon, "v9_dispatch", None)
        try:
            if dispatch is not None:
                kv, _media = dispatch.one_shot(kv, trig.get("value"))
            fresh = self.one_shot_fn(kv)
        except Exception as exc:
            print(f"[SUP][WARN] trg id={trig.get('id')} kv {kv} dropped at action time "
                  f"({type(exc).__name__}: {exc}); the trigger runs without it")
            return settings
        for key in ("trigger", "source_image_path", "video"):
            if key in settings and key not in fresh:
                fresh[key] = settings[key]
        fresh["trigger"] = trig
        self.one_shot_kv = dict(kv)
        if "mode.output" in kv:
            self.one_shot_output = kv["mode.output"]
        if "still.save.quality" in kv:
            if self._save_quality_base is None:
                self._save_quality_base = self.save_quality
            self.save_quality = int(kv["still.save.quality"])
        print(f"[SUP] trg id={trig.get('id')}: one action with {kv} (not persisted)")
        return fresh

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
        if self.v9_replies is not None:
            import rc_telemetry
            import rc_uplink_messages
            rc_telemetry.WS_EXTRA_FN = None
            rc_uplink_messages.START_EXTRA_FN = None
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
        "output": (boot.one_shot_output or boot.output) if boot.transmit else "none",
        "trigger_id": (trig or {}).get("id") if isinstance(trig, dict) else None,
        "stage": s.get("stage"), "media_key": s.get("media_key") or boot.media_key,
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
        summary = boot.pick_action(action_fn)(boot)
        boot.note_guards(summary)
        n = 1
        while True:
            n, summary = _w10_actions(boot, action_fn, n, summary)
            # b.6c: hld / keep-alive keep a per_boot unit up after its tail; a trg
            # that arrives meanwhile and fits the budget is the next W10 action.
            daemon = boot.owner.daemon if boot.owner else None
            if boot.stay_awake(daemon, boot._sleep_fn or time.sleep) != "trigger":
                break
        boot.note_guards(None)          # S4b review R2-10: the hold/keep-alive time counts
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


def _w10_actions(boot, action_fn, n, summary):
    """W10 (O3): every trg that fits this boot's budget runs as another action
    on the SAME budget (G1). -> (action count, last summary)."""
    while boot.w10_trigger_fits():
        write_action_log(boot)          # the finished action's line
        n += 1
        trg = boot._pending_trigger()
        print(f"[SUP] W10: trg id={trg.get('id')} heard in the listen tail fires this "
              f"boot (action {n}; {boot.budget.remaining_s():.0f}s of budget left)")
        # Commands applied since the boot drain (tail) govern this action:
        # the overlay is re-read onto the YAML base (same budget, G1).
        if boot.reresolve_fn is not None:
            boot.settings = boot._reresolve(boot.settings, boot.summary or {})
        summary = boot.pick_action(action_fn)(boot)
        boot.note_guards(summary)
        if boot._pending_trigger() is trg:
            boot.w10_stuck = trg
            print(f"[SUP][ERR] W10: trg id={trg.get('id')} still armed after its action "
                  "(consume not persisted); not firing it again this boot")
    return n, summary


# ---------------------------------------------------------------------------
# stay_on (Sprint26 S3b; DESIGN_supervisor.md §4, PLAN_S3b.md H2-H6)
# ---------------------------------------------------------------------------

IDLE_TICK_S = 0.2            # §4: commands processed every 0.2 s while idle
IDLE_HEAL_S = 600.0          # O5 (Nick 2026-09-25): pending heals go out after 10 min idle
GUARD_NOTE_S = 60.0          # S4: idle uptime is added to guarded keys at most this often
EXIT_ARGS = 2                # stay_on cannot run as invoked (no daemon: no --transmit)
EXIT_CRASH = 70              # H7: stay_on failed (watchdog, error); wrapper restarts, backoff
EXIT_RSS = 71                # H9: RSS ceiling reached; clean exit, wrapper restarts in 5 s
EXIT_CONFIG = 72             # S4 G10g: a next-boot setting changed; wrapper restarts in 5 s,
                             # not counted toward the crash-loop cap

# H6: SIGTERM sets a flag only (stay_on). Raising inside subprocess.run would
# kill a capture or the halt script mid-way; the loop stops at its next safe
# point instead. per_boot never installs this (tools rely on "SIGTERM = no
# finally = no halt" there).
STOP = {"requested": False, "signal": None}


def _on_sigterm(signum, _frame):
    # "Ignored once halting has started" (§4) is vacuous here: a stay_on
    # process never halts, and the crash-loop fallback (per_boot) never
    # installs this handler.
    STOP["requested"] = True
    STOP["signal"] = signum


def install_stop_flag():
    import signal
    import rc_capture
    STOP.update(requested=False, signal=None)
    previous = signal.signal(signal.SIGTERM, _on_sigterm)
    rc_capture.stop_check = stop_requested      # H6: bounded wait in a capture retry
    print("[SUP] SIGTERM -> stop at the next safe point (no halt)")
    return previous


def uninstall_stop_flag(previous):
    import signal
    import rc_capture
    rc_capture.stop_check = None
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


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


def _guarded_settings_fn(settings_fn, fallback):
    """settings_fn that never raises (review S3b #4): a failed overlay
    re-resolve keeps the last good settings, loudly, as Boot._reresolve does.
    A raise here would kill a stay_on unit (exit 1 at start: no restart)."""
    last_good = {"s": fallback}

    def fresh():
        try:
            last_good["s"] = settings_fn()
        except Exception as exc:
            print(f"[SUP][ERR] settings re-resolve failed ({type(exc).__name__}: {exc}); "
                  "keeping the last good settings")
        return copy.deepcopy(last_good["s"])
    return fresh


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
        summary = boot.pick_action(action_fn)(boot, settings)
    except Exception as exc:
        # One failed action is not a failed process: logged, and the loop
        # goes on (the next trg or slot may well succeed).
        error = f"{type(exc).__name__}: {exc}"
        print(f"[SUP][ERR] action {n} failed: {error}")
        summary = boot.summary or {}
    write_action_log(boot, error, n=n, kind=kind)
    boot.note_guards(summary)
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


def send_pending_heals(daemon, settings, summary, budget, tx_open_fn, clock, sleep_fn):
    """The heal part of a transmitting action on its own (O5 idle pass; S3c
    save_local actions, PLAN_S3c.md §5 C14): plan (<= HEAL_CAP_PER_WAKE chunks,
    newest first), the lane wait when the unit uses the transmit phase, the
    chunks (paced, pump-only), then one <HL> per key. Runs on the CALLER's
    budget and summary (a per_boot save_local action keeps its one budget, G1).
    The pass counts as a wake for wakes_left. -> messages planned (chunks +
    <HL>), or None when there is no heal wake (no daemon). Raises on a send
    failure."""
    import rc_heal
    import rc_transmit_phase
    heals = rc_heal.begin_wake(daemon, settings, summary,
                               pump_fn=cmd_hooks.make_pending_pump_fn(daemon, summary))
    if heals is None:
        return None
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
    return heals.planned_msgs


def save_local_heals(daemon, settings, summary, budget, *, transmit, tx_open_fn, clock,
                     sleep_fn, run="per_boot"):
    """S3c §5 C14: a save_local action sends the heals that are pending (media
    sent earlier; <= HEAL_CAP_PER_WAKE chunks, then <HL>), so they neither stall
    nor stop ageing, on the action's own budget (G1). -> True when it put
    anything on the uplink. Never raises (a heal must not cost the save)."""
    if not transmit or daemon is None:
        return False
    if run != "per_boot":
        # stay_on: the O5 idle pass sends them (a save_local action is not an
        # uplink, so the idle timer runs); a heal wake per action would age
        # wakes_left once a minute at interval 60 (review S3c #4).
        return False
    state = getattr(daemon, "state", None)
    if not (getattr(state, "pending_heals", None) or getattr(daemon, "heal_events", None)):
        return False
    try:
        planned = send_pending_heals(daemon, settings, summary, budget,
                                     tx_open_fn, clock, sleep_fn)
    except Exception as exc:
        print(f"[HEAL][WARN] save_local heal slot failed ({type(exc).__name__}: {exc})")
        return True                      # something may have gone out: count it
    return bool(planned)


def heal_pass(boot, daemon, settings, tx_open_fn, clock, sleep_fn):
    """O5 (PLAN_S3b.md H11): send pending heals with no capture, on a fresh
    budget per pass (send_pending_heals). -> its summary. Never raises."""
    summary = {"command_events": [], "stage": "heal_pass"}
    boot.summary = summary
    boot.end_line = None             # the last action's end line is not this pass's
    budget = CycleBudget(settings["budget_seconds"], settings["pacing_delay_seconds"],
                         clock=clock)
    boot.budget = budget
    try:
        if send_pending_heals(daemon, settings, summary, budget, tx_open_fn, clock,
                              sleep_fn) is not None:
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
    settings_fn = _guarded_settings_fn(settings_fn, boot.settings)
    settings = settings_fn()
    boot.settings = settings
    print(f"[SUP] stay_on: media={boot.media} interval_s={interval_s} "
          f"({'trigger-only' if interval_s == 0 else 'scheduled + trg'}) "
          f"heartbeat_s={heartbeat_s}{' (off)' if heartbeat_s == 0 else ''}")
    if settings.get("power_halt_enabled"):
        print("[SUP][WARN] stay_on: power.halt is IGNORED (a stay_on process never halts)")
    previous_handler = install_stop_flag()
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
        boot.note_guards(None)          # S4b review R2-10: uptime before an exit counts
        boot.finish(halt=False)
        guard.marker_clear()
        uninstall_stop_flag(previous_handler)
        print(f"[SUP] stay_on exit {code}")
    return code


def _loop(boot, daemon, action_fn, settings_fn, interval_s, heartbeat_s, heartbeat_fn,
          clock, sleep_fn, heal_tx_open_fn=None):
    state = boot.command_state
    n = 0
    next_due = None
    if interval_s > 0:
        # H2: first scheduled action at boot. After a wrapper restart in the
        # same boot (review S3b #2) the last slot's start is honoured, so a
        # crash/RSS restart never runs an extra action at once.
        last = guard.sched_load()
        now = clock()
        next_due = now if last is None or last > now else max(now, last + interval_s)
        if last is not None and next_due > now:
            print(f"[SUP] restart: next scheduled action in {next_due - now:.0f}s "
                  "(the last slot started in this boot)")
    last_uplink = clock()
    last_send = clock()          # O5: the last action or heal pass (heartbeats do not count)
    skip_run = False             # H3: the last scheduled action was a window skip
    stuck_trg = None             # a trg whose consume could not be persisted
    last_guard_note = clock()
    while not stop_requested():
        _idle_tick(daemon, clock, sleep_fn)
        if clock() - last_guard_note >= GUARD_NOTE_S:
            # S4b review #1: idle time counts toward the guarded keys' 2 h
            # backstop (persisted at most once a minute, not every tick).
            boot.note_guards(None)
            last_guard_note = clock()
        restart = getattr(getattr(daemon, "v9_dispatch", None), "restart_requested", None)
        if restart:
            # G10g: a next-boot key (mode, UART, topic, ...) takes effect through a
            # clean process restart; everything that arrived this tick is persisted
            # and its acks queued (flushed by finish), so several sets coalesce.
            print(f"[SUP] config restart: {', '.join(restart)} changed (next boot); "
                  f"exit {EXIT_CONFIG} (the wrapper restarts in 5 s)")
            return EXIT_CONFIG
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
            if kind == "scheduled":
                guard.sched_save(now)
            summary = _run_action(boot, action_fn, settings_fn(), n, kind, quiet)
            skipped = summary.get("schedule_allowed") is False
            # S3c (§5 C1): an action that sent nothing (a save_local action with
            # no heals) is not an uplink: it moves neither the heartbeat nor the
            # O5 idle-heal timer, or a unit saving every minute would never beat.
            # Transmitting actions never set the key (their summaries unchanged).
            # A save_local action that raised has no key: it sent nothing
            # (review S3c #1), so a failing unit keeps its heartbeat.
            uplinked = summary.get("uplinked", not boot.save_local)
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
            if uplinked and not (skipped and quiet):
                last_uplink = clock()
            if uplinked and not skipped:
                last_send = clock()      # O5 idle = no action or heal SENT (review S3b #5)
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
            lane = getattr(daemon, "lane_wait_s", None)
            if lane is not None and lane() > 0:
                sleep_fn(IDLE_TICK_S)        # b.8: heartbeat after the boundary guard
                continue
            try:
                heartbeat_fn(settings_fn())
                print(f"[SUP] heartbeat <WS a=idle> after {now - last_uplink:.0f}s idle")
                boot.note_guards(None)       # idle time counts toward save_local's 2 h
            except Exception as exc:
                print(f"[SUP][WARN] heartbeat failed ({type(exc).__name__}: {exc})")
            last_uplink = clock()
            guard.rotate_stdout_if_big()
            continue
        sleep_fn(IDLE_TICK_S)
    return 0
