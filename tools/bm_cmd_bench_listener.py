#!/usr/bin/env python3
r"""
bm_cmd_bench_listener.py -- BENCH ONLY: boot-time bmcam/cmd listener for mote
command-buffering tests (nereus_cam mote firmware, cmdWaitMs, 2026-09-25).
Commands v9 since Sprint26 S4 c.3 (DESIGN_supervisor.md §6.1-6.2).

PURPOSE
    Replace the ~8 min video cycle with a short, zero-image boot so the
    Spotter can power-cycle the bus fast (e.g. 5 min on / 1 min off). Each boot:
    wait until a chosen uptime (to emulate production subscribe timing), subscribe
    to bmcam/cmd with the PRODUCTION CommandDaemon + the v9 dispatcher
    (command_v9.Dispatcher, as the supervisor wires it), log every command with
    Pi uptime + UTC, answer exactly like production (get -> console + <CF>,
    help -> console, ping -> ack; cellular copies only for remote/service-range
    ids), then halt itself BEFORE the bus cut.

    Reused unchanged: bm_serial, command_daemon, command_v9, command_state_v9,
    supervisor_config, rc_command_hooks (ack/console flush), rc_power_halt. Only
    the timing and the per-command uptime log are new.

INPUTS
    --config-path      camera_schedule.yaml beside camera_config.yaml (the v2
                       file must exist: v9 needs a config-v2 unit whose command
                       state is bm_command_state_v2; otherwise exit 2)
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
      [BENCH] cmd id=.. c=.. action=.. [e=..] uptime_s=.. utc=..
      [BENCH] summary ... then the production [CMD] stopped line and halt.
    One JSON line per boot appended to cron_logs/bench_listener_events.jsonl.

RUN (on the Pi, from the deployed runtime dir so it imports the unit's modules):
    cd ~/BM_Devel_Pi && python3 -u bm_cmd_bench_listener.py --subscribe-at 21 --halt-at 240
    Bench cron (replaces the production @reboot line; BACK UP crontab first):
    @reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /bin/bash -c 'cd /home/pi/BM_Devel_Pi && python3 -u bm_cmd_bench_listener.py >> cron_logs/bench_listener_$(date -u +\%Y\%m\%dT\%H\%M\%SZ).log 2>&1'
    Test commands (console ids 1-99999 answer on the console only; remote ids
    1000000+ also ack over cellular and move the unit's high-water mark):
    bm pub bmcam/cmd {"id":1001,"c":"ping"} 1 1
    bm pub bmcam/cmd {"id":1002,"c":"get","k":["mode"]} 1 1
    bm pub bmcam/cmd {"id":1003,"c":"help"} 1 1

ASSUMPTIONS / LIMITATIONS
    - Requires commands.enabled (bm_commands.enabled in the render), like
      production, and a migrated unit (v2 config + v2 command state).
    - Uses the unit's real v9 state file: every id is cached (a re-sent id gets
      its original answer + d:1), so every test command needs a fresh id. set /
      reset / trg / hld WOULD change or act on the next production boot -- send
      only ping / get / help in these tests.
    - The guard boot counter is NOT advanced (this is not a --transmit boot).
    - Pi uptime ~= time since bus power-on (the Pi is powered through the mote),
      so uptime is the clock for "was it delivered at cmdWaitMs?".
    - Halt uses the effective config's power.halt.* (YAML + command overlay,
      rendered by supervisor_config.apply), like the supervisor path.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# On the Pi this file runs from ~/BM_Devel_Pi (the runtime dir); in the repo
# the runtime modules are in ../BM_Devel_Pi (appended last, so a deployed
# runtime always wins).
for _p in (os.path.expanduser("~/BM_Devel_Pi"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.append(os.path.join(os.path.dirname(HERE), "BM_Devel_Pi"))

DEFAULT_CONFIG_PATH = "/home/pi/BM_Devel_Pi/camera_schedule.yaml"


def uptime_s():
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def _verb(payload):
    """The `c` of a raw payload, or "?" (the dispatcher did the real parse)."""
    try:
        import command_wire as W
        return str(W.strict_loads(payload).get("c", "?"))
    except Exception:
        return "?"


def log_events(events, record):
    """Print + record one line per command the v9 dispatcher just handled.
    Event: {"action", "id"[, "e"], "payload"} (command_v9.Dispatcher.handle)."""
    for e in events:
        row = {"id": e.get("id"), "c": _verb(e.get("payload", b"")),
               "action": e.get("action"), "e": e.get("e"),
               "uptime_s": round(uptime_s(), 2), "utc": utc_now()}
        record["commands"].append(row)
        err = f" e={row['e']}" if row["e"] else ""
        print(f"[BENCH] cmd id={row['id']} c={row['c']} action={row['action']}{err} "
              f"uptime_s={row['uptime_s']} utc={row['utc']}", flush=True)


def load_v9(config_path):
    """-> (settings, bm_cfg, state, base, env, base_source) for a migrated
    config-v2 unit, or raise RuntimeError with the reason (exit 2).
    Mirrors rc_progressive_jpeg.main's supervisor v9 setup (S4 b.1/b.5)."""
    import command_state_v9
    import command_v9
    import config_migrate
    import config_v2
    import config_validate
    import supervisor_config
    from command_daemon import load_bm_commands_config
    from rc_progressive_jpeg import resolve_rc_settings

    path, boot = config_v2.select_for_legacy_runtime(config_path, "v2", persist=False)
    if boot is None or path is None:
        raise RuntimeError(f"no usable config v2 beside {config_path} (v9 needs one)")
    base = dict(boot.values or {})
    if base.get("mode.media") == "video_logger":
        raise RuntimeError("mode.media video_logger has no v9 command path")
    if not supervisor_config.is_migrated(base):
        raise RuntimeError(f"{supervisor_config.state_path_for(base)} is not a v2 command "
                           "state (unmigrated unit: v8 only)")
    env = config_validate.probe_env([base.get("schedule.timezone")])
    try:
        supervisor_config.apply(boot, path, env=env)     # effective render (G3)
    except Exception as exc:        # never brick: the YAML base render, as production
        print(f"[BENCH][WARN] effective config failed ({type(exc).__name__}: {exc}); "
              "running the YAML base", flush=True)
    settings = resolve_rc_settings(path)
    bm_cfg = load_bm_commands_config(path)
    state = command_state_v9.V9State(supervisor_config.state_path_for(base),
                                     trigger_validator=command_v9.check_persisted_kv)
    for key, old, new in state.fold_v8(config_migrate.overlay_from_v8):
        print(f"[CMD] v8 fold: {key}: {old!r} -> {new!r}", flush=True)
    base_source = {k: v for k, v in (getattr(boot.config, "source", None) or {}).items()
                   if v in ("yaml", "default")}
    return settings, bm_cfg, state, base, env, base_source


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1].strip())
    p.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    p.add_argument("--subscribe-at", type=float, default=21.0)
    p.add_argument("--halt-at", type=float, default=240.0)
    p.add_argument("--no-halt", action="store_true")
    args = p.parse_args(argv)

    import command_v9
    import rc_command_hooks as cmd_hooks
    import supervisor_config
    from rc_power_halt import perform_power_halt

    record = {"boot_utc": utc_now(), "start_uptime_s": round(uptime_s(), 2),
              "subscribe_at": args.subscribe_at, "halt_at": args.halt_at,
              "commands": []}
    print(f"[BENCH] start utc={record['boot_utc']} uptime_s={record['start_uptime_s']} "
          f"subscribe_at={args.subscribe_at} halt_at={args.halt_at} "
          f"no_halt={args.no_halt} commands=v9", flush=True)

    try:
        settings, bm_cfg, state, base, env, base_source = load_v9(args.config_path)
    except RuntimeError as exc:
        print(f"[BENCH][ERROR] {exc}", flush=True)
        return 2
    if not bm_cfg["enabled"]:
        print("[BENCH][ERROR] commands.enabled is false; nothing to test", flush=True)
        return 2

    wait = args.subscribe_at - uptime_s()
    if wait > 0:
        print(f"[BENCH] waiting {wait:.1f}s to subscribe at uptime {args.subscribe_at}", flush=True)
        time.sleep(wait)

    # Same factory production uses: opens the UART once, wires wap and the rsd
    # heal validator (a V9State: no v8 help/cfg renderer). Then the v9 replies
    # and dispatcher, as rc_supervisor wires them (b.2c).
    daemon = cmd_hooks.default_daemon_factory(settings, bm_cfg, state)
    daemon.v9 = supervisor_config.v9_replies(base, env=env)
    daemon.v9_dispatch = command_v9.Dispatcher(
        daemon, state, base, env=env, service_key=command_v9.load_service_key(),
        base_source=base_source)
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
            with open(os.path.join(HERE, "cron_logs", "bench_listener_events.jsonl"), "a") as fh:
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
