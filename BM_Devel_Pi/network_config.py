#!/usr/bin/env python3
# filename: network_config.py
# description: Sprint16 network island (D-S16-3/4) — parse the network:
#              YAML block and apply the boot default via network_ap.sh.
"""
Sprint16 network configuration.

The `network:` island in camera_schedule.yaml holds the BOOT DEFAULT —
the one persistent piece of Sprint16 state (everything the wap command
or the GUI join form does at runtime is session-only, D-S16-2):

    network:
      default: nereus_hq       # ap | nereus_hq  (ship value: ap)
      ap_fallback_s: 90        # raise the open AP if the default client
                               # network is not joined this many seconds
                               # after boot (ignored when default is ap)
      ap_timeout_min: 60       # auto-revert timer for REMOTE wap flips

Contract (extension, not regression): a YAML with NO network island
resolves to None and apply_boot_default() is a NO-OP — units that
predate Sprint16 behave exactly as before. A PRESENT island with
nonsense values fails loudly at config time.

Assumption (labeled): network_ap.sh owns all nmcli/systemd mechanics;
this module only decides WHAT to ask for and never blocks the capture
path (fire-and-forget Popen, same pattern as the wap hook).
"""

import os
import subprocess

NETWORK_SCRIPT = "/home/pi/BM_Devel_Pi/network_ap.sh"

VALID_DEFAULTS = ("ap", "nereus_hq")

_DEFAULTS = {
    "ap_fallback_s": 90,
    "ap_timeout_min": 60,
}


def _parse_int(name, value, lo, hi):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"network.{name} must be an integer, got {value!r}")
    if not lo <= n <= hi:
        raise ValueError(
            f"network.{name}={n} outside sane range [{lo}, {hi}]")
    return n


def _strip_yaml_value(value):
    return value.split("#", 1)[0].strip().strip('"').strip("'")


def load_network_config(config_path):
    """Parse the network island. Returns a dict, or None when the YAML
    has no `network:` block (pre-Sprint16 config — boot apply no-ops).

    Same tiny hand-parser style as load_video_config (no PyYAML
    dependency on units). Loud on nonsense: a PRESENT island that is
    invalid — including one that names no default — raises."""
    if not config_path or not os.path.exists(config_path):
        return None

    saw_island = False
    island = {}
    in_network = False
    with open(config_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip("\n").rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            stripped = line.strip()

            if stripped.endswith(":") and ":" not in stripped[:-1]:
                name = stripped[:-1].strip()
                if indent == 0:
                    in_network = (name == "network")
                    saw_island = saw_island or in_network
                continue
            if ":" not in stripped:
                continue
            if indent == 0:
                # `network: <scalar>` is a malformed island, not absence.
                key = stripped.split(":", 1)[0].strip()
                if key == "network":
                    raise ValueError("network: island must be a mapping")
                in_network = False
                continue
            if not in_network:
                continue
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = _strip_yaml_value(value)
            if key in ("default", "ap_fallback_s", "ap_timeout_min"):
                island[key] = value
            else:
                print(f"[NET][WARN] unknown key network.{key} ignored")

    if not saw_island:
        return None

    default = str(island.get("default", "")).strip()
    if default not in VALID_DEFAULTS:
        raise ValueError(
            f"network.default must be one of {VALID_DEFAULTS}, "
            f"got {default!r}")

    cfg = {"default": default}
    cfg["ap_fallback_s"] = _parse_int(
        "ap_fallback_s", island.get("ap_fallback_s",
                                    _DEFAULTS["ap_fallback_s"]), 10, 600)
    cfg["ap_timeout_min"] = _parse_int(
        "ap_timeout_min", island.get("ap_timeout_min",
                                     _DEFAULTS["ap_timeout_min"]), 5, 1440)
    return cfg


def print_network_settings(cfg):
    if cfg is None:
        print("[NET] no network island: WiFi left to the OS (pre-Sprint16 behavior)")
        return
    print(f"[NET] network island: default={cfg['default']} "
          f"ap_fallback_s={cfg['ap_fallback_s']} "
          f"ap_timeout_min={cfg['ap_timeout_min']}")


def apply_boot_default(cfg, *, script_path=NETWORK_SCRIPT,
                       popen_fn=subprocess.Popen):
    """Fire-and-forget `network_ap.sh default <mode> <fallback_s>`.

    Never blocks or raises into the capture path: a WiFi problem must
    not cost a clip/photo cycle (script output lands in the cron log
    via inherited stdout). Returns the argv used (None if no-op)."""
    if cfg is None:
        return None
    argv = ["sudo", "-n", script_path, "default",
            cfg["default"], str(cfg["ap_fallback_s"])]
    print(f"[NET] applying boot default: {' '.join(argv)}")
    try:
        popen_fn(argv)
    except Exception as exc:  # loud, non-fatal (D-S16-3)
        print(f"[NET][WARN] boot default dispatch failed: {exc}")
    return argv
