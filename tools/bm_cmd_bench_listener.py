#!/usr/bin/env python3
r"""
bm_cmd_bench_listener.py -- BENCH ONLY: boot-time bmcam/cmd listener for mote
command-buffering tests (nereus_cam mote firmware, cmdWaitMs, 2026-09-25).

PURPOSE
    Replace the ~8 min video cycle with a short, zero-image boot so the
    Spotter can power-cycle the bus fast (e.g. 5 min on / 1 min off). Each boot:
    wait until a chosen uptime (to emulate production subscribe timing), subscribe
    to bmcam/cmd with the PRODUCTION CommandDaemon, log every command with Pi
    uptime + UTC, answer queries (cfg -> Spotter console, ping -> cellular ack)
    exactly like production, then halt itself BEFORE the bus cut.

    Reused unchanged: bm_serial, command_daemon, rc_command_hooks (query
    renderer, heal validator, ack flush), rc_power_halt. Only the timing and
    the per-command uptime log are new.

INPUTS
    --config-path      camera_schedule.yaml (default: the unit's production one)
    --subscribe-at     Pi uptime (s) to subscribe at. Default 21 = production
                       video cycle on bmcam004, 2026-09-25 (`[BOOT] cmd_subscribed
                       uptime_s=21.48`). Sprint23 measured ~38-40 s on the older
                       stills path; use 40 for that worst case. 0 = ASAP.
    --halt-at          Pi uptime (s) to stop listening and halt. Must end before
                       the Spotter cuts bus power (5 min window -> 240 default).
    --no-halt          listen until --halt-at, then exit without halting.

OUTPUTS
    stdout (cron redirects it to cron_logs/bench_listener_<ts>.log):
      [BENCH] subscribed uptime_s=..
      [BENCH] cmd id=.. c=.. action=.. uptime_s=.. utc=..
      [BENCH] summary ... then the production [CMD] stopped line and halt.
    One JSON line per boot appended to cron_logs/bench_listener_events.jsonl.

RUN (on the Pi, from the deployed runtime dir so it imports the unit's modules):
    cd ~/BM_Devel_Pi && python3 -u bm_cmd_bench_listener.py --subscribe-at 21 --halt-at 240
    Bench cron (replaces the production @reboot line; BACK UP crontab first):
    @reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /bin/bash -c 'cd /home/pi/BM_Devel_Pi && python3 -u bm_cmd_bench_listener.py >> cron_logs/bench_listener_$(date -u +\%Y\%m\%dT\%H\%M\%SZ).log 2>&1'

ASSUMPTIONS / LIMITATIONS
    - Requires bm_commands.enabled in the YAML (same gate as production).
    - Uses the unit's real command state file: ids are deduped across boots, so
      every test command needs a fresh id. Settings commands WOULD persist and
      govern the next production boot -- send only cfg/help/ping in these tests.
    - Pi uptime ~= time since bus power-on (the Pi is powered through the mote),
      so uptime is the clock for "was it delivered at cmdWaitMs?".
    - Halt uses the YAML power_halt settings (+ hlt overlay), like production.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.expanduser("~/BM_Devel_Pi"))

import rc_command_hooks as cmd_hooks  # noqa: E402
from command_daemon import load_bm_commands_config  # noqa: E402
from command_state import CommandState  # noqa: E402
from rc_power_halt import perform_power_halt  # noqa: E402
from rc_progressive_jpeg import (  # noqa: E402
    DEFAULT_CONFIG_PATH, _apply_command_overlay, resolve_rc_settings)


def uptime_s():
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def log_events(events, record):
    """Print + record one line per command the daemon just processed."""
    for e in events:
        r = e.get("result") or {}
        row = {"id": r.get("id"), "c": r.get("cmd"), "v": r.get("value"),
               "action": e.get("action"), "uptime_s": round(uptime_s(), 2),
               "utc": utc_now()}
        record["commands"].append(row)
        print(f"[BENCH] cmd id={row['id']} c={row['c']} v={row['v']} "
              f"action={row['action']} uptime_s={row['uptime_s']} utc={row['utc']}",
              flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1].strip())
    p.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    p.add_argument("--subscribe-at", type=float, default=21.0)
    p.add_argument("--halt-at", type=float, default=240.0)
    p.add_argument("--no-halt", action="store_true")
    args = p.parse_args(argv)

    record = {"boot_utc": utc_now(), "start_uptime_s": round(uptime_s(), 2),
              "subscribe_at": args.subscribe_at, "halt_at": args.halt_at,
              "commands": []}
    print(f"[BENCH] start utc={record['boot_utc']} uptime_s={record['start_uptime_s']} "
          f"subscribe_at={args.subscribe_at} halt_at={args.halt_at} "
          f"no_halt={args.no_halt}", flush=True)

    settings = resolve_rc_settings(args.config_path)
    bm_cfg = load_bm_commands_config(args.config_path)
    if not bm_cfg["enabled"]:
        print("[BENCH][ERROR] bm_commands.enabled is false; nothing to test", flush=True)
        return 2
    state = CommandState(path=bm_cfg["state_path"])
    settings = _apply_command_overlay(settings, state)

    wait = args.subscribe_at - uptime_s()
    if wait > 0:
        print(f"[BENCH] waiting {wait:.1f}s to subscribe at uptime {args.subscribe_at}", flush=True)
        time.sleep(wait)

    # Same factory production uses: opens the UART once, wires cfg/help
    # rendering, wap and the rsd heal validator.
    daemon = cmd_hooks.default_daemon_factory(settings, bm_cfg, state)
    summary = {"command_events": []}
    rc = 0
    try:
        daemon.start()
        record["subscribed_uptime_s"] = round(uptime_s(), 2)
        print(f"[BENCH] subscribed uptime_s={record['subscribed_uptime_s']} utc={utc_now()}", flush=True)
        while uptime_s() < args.halt_at:
            log_events(daemon.process_pending(), record)
            daemon.drain_acks()
            daemon.drain_console()
            time.sleep(0.2)
    except Exception as exc:
        print(f"[BENCH][ERROR] listener failed: {exc}", flush=True)
        rc = 1
    finally:
        log_events(daemon.process_pending(), record)
        cmd_hooks.shutdown(daemon, summary, print)
        try:
            daemon.bm.uart.close()
        except Exception as exc:
            print(f"[BENCH][WARN] uart close failed: {exc}", flush=True)
        record["end_uptime_s"] = round(uptime_s(), 2)
        print(f"[BENCH] summary commands={len(record['commands'])} "
              f"end_uptime_s={record['end_uptime_s']}", flush=True)
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "cron_logs", "bench_listener_events.jsonl"), "a") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception as exc:
            print(f"[BENCH][WARN] events.jsonl write failed: {exc}", flush=True)
        if not args.no_halt:
            cmd_hooks.boot_mark("halt")
            perform_power_halt(
                enabled=settings["power_halt_enabled"],
                dry_run=settings["power_halt_dry_run"],
                mode=settings["power_halt_mode"],
                script_path=settings["power_halt_script_path"],
            )
    return rc


if __name__ == "__main__":
    sys.exit(main())
