#!/usr/bin/env python3
# filename: rc_power_halt.py
# description: Sprint08 M6 — thin wrapper over the validated power-savings halt.
"""
Sprint08 M6 — power-savings halt wrapper (sprint spec section 2).

Wraps the already-validated tuned halt (tools/power/tuned_halt.sh — ~0.26 W
low idle, power-modes work PR #5; deployed copy lives in /home/pi/BM_Devel_Pi
per P6 decision). Invoked by the RC orchestrator (M7) at cycle end — on
success OR budget exhaustion.

P6 decisions (Nick-approved):
  - Invoke via `sudo -n /bin/bash <script>` — fails fast if passwordless
    sudo is unavailable instead of hanging a cron cycle on a prompt.
  - Halt failure NEVER raises: log loudly, return a failure result, let the
    cycle exit normally (worst case the box idles until the next power
    cycle — a power miss must not become a crashed cycle).
  - dry_run prints exactly what would run and executes nothing; this is the
    default for all bench/manual cycles until the weekend soak.

RECOVERY NOTE (from the halt script itself): after a real halt the ONLY way
back is a physical power cycle. SSH dropping ~15 s after invocation is
SUCCESS. Bluetooth soft-block persists across the power cycle (systemd-
rfkill); run `sudo rfkill unblock bluetooth` after recovery if BT is ever
needed (the camera runtime does not use it).
"""

import os
import subprocess

DEFAULT_HALT_SCRIPT = "/home/pi/BM_Devel_Pi/tuned_halt.sh"
HALT_MODES = {"halt", "poweroff"}

# The script counts down 10 s before systemctl; give it slack, then stop
# waiting — if the box is really going down our process dies anyway.
HALT_RUNNER_TIMEOUT_S = 60


def build_halt_command(script_path, mode):
    """Return the exact argv M6 runs (also what dry_run prints)."""
    mode = str(mode or "halt").strip().lower()
    if mode not in HALT_MODES:
        raise ValueError(f"power_halt.mode must be halt or poweroff, got {mode!r}")
    cmd = ["sudo", "-n", "/bin/bash", str(script_path)]
    if mode == "poweroff":
        cmd.append("--poweroff")
    return cmd


def perform_power_halt(
    *,
    enabled,
    dry_run,
    mode="halt",
    script_path=DEFAULT_HALT_SCRIPT,
    runner=subprocess.run,
    log=print,
):
    """Execute (or log) the cycle-end power halt. Never raises.

    Returns {"action": "disabled" | "dry_run" | "halt_initiated" | "failed",
             "command": [...], "script_exists": bool, "detail": str}.
    """
    command = build_halt_command(script_path, mode)
    script_exists = os.path.exists(script_path)
    result = {
        "action": None,
        "command": command,
        "script_exists": script_exists,
        "detail": "",
    }

    if not enabled:
        result["action"] = "disabled"
        result["detail"] = "power_halt.enabled=false; skipping halt"
        log(f"[RC][halt] {result['detail']}")
        return result

    if dry_run:
        result["action"] = "dry_run"
        result["detail"] = "dry_run: would execute halt command"
        log(f"[RC][halt] DRY RUN — would execute: {' '.join(command)}")
        if not script_exists:
            log(f"[RC][halt] WARNING: halt script missing at {script_path} — a real run would fail")
        else:
            log("[RC][halt] DRY RUN — script present; real run would drop SSH within ~15 s (that is success)")
        return result

    if not script_exists:
        result["action"] = "failed"
        result["detail"] = f"halt script missing: {script_path}"
        log(f"[RC][halt] ERROR: {result['detail']} — box will idle until next power cycle")
        return result

    log(f"[RC][halt] executing: {' '.join(command)} (SSH/process death after this is SUCCESS)")
    try:
        proc = runner(
            command,
            capture_output=True,
            text=True,
            timeout=HALT_RUNNER_TIMEOUT_S,
            check=False,
        )
    except Exception as exc:  # timeout/kill during shutdown included — never raise
        result["action"] = "failed"
        result["detail"] = f"halt runner exception: {exc}"
        log(f"[RC][halt] ERROR: {result['detail']} — box will idle until next power cycle")
        return result

    if proc.returncode == 0:
        result["action"] = "halt_initiated"
        result["detail"] = "halt command returned 0; system shutdown in progress"
        log(f"[RC][halt] {result['detail']}")
    else:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        result["action"] = "failed"
        result["detail"] = f"halt command exit={proc.returncode}: {stderr_tail[0]}"
        log(f"[RC][halt] ERROR: {result['detail']} — box will idle until next power cycle")

    return result
