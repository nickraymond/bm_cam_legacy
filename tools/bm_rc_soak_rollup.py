#!/usr/bin/env python3
"""
bm_rc_soak_rollup.py — Sprint08 P8: pull + summarize the weekend RC soak.

Purpose
-------
Turn the soak's on-device artifacts (cron_logs/rc_cycle_*.log, camera_log.csv,
image sidecars) into the acceptance summary the sprint needs: cycles run,
quality/attempts distributions, incomplete cycles, halts, failures — and a
verdict on the four required behaviors (progressive JPEG sent · adaptive
quality · incomplete-cycle log · power halt).

Inputs
------
  default        pull from the Pi over SSH (--host, default pi@bmcam000) into
                 <out>/raw/, then parse
  --from-dir D   parse an already-pulled directory (offline; used by tests)

Outputs (timestamped, self-contained run folder)
------------------------------------------------
  <out>/run_manifest.json
  <out>/raw/...                    (pulled logs/sidecars; pull mode only)
  <out>/results/cycles.csv         (one row per RC cycle)
  <out>/results/soak_summary.json  (counts + acceptance verdict)
  stdout: per-cycle table + verdict

Example
-------
  python3 tools/bm_rc_soak_rollup.py
  python3 tools/bm_rc_soak_rollup.py --from-dir ~/Downloads/rc_soak_x/raw

Notes / limitations
-------------------
- A truncated log that ends at "[RC][halt] halt command returned 0" is a
  SUCCESSFUL halted cycle (shutdown can kill trailing writes); the parser
  treats halt_initiated as terminal success.
- Parses the wrapper/orchestrator line formats of this repo version only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_HOST = "pi@bmcam000"
REMOTE_LOG_GLOB = "/home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log"
REMOTE_CSV = "/home/pi/BM_Devel_Pi/camera_log.csv"
REMOTE_SIDECAR_GLOB = "/home/pi/BM_Devel_Pi/images/*_compressed.jpg.capture_metadata.json"

RE_ATTEMPT = re.compile(
    r"\[RC\] attempt q(\d+): (\d+) B, (\d+) msgs, over_cap=(\w+), budget_fit=(\w+)"
)
RE_SELECTION = re.compile(
    r"\[RC\] selection: quality=(\d+) attempts=(\d+) fits=(\w+) reason=(\w+)"
)
RE_TRANSMIT = re.compile(
    r"\[RC\] transmit done: sent=(\d+)/(\d+) complete=(\w+) "
    r"incomplete_emitted=(\w+) uart=([\d.]+)s"
)
RE_CYCLE_END = re.compile(r"\[RC\] cycle end: elapsed=([\d.]+)s of (\d+)s; halt=(\w+)")
RE_HALT_INITIATED = re.compile(r"halt command returned 0|halt_initiated")
RE_START_UTC = re.compile(r"\[RC-CRON\] start_utc=(\S+)")
RE_ERROR = re.compile(r"\[(?:RC|RC-CRON)\]\[ERROR\] (.*)")
RE_GATE = re.compile(r"\[RC\] schedule gate: (.*)")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_cycle_log(path: Path) -> Dict[str, object]:
    """Parse one rc_cycle_*.log into a flat cycle record."""
    text = path.read_text(errors="replace")
    row: Dict[str, object] = {
        "log_file": path.name,
        "start_utc": None,
        "attempts": 0,
        "attempt_qualities": "",
        "quality": None,
        "fits": None,
        "reason": None,
        "sent": None,
        "planned": None,
        "complete_send": None,
        "incomplete_emitted": False,
        "uart_s": None,
        "elapsed_s": None,
        "budget_s": None,
        "halt": None,
        "halt_initiated": bool(RE_HALT_INITIATED.search(text)),
        "errors": "",
        "gate": None,
        "status": "unknown",
    }

    m = RE_START_UTC.search(text)
    if m:
        row["start_utc"] = m.group(1)
    m = RE_GATE.search(text)
    if m:
        row["gate"] = m.group(1)[:60]

    attempts = RE_ATTEMPT.findall(text)
    row["attempts"] = len(attempts)
    row["attempt_qualities"] = " ".join(f"q{a[0]}:{a[2]}msgs" for a in attempts)

    m = RE_SELECTION.search(text)
    if m:
        row["quality"] = int(m.group(1))
        row["fits"] = m.group(3) == "True"
        row["reason"] = m.group(4)

    m = RE_TRANSMIT.search(text)
    if m:
        row["sent"] = int(m.group(1))
        row["planned"] = int(m.group(2))
        row["complete_send"] = m.group(3) == "True"
        row["incomplete_emitted"] = m.group(4) == "True"
        row["uart_s"] = float(m.group(5))

    m = RE_CYCLE_END.search(text)
    if m:
        row["elapsed_s"] = float(m.group(1))
        row["budget_s"] = int(m.group(2))
        row["halt"] = m.group(3)

    errors = RE_ERROR.findall(text)
    row["errors"] = " | ".join(e.strip()[:80] for e in errors)

    # Status classification (order matters).
    if errors:
        row["status"] = "error"
    elif row["incomplete_emitted"]:
        row["status"] = "incomplete_bounded"
    elif row["complete_send"]:
        row["status"] = "complete"
    elif row["sent"] is None and row["quality"] is None and row["halt_initiated"]:
        row["status"] = "halted_truncated_log"
    elif row["gate"] and "Outside" in str(row["gate"]):
        row["status"] = "window_skip"
    return row


def summarize(rows: List[Dict[str, object]]) -> Dict[str, object]:
    quality_hist: Dict[str, int] = {}
    attempts_hist: Dict[str, int] = {}
    for r in rows:
        if r["quality"] is not None:
            quality_hist[f"q{r['quality']}"] = quality_hist.get(f"q{r['quality']}", 0) + 1
        attempts_hist[str(r["attempts"])] = attempts_hist.get(str(r["attempts"]), 0) + 1

    complete = sum(1 for r in rows if r["status"] == "complete")
    incomplete = sum(1 for r in rows if r["status"] == "incomplete_bounded")
    errors = sum(1 for r in rows if r["status"] == "error")
    halts = sum(1 for r in rows if r["halt_initiated"])
    adaptive = sum(1 for r in rows if (r["attempts"] or 0) > 1)

    acceptance = {
        "progressive_jpeg_sent": complete > 0,
        "adaptive_quality_with_attempts_logged": adaptive > 0,
        "incomplete_cycle_logged": incomplete > 0,
        "power_halt_performed": halts > 0,
    }
    return {
        "cycles_total": len(rows),
        "complete_sends": complete,
        "incomplete_bounded_sends": incomplete,
        "error_cycles": errors,
        "halts_initiated": halts,
        "adaptive_cycles_gt1_attempt": adaptive,
        "quality_histogram": dict(sorted(quality_hist.items())),
        "attempts_histogram": dict(sorted(attempts_hist.items())),
        "acceptance": acceptance,
        "acceptance_pass": all(acceptance.values()),
    }


def pull_from_pi(host: str, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for label, pattern in [
        ("cycle logs", REMOTE_LOG_GLOB),
        ("camera_log.csv", REMOTE_CSV),
        ("sidecars", REMOTE_SIDECAR_GLOB),
    ]:
        print(f"[rollup] pulling {label} ...")
        result = subprocess.run(
            ["scp", f"{host}:{pattern}", str(raw_dir) + "/"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[rollup] WARNING: {label} pull failed: {result.stderr.strip()[:120]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sprint08 P8 soak rollup (see module docstring).")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--from-dir", type=Path, default=None,
                    help="Parse an existing directory instead of pulling from the Pi")
    ap.add_argument("--output", type=Path, default=None,
                    help="Run folder. Default: ~/Downloads/rc_soak_<UTC>")
    args = ap.parse_args()

    out_dir = (args.output or Path.home() / "Downloads" / f"rc_soak_{utc_stamp()}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    results_dir = out_dir / "results"
    results_dir.mkdir(exist_ok=True)

    if args.from_dir:
        raw_dir = args.from_dir.expanduser().resolve()
    else:
        raw_dir = out_dir / "raw"
        pull_from_pi(args.host, raw_dir)

    logs = sorted(raw_dir.glob("rc_cycle_*.log"))
    print(f"[rollup] parsing {len(logs)} cycle logs from {raw_dir}")
    rows = [parse_cycle_log(p) for p in logs]
    summary = summarize(rows)

    if rows:
        with (results_dir / "cycles.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    manifest = {
        "tool": "bm_rc_soak_rollup.py",
        "created_utc": utc_stamp(),
        "source": str(raw_dir),
        "host": None if args.from_dir else args.host,
        "logs_parsed": len(logs),
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (results_dir / "soak_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(f"\n{'log':<34} {'status':<22} {'q':>4} {'att':>4} {'sent':>9} {'elapsed':>9} {'halt':<16}")
    for r in rows:
        sent = f"{r['sent']}/{r['planned']}" if r["sent"] is not None else "-"
        elapsed = f"{r['elapsed_s']:.0f}s" if r["elapsed_s"] is not None else "-"
        print(f"{r['log_file']:<34} {r['status']:<22} {str(r['quality'] or '-'):>4} "
              f"{r['attempts']:>4} {sent:>9} {elapsed:>9} {str(r['halt'] or ('init' if r['halt_initiated'] else '-')):<16}")

    print(f"\n[rollup] summary: {json.dumps(summary, indent=2)}")
    print(f"[rollup] run folder: {out_dir}")
    print(f"[rollup] ACCEPTANCE: {'PASS' if summary['acceptance_pass'] else 'NOT YET'} "
          f"({sum(summary['acceptance'].values())}/4 behaviors shown)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
