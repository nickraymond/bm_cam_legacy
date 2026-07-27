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
    describe_overrides,
    overlay_camera_controls,
    overlay_rc_settings,
)
from command_daemon import CommandDaemon


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
    return CommandDaemon(bm, state, topic=bm_commands_cfg["topic"])


def apply_command_overlay(settings, state, load_controls_fn):
    """Re-resolve the command overlay (D13): roi/win onto settings,
    foc/awb/exp onto the camera_controls override. Returns new settings.
    load_controls_fn(config_path) supplies the YAML island dict."""
    settings, overrides = overlay_rc_settings(settings, state)
    for line in describe_overrides(overrides):
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


def gate_kwargs_for(daemon):
    """kwargs for should_transmit_now_from_schedule: with a daemon, the
    Spotter-time read rides the shared port instead of opening its own."""
    if daemon is None:
        return {}
    return {
        "read_spotter_utc_fn":
            lambda timeout_seconds, port, baudrate, verbose=False:
                daemon.wait_for_spotter_utc(timeout_seconds)
    }


def pre_capture_listen(daemon, bm_commands_cfg, summary, settings,
                       load_controls_fn, clock, sleep_fn):
    """The D5-corrected pre-capture listen window. Commands that land
    here govern THIS capture (roi/foc/awb/exp); a win change waits for
    the next cycle (budget already charged). Returns (settings, applied)."""
    if daemon is None:
        return settings, False
    listen_s = float(bm_commands_cfg.get("pre_capture_listen_s", 0))
    events = daemon.listen_window(listen_s, clock=clock, sleep_fn=sleep_fn)
    summary["command_events"].extend(e["action"] for e in events)
    applied = any(e["action"] == "applied" for e in events)
    if applied:
        settings = apply_command_overlay(settings, daemon.state, load_controls_fn)
    return settings, applied


import time as _time

# Bound on the end-of-cycle paced ack flush: enough for a 12-ack burst
# at the 1.0 s pacing floor, trivial against the power budget.
FINAL_ACK_FLUSH_S = 15.0


def drain_now(daemon, summary, clock=_time.monotonic):
    """Pick up pending commands and send acks (paced; idle-point drain)."""
    if daemon is None:
        return 0
    summary["command_events"].extend(
        e["action"] for e in daemon.process_pending()
    )
    return daemon.drain_acks(clock=clock)


def make_ack_drain_fn(daemon, summary, clock=_time.monotonic):
    """ack_drain_fn for transmit pacing slots (D12), or None."""
    if daemon is None:
        return None

    def ack_drain_fn(max_n=1):
        summary["command_events"].extend(
            e["action"] for e in daemon.process_pending()
        )
        return daemon.drain_acks(max_n=max_n, clock=clock)

    return ack_drain_fn


def shutdown(daemon, summary, debug_print,
             clock=_time.monotonic, sleep_fn=_time.sleep):
    """Final pickup + PACED ack flush + reader stop; never raises (runs
    in finally). The flush loops because drain_acks sends at most one
    ack per pacing interval — a late burst needs several seconds to
    leave the wire without overflowing the Spotter queue."""
    if daemon is None:
        return
    try:
        deadline = clock() + FINAL_ACK_FLUSH_S
        drain_now(daemon, summary, clock=clock)
        while daemon.pending_acks and clock() < deadline:
            sleep_fn(0.2)
            drain_now(daemon, summary, clock=clock)
        if daemon.pending_acks:
            print(f"[CMD][WARN] {daemon.pending_acks} ack(s) unsent at "
                  f"halt (flush window {FINAL_ACK_FLUSH_S:.0f}s elapsed); "
                  "cloud re-send + dedupe recover them next cycle")
    except Exception as exc:
        debug_print(f"final command drain failed: {exc}")
    try:
        daemon.stop()
    except Exception as exc:
        debug_print(f"daemon stop failed: {exc}")
