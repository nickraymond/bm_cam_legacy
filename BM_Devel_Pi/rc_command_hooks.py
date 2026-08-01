#!/usr/bin/env python3
# filename: rc_command_hooks.py
# description: Sprint10 — glue between the RC cycle and the command daemon.
"""
Sprint10 — the RC-orchestrator side of the command daemon wiring.

rc_progressive_jpeg.run_cycle() stays the cycle narrative; every
daemon-specific block lives here so the orchestrator keeps to the ~600
line rule and the Sprint08/09 flow stays readable. Each hook is a small
function taking the daemon (or None) — every one is a no-op / neutral
value when the daemon is off, so call sites need no branching.

See command_daemon.py for the concurrency contract and DESIGN
D5/D11/D12/D13 for the decisions these hooks implement.
"""

from bm_serial import BristlemouthSerial, load_uart_config
from command_bindings import (
    apply_trigger,
    describe_overrides,
    overlay_camera_controls,
    overlay_rc_settings,
    stranding_warnings,
)
from command_daemon import CommandDaemon


def make_query_render_fn(settings, state, topic):
    """Sprint13: closure the daemon calls to answer help/cfg queries.

    Captures the RESOLVED settings dict (post-overlay — the same dict
    --print-config prints, so cfg can never disagree with it) and the
    live CommandState (so a cfg sent after e.g. twn 2 in the same listen
    window shows the new override as next-boot truth).

    Returns None if command_help cannot import — a broken/missing help
    renderer must degrade to "queries ack, no output", never kill the
    capture cycle (found the hard way on bmcam003, 2026-08-01: the
    module missing from the deploy manifest failed the whole boot)."""
    try:
        from command_help import render_cfg, render_help
    except Exception as exc:
        print(f"[CMD][WARN] command_help unavailable ({exc}); "
              "help/cfg will ack without console output")
        return None

    def render(cmd):
        if cmd == "help":
            return render_help(topic=topic)
        if cmd == "cfg":
            return render_cfg(settings, state,
                              settings.get("camera_controls_override"))
        return []

    return render


def default_daemon_factory(settings, bm_commands_cfg, state):
    """Open the UART ONCE for the whole cycle (D11) and build the command
    daemon on it. Installs the shared BristlemouthSerial as
    process_image_v2's instance so wake status/telemetry/transmit all use
    the same port. The uart read timeout is required by the reader thread."""
    import serial as pyserial
    import process_image_v2

    port, baudrate = load_uart_config(settings["config_path"])
    uart = pyserial.Serial(port, baudrate, timeout=0.1)
    bm = BristlemouthSerial(uart=uart)
    process_image_v2.bm = bm
    print(f"[CMD] shared UART open: {port}@{baudrate} (single port owner)")
    return CommandDaemon(
        bm, state, topic=bm_commands_cfg["topic"],
        query_render_fn=make_query_render_fn(
            settings, state, bm_commands_cfg["topic"]),
    )


def apply_command_overlay(settings, state, load_controls_fn):
    """Re-resolve the command overlay (D13): roi/win onto settings,
    foc/awb/exp onto the camera_controls override. Returns new settings.
    load_controls_fn(config_path) supplies the YAML island dict."""
    settings, overrides = overlay_rc_settings(settings, state)
    for line in describe_overrides(overrides):
        print(line)
    # Sprint12: an active hlt override states its stranding trade loudly.
    for line in stranding_warnings(state):
        print(line)
    controls = overlay_camera_controls(
        load_controls_fn(settings["config_path"]), state
    )
    if controls is not None:
        settings["camera_controls_override"] = controls
    if state.touched:
        print(f"[CMD] overlay active: settings={state.settings} "
              f"touched={sorted(state.touched)}")
    return settings


def gate_kwargs_for(daemon, settings=None):
    """kwargs for should_transmit_now_from_schedule: with a daemon, the
    Spotter-time read rides the shared port instead of opening its own.

    Sprint12 (D-S12-6): when a COMMANDED window is active (twn), pass it
    as an explicit override — the gate re-reads the YAML itself and would
    otherwise never see the overlay. No commanded window -> no key, so
    the un-commanded path stays byte-identical (D14). Sprint13: a
    commanded timezone (tmz) rides the same pattern.
    """
    kwargs = {}
    if settings is not None and str(
            settings.get("window_source", "")).startswith("command"):
        kwargs["window_override"] = (
            settings["window_start"], settings["window_end"])
    if settings is not None and str(
            settings.get("timezone_source", "")).startswith("command"):
        kwargs["timezone_override"] = settings["timezone"]
    if daemon is None:
        return kwargs
    kwargs["read_spotter_utc_fn"] = (
        lambda timeout_seconds, port, baudrate, verbose=False:
            daemon.wait_for_spotter_utc(timeout_seconds))
    return kwargs


def service_pending_trigger(settings, command_state, transmit):
    """Consume + apply the pending one-shot trigger (D-S12-3/4/5).

    Only a --transmit boot services triggers: bench/manual runs must not
    burn an operator's armed trigger (and a capture_transmit trigger on a
    no-transmit invocation could not honour its action anyway — cron
    production boots always carry --transmit). Returns (settings, flags)
    where flags = {"skip_time_window": bool, "capture_only": bool}.
    """
    flags = {"skip_time_window": False, "capture_only": False}
    if command_state is None:
        return settings, flags
    if command_state.pending_trigger is not None and not transmit:
        print(f"[CMD] pending trigger {command_state.pending_trigger} NOT "
              "serviced (not a --transmit boot); it stays armed")
        return settings, flags
    trigger = command_state.consume_trigger()
    if trigger is None:
        return settings, flags
    settings, flags, lines = apply_trigger(settings, trigger)
    for line in lines:
        print(line)
    return settings, flags


import time as _time

# Bound on the end-of-cycle paced ack flush: enough for a 12-ack burst
# at the 1.0 s pacing floor, trivial against the power budget.
FINAL_ACK_FLUSH_S = 15.0

# Sprint11 C4: seconds of the cycle budget the post-transmit tail must
# leave untouched, so the halt and the final state writes always complete
# before the Spotter cuts bus power. The tail is a nice-to-have; landing
# the image and halting cleanly are not.
TAIL_SAFETY_S = 20.0


def drain_now(daemon, summary, clock=_time.monotonic):
    """Pick up pending commands and send acks (paced; idle-point drain).
    Sprint13: queued help/cfg console lines flush here too — an idle
    point is exactly where a long console print belongs."""
    if daemon is None:
        return 0
    summary["command_events"].extend(
        e["action"] for e in daemon.process_pending()
    )
    daemon.drain_console()
    return daemon.drain_acks(clock=clock)


def make_pending_pump_fn(daemon, summary):
    """Sprint11 C3: pump for the transmit pacing slots that touches NO wire.

    Commands arriving mid-burst are still parsed and PERSISTED immediately
    (so they govern the next boot even if this cycle dies), but their acks
    only queue -- nothing is submitted into the Spotter's 2-slot cellular
    queue while image chunks are flowing. Returns None when the daemon is
    off, so the transmit loop stays byte-identical.
    """
    if daemon is None:
        return None

    def pending_pump_fn():
        events = daemon.process_pending()
        summary["command_events"].extend(e["action"] for e in events)
        return len(events)

    return pending_pump_fn


def make_ack_drain_fn(daemon, summary, clock=_time.monotonic, defer=False):
    """ack_drain_fn for transmit pacing slots (D12), or None.

    Sprint11 C3/D5: with `defer` set, this returns None so no ack can be
    submitted between the first and last image chunk. An ack is an uplink
    message into the SAME 2-slot queue as the chunks -- acking mid-transmit
    manufactures exactly the collisions the pacing exists to avoid
    (incident 001; the soak's "scattered singles"). The acks are not lost:
    they flush in flush_acks() right after END, and again in the C4 tail.
    """
    if daemon is None or defer:
        return None

    def ack_drain_fn(max_n=1):
        summary["command_events"].extend(
            e["action"] for e in daemon.process_pending()
        )
        return daemon.drain_acks(max_n=max_n, clock=clock)

    return ack_drain_fn


def flush_acks(daemon, summary, clock=_time.monotonic, sleep_fn=_time.sleep,
               budget_s=FINAL_ACK_FLUSH_S, label="ack flush"):
    """Paced flush of every queued ack. Loops because drain_acks sends at
    most one ack per pacing interval -- a deferred burst (C3) needs several
    seconds to leave the wire without overflowing the Spotter queue.

    Never raises: this runs on the way to the halt.
    """
    if daemon is None:
        return
    try:
        deadline = clock() + float(budget_s)
        drain_now(daemon, summary, clock=clock)
        while daemon.pending_acks and clock() < deadline:
            sleep_fn(0.2)
            drain_now(daemon, summary, clock=clock)
        if daemon.pending_acks:
            print(f"[CMD][WARN] {daemon.pending_acks} ack(s) unsent after "
                  f"{label} ({budget_s:.0f}s); cloud re-send + dedupe "
                  "recover them next cycle")
    except Exception as exc:
        print(f"[CMD][WARN] {label} failed: {exc}")


def post_transmit_listen(daemon, bm_commands_cfg, summary, budget,
                         clock=_time.monotonic, sleep_fn=_time.sleep):
    """Sprint11 C4/D6 — the bounded listen tail that replaces the
    pre-capture window.

    Finding 006 is specific: the mailbox drain is triggered by the sync OUR
    OWN transmit initiates, and fires 1-4 min after the cycle ends. That is
    why bmcam000 took 0/10 commands during the Sprint10 soak -- it was
    always halted by then. A 194 s transmit only covers the front of that
    window; this tail covers the rest.

    BOUNDED, never open-ended: the halt is what makes the energy numbers
    work. The tail is additionally clamped to the cycle budget less
    TAIL_SAFETY_S, so it can never run into the Spotter's bus-power cut
    mid-write. If there is no room it is SKIPPED, loudly.

    Returns the seconds actually spent listening.
    """
    if daemon is None:
        return 0.0
    tail_s = float(bm_commands_cfg.get("post_transmit_listen_s", 0.0))
    if tail_s <= 0:
        return 0.0
    room_s = budget.remaining_s() - TAIL_SAFETY_S
    if room_s <= 0:
        print(f"[CMD] post-transmit tail SKIPPED: only "
              f"{budget.remaining_s():.0f}s of budget left "
              f"(need > {TAIL_SAFETY_S:.0f}s of halt margin)")
        summary["listen_tail_s"] = 0.0
        return 0.0
    actual_s = min(tail_s, room_s)
    if actual_s < tail_s:
        print(f"[CMD] post-transmit tail TRIMMED {tail_s:.0f}s -> "
              f"{actual_s:.0f}s by the cycle budget")
    events = daemon.listen_window(actual_s, clock=clock, sleep_fn=sleep_fn,
                                  label="post-transmit listen")
    summary["command_events"].extend(e["action"] for e in events)
    summary["listen_tail_s"] = actual_s
    return actual_s


def shutdown(daemon, summary, debug_print,
             clock=_time.monotonic, sleep_fn=_time.sleep):
    """Final pickup + PACED ack flush + reader stop; never raises (runs
    in finally, on the success AND the failure path). Sprint13: queued
    console lines flush BEFORE the ack flush budget starts — a help
    response must beat the halt the same way acks do."""
    if daemon is None:
        return
    try:
        daemon.drain_console(sleep_fn=sleep_fn)
    except Exception as exc:
        debug_print(f"console flush failed: {exc}")
    flush_acks(daemon, summary, clock=clock, sleep_fn=sleep_fn,
               label="final drain at halt")
    try:
        daemon.stop()
    except Exception as exc:
        debug_print(f"daemon stop failed: {exc}")
