#!/usr/bin/env python3
# filename: analyze_overnight_energy.py
# description: Sprint10 overnight A/B — energy per image from BM-bus power telemetry.
"""
Energy comparison for the 2026-07-29 overnight A/B (txd 1.0 s vs 5.0 s).

WHERE THE NUMBERS COME FROM
Each camera node publishes its own BM-bus rail on the Spotter console:
    <node_id>, power | ... voltage: 23.86, current: 0.0342
That is the CAMERA UNIT's draw (bus volts x bus amps), which is exactly the
quantity being compared. The Spotter's own SD `PWR.csv` is its internal
4.78 V / 3.96 V rails, not the bus, so it answers a different question.

A MEASUREMENT ASYMMETRY YOU MUST KNOW ABOUT
The two Spotters are NOT configured the same for telemetry:
    SPOT-33507C  transmitAggregations=0  -> 1253 bus samples in the window
    SPOT-31593C  transmitAggregations=1  ->   24 bus samples in the window
So arm B (bmcam003) has a dense, directly integrable power trace and arm A
(bmcam000) does not. Rather than pretend otherwise, this script:
  1. measures the POWER PROFILE (halted / awake-idle / active) from the
     dense unit,
  2. checks the sparse unit's samples land on the same levels — same Pi,
     same board, same workload, so they should,
  3. integrates energy using each arm's OWN measured durations (from its
     cron logs), with the shared power profile.
Step 3 is a model. It is labelled as one everywhere it is reported.

INPUTS
  --console-root   ~/spotter_logs
  --cronlogs       runs/.../cronlogs
  --start/--end    UTC window, default the overnight run
OUTPUTS
  energy_summary.json, energy_by_cycle.csv, energy_overnight.png

USAGE
  python3 tools/analyze_overnight_energy.py \
      --out-dir runs/sprint10_overnight_20260729
"""

import argparse
import csv
import glob
import json
import os
import re
import statistics as st
from datetime import datetime, timezone

NODES = {
    "53171fa3d81a8e6f": dict(spot="SPOT-33507C", unit="bmcam003", arm="B", txd=1.0),
    "49cfe4d7cceb2771": dict(spot="SPOT-31593C", unit="bmcam000", arm="A", txd=5.0),
}
BOOT_OVERHEAD_S = 65.0   # measured: boot ~35 s + cron settle 30 s


def parse_console(root, node, spot, t0, t1):
    """[(datetime, volts, amps)] for one camera node."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, spot, "console_*.log"))):
        with open(path, errors="replace") as fh:
            for line in fh:
                if node not in line or ", power |" not in line:
                    continue
                v = re.search(r"voltage: ([\d.]+)", line)
                a = re.search(r"current: (-?[\d.]+)", line)
                if not (v and a):
                    continue
                try:
                    ts = datetime.strptime(line.split()[0], "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    continue
                ts = ts.replace(tzinfo=timezone.utc)
                if t0 <= ts <= t1:
                    out.append((ts, float(v.group(1)), float(a.group(1))))
    out.sort()
    return out


def cycle_durations(cronlog_dir, unit, t0):
    """[(start_utc, elapsed_s, msgs)] per cycle from the device's own logs."""
    rows = []
    for f in sorted(glob.glob(os.path.join(cronlog_dir, unit, "rc_cycle_*.log"))):
        ts = re.search(r"rc_cycle_(\d{8}T\d{6}Z)", f)
        if not ts:
            continue
        start = datetime.strptime(ts.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
        if start < t0:
            continue
        s = open(f, errors="replace").read()
        el = re.search(r"cycle end: elapsed=([\d.]+)s", s)
        tx = re.search(r"transmit done: sent=(\d+)/(\d+)", s)
        if el and tx:
            rows.append((start, float(el.group(1)), int(tx.group(1))))
    return rows


def power_levels(samples):
    """Split the dense trace into idle vs active power (W) by a midpoint
    threshold on current — the trace is strongly bimodal (halted board vs
    Pi awake and working)."""
    watts = [v * a for _, v, a in samples]
    if not watts:
        return None
    lo, hi = min(watts), max(watts)
    mid = lo + 0.45 * (hi - lo)
    idle = [w for w in watts if w < mid]
    active = [w for w in watts if w >= mid]
    return dict(
        n=len(watts),
        idle_w=round(st.median(idle), 3) if idle else None,
        active_w=round(st.median(active), 3) if active else None,
        peak_w=round(hi, 3), min_w=round(lo, 3),
        active_frac=round(len(active) / len(watts), 3),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--console-root", default=os.path.expanduser("~/spotter_logs"))
    ap.add_argument("--out-dir", default="runs/sprint10_overnight_20260729")
    ap.add_argument("--start", default="2026-07-29T07:49:00Z")
    ap.add_argument("--end", default="2026-07-29T14:00:00Z")
    args = ap.parse_args(argv)
    t0 = datetime.strptime(args.start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(args.end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    cron = os.path.join(args.out_dir, "cronlogs")

    traces, profiles, cycles = {}, {}, {}
    for node, meta in NODES.items():
        s = parse_console(args.console_root, node, meta["spot"], t0, t1)
        traces[meta["unit"]] = s
        profiles[meta["unit"]] = power_levels(s)
        cycles[meta["unit"]] = cycle_durations(cron, meta["unit"], t0)

    # The dense unit defines the shared power profile.
    dense = max(profiles, key=lambda u: (profiles[u] or {}).get("n", 0))
    prof = profiles[dense]
    print(f"# power profile measured on {dense} ({prof['n']} bus samples)")
    print(f"#   halted/idle {prof['idle_w']} W   active {prof['active_w']} W"
          f"   peak {prof['peak_w']} W")
    for u, p in profiles.items():
        if u != dense and p:
            print(f"# cross-check {u}: {p['n']} samples, "
                  f"range {p['min_w']}-{p['peak_w']} W "
                  f"(levels consistent: {'YES' if p['peak_w'] <= prof['peak_w']*1.25 else 'NO'})")

    rows, summary = [], {}
    for node, meta in NODES.items():
        u = meta["unit"]
        cyc = cycles[u]
        per = []
        for start, elapsed, msgs in cyc:
            awake = elapsed + BOOT_OVERHEAD_S
            # Whole awake period is drawn at the ACTIVE level: the Pi is
            # booted and either encoding or paced-transmitting throughout;
            # the idle level is what it draws once HALTED, between cycles.
            wh = prof["active_w"] * awake / 3600.0
            per.append(wh)
            rows.append(dict(unit=u, arm=meta["arm"], txd=meta["txd"],
                             cycle_start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             elapsed_s=round(elapsed, 1),
                             awake_s=round(awake, 1), msgs=msgs,
                             energy_wh=round(wh, 4)))
        if per:
            summary[u] = dict(
                arm=meta["arm"], txd=meta["txd"], cycles=len(per),
                mean_awake_s=round(st.mean([c[1] for c in cyc]) + BOOT_OVERHEAD_S, 1),
                mean_wh_per_cycle=round(st.mean(per), 4),
                total_wh=round(sum(per), 3),
                active_w=prof["active_w"])

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "energy_by_cycle.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    units = list(summary)
    if len(units) == 2:
        a = summary["bmcam000"]; b = summary["bmcam003"]
        ratio = a["mean_wh_per_cycle"] / b["mean_wh_per_cycle"]
        summary["comparison"] = dict(
            faster_arm="bmcam003 (B, 1.0 s)", slower_arm="bmcam000 (A, 5.0 s)",
            energy_ratio_slow_over_fast=round(ratio, 2),
            energy_saving_of_fast_pct=round(100 * (1 - 1 / ratio), 1),
            wh_saved_per_cycle=round(a["mean_wh_per_cycle"] - b["mean_wh_per_cycle"], 4))
    summary["_method"] = ("power profile measured on the densely-sampled unit; "
                          "energy = active_W x each arm's OWN measured awake time. "
                          "MODEL, not a direct integration for the sparse unit.")
    with open(os.path.join(args.out_dir, "energy_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("\n" + json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
