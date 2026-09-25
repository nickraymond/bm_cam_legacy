#!/usr/bin/env python3
# filename: config_dump.py
# description: Sprint26 S2b — every v1 config loader's resolved output as one JSON (`--print-config --json`).
"""
One machine-readable answer to "what exactly would this unit run with?".

`rc_progressive_jpeg.py --print-config --json` prints the output of EVERY v1
config loader for one YAML, plus the command overlay the unit would apply, as a
single JSON line (the last line of stdout). S2 uses it to prove config v2
changes nothing:
  - deploy parity: the same file through the old and the new runtime;
  - migration parity: a unit's v1 YAML vs its migrated v2 file rendered back
    to v1 (DESIGN_supervisor.md §8.3 S2b/S2f; PLAN_S2.md G2).

The keys match what the settings goldens record (tests/golden/run_scenario.py
`settings`), plus:
  env          what the readers that ignore --config-path see
               (BM_CAMERA_CONFIG_PATH: BristlemouthSerial, bm_port)
  wap_network  the network island `wap` reads from its hard-coded path
  overlaid     settings after the v8 command overlay, when commands are on
  loader_output  whatever the loaders printed (warnings), in order

Zero side effects: nothing is written, no port is opened, no network change.
Each loader is wrapped: a loader error is reported as {"error": "..."}, which
is itself a behaviour worth comparing.

Example (on a unit):
  cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config --json | tail -1
"""

import contextlib
import io
import json

SCHEMA = "print_config_json/1"


def _jsonable(obj):
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    return str(obj)


def collect(config_path):
    """Return {loader name: resolved output} for one YAML (see module doc)."""
    import bm_serial as bms
    import command_daemon
    import network_config
    import rc_command_hooks
    import rc_media_key
    import rc_progressive_jpeg as rc
    import rc_transmit_phase
    import rc_video_tx
    import video_recorder
    from command_state import CommandState

    out = {"schema": SCHEMA, "config_path": config_path}
    chatter = io.StringIO()

    def attempt(fn):
        try:
            with contextlib.redirect_stdout(chatter):
                return fn()
        except Exception as exc:        # a loader error IS a behaviour to compare
            return {"error": f"{type(exc).__name__}: {exc}"}

    out["resolved"] = attempt(lambda: rc.resolve_rc_settings(config_path))
    out["camera_controls_island"] = attempt(lambda: rc._load_camera_controls_island(config_path))
    out["bm_commands"] = attempt(lambda: command_daemon.load_bm_commands_config(config_path))
    cmds = out["bm_commands"]
    if isinstance(cmds, dict) and cmds.get("enabled") and "error" not in out["resolved"]:
        def overlay():
            state = CommandState(path=cmds["state_path"]) if cmds["state_path"] else CommandState()
            return rc._apply_command_overlay(rc.resolve_rc_settings(config_path), state)
        out["overlaid"] = attempt(overlay)
    out["video"] = attempt(lambda: video_recorder.load_video_config(config_path))
    out["video_tx"] = attempt(lambda: rc_video_tx.load_video_tx_config(config_path))
    out["media_key"] = attempt(lambda: rc_media_key.load_media_key_config(config_path))
    out["transmit_phase"] = attempt(
        lambda: rc_transmit_phase.load_transmit_phase_config(config_path))
    out["network"] = attempt(lambda: network_config.load_network_config(config_path))
    out["bm_serial"] = attempt(lambda: bms.load_bm_serial_config(config_path))
    out["network_type"] = attempt(lambda: bms.load_network_type_from_config(config_path).hex())
    out["uart"] = attempt(lambda: list(bms.load_uart_config(config_path)))
    out["env"] = {
        "BM_CAMERA_CONFIG_PATH": bms.BM_CAMERA_CONFIG_PATH,
        "bm_serial": attempt(lambda: bms.load_bm_serial_config()),
        "network_type": attempt(lambda: bms.load_network_type_from_config().hex()),
        "uart": attempt(lambda: list(bms.load_uart_config())),
    }
    out["wap_network"] = {
        "path": rc_command_hooks.CONFIG_PATH,
        "network": attempt(lambda: network_config.load_network_config(
            rc_command_hooks.CONFIG_PATH)),
    }
    out["loader_output"] = chatter.getvalue().splitlines()
    return out


def to_json_line(obj):
    """One line, stable key order: diffable with `diff <(tail -1 a) <(tail -1 b)`."""
    return json.dumps(obj, sort_keys=True, default=_jsonable, separators=(",", ":"))
