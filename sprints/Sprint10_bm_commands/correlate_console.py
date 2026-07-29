#!/usr/bin/env python3
# filename: correlate_console.py
# description: Sprint10 Phase E — tie each measured backend gap to what the
#              Spotter console was doing at that exact moment.
"""
Sprint10 Phase E — correlate backend gaps with Spotter console events.

WHY THIS EXISTS
`analyze_queue_drain.py` proves messages are MISSING and when. It cannot
say WHY. The console is the only place the drop mechanism is visible: the
Pi logs `sent=N/N complete=True` for messages the Spotter silently threw
away (`[MS] [ERROR] Queue MS_Q_CELLULAR_ONLY is full` /
`[BM_TX] [ERROR] Unable to submit`). This script joins the two so a gap
can be labelled as a confirmed queue-full blackout rather than an
inference. That join is a Phase E deliverable (PHASE_E.md §4 step 3).

THE CLOCK PROBLEM (this is the whole trick)
Three clocks are involved and two of them disagree:
  - sendlog `t_offset_s`  : Pi MONOTONIC, immune to clock error. Trusted.
  - manifest `started_utc`: Pi WALL clock. On bmcam000 NTP is disabled by
                            design (RTC time-source patch), so its wall
                            clock ran -2773 s behind the bench Mac on
                            2026-07-29. See clock_offsets.txt.
  - console log timestamps: the MONITORING MAC's clock, stamped by
                            spotter_serial_monitor.py as lines arrive.
So: absolute_gap_time = started_utc + t_offset_s - clock_offset_s, where
clock_offset_s = (pi_epoch - mac_epoch). Pass --clock-offset-s for any
unit whose wall clock is not the Mac's. Getting this wrong silently
shifts every correlation by ~46 min and turns real hits into misses.

INPUTS
  --gaps-csv       gaps_<SPOT>.csv from analyze_queue_drain.py
  --manifest       manifest_<RUN>.json (for each burst's started_utc)
  --console-glob   ~/spotter_logs/<SPOT>/console_*.log
  --clock-offset-s pi_epoch - mac_epoch for the unit that SENT (default 0)
  --window-s       slack around the gap window (default 15 s), covers
                   console/Notecard timestamp jitter

OUTPUT
  gap_console_<SPOT>.csv  one row per gap + the console evidence found
  stdout summary: how many gaps are console-confirmed

EXAMPLE
  python3 correlate_console.py \
    --gaps-csv  runs/sprint10_phaseE_20260728/analysis/gaps_SPOT-31593C.csv \
    --manifest  runs/sprint10_phaseE_20260728/sendlogs/bmcam000/manifest_PAR0.json \
    --console-glob '~/spotter_logs/SPOT-31593C/console_*.log' \
    --clock-offset-s -2773 \
    --out-dir runs/sprint10_phaseE_20260728/analysis

LIMITATIONS
  Console-only visibility; a gap with no console hit is NOT proven to be a
  different mechanism (the console line may simply not have been emitted,
  e.g. loss further upstream at the Notecard or in the cell network).
  Absence of evidence is reported as `none`, never as "not a blackout".
"""

import argparse
import csv
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone

# Console signatures of the silent-drop mechanism (findings 006/007).
QUEUE_FULL = re.compile(r"Queue MS_Q\w*\s+is full|Unable to submit")
SYNC_MARK = re.compile(r"Attempting to Sync|Sync request|Waiting for TX")
FILL_MARK = re.compile(r"Notecard is ([0-9.]+) pct full")
# Monitor stamps every line: "2026-07-29T01:41:07Z <raw console line>"
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s")


def parse_utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_console(pattern):
    """[(datetime, line)] sorted by time, from the monitor's own stamps."""
    rows = []
    for path in sorted(glob.glob(os.path.expanduser(pattern))):
        with open(path, "r", errors="replace") as f:
            for line in f:
                m = TS_RE.match(line)
                if m:
                    rows.append((parse_utc(m.group(1)), line.rstrip()))
    rows.sort(key=lambda r: r[0])
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--gaps-csv", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--console-glob", required=True)
    ap.add_argument("--clock-offset-s", type=float, default=0.0,
                    help="pi_epoch - mac_epoch for the SENDING unit")
    ap.add_argument("--window-s", type=float, default=15.0)
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args(argv)

    with open(args.manifest) as f:
        manifest = json.load(f)
    starts = {b["burst_id"]: parse_utc(b["started_utc"])
              for b in manifest["bursts"]}
    console = load_console(args.console_glob)
    if not console:
        print(f"[WARN] no console lines matched {args.console_glob}")

    with open(args.gaps_csv) as f:
        gaps = list(csv.DictReader(f))
    if not gaps:
        print("[INFO] no gaps to correlate — nothing lost, nothing to explain")
        return 0

    out_rows, confirmed = [], 0
    spot = os.path.basename(args.gaps_csv).replace("gaps_", "").replace(".csv", "")
    for g in gaps:
        bid = g["burst_id"]
        if bid not in starts or not g.get("gap_start_s"):
            continue
        # Pi wall time of the gap, then shifted onto the Mac/console clock.
        t0 = (starts[bid] + timedelta(seconds=float(g["gap_start_s"]))
              - timedelta(seconds=args.clock_offset_s))
        span = float(g.get("gap_span_s") or 0)
        lo = t0 - timedelta(seconds=args.window_s)
        hi = t0 + timedelta(seconds=span + args.window_s)

        hits = [ln for ts, ln in console if lo <= ts <= hi]
        qfull = [ln for ln in hits if QUEUE_FULL.search(ln)]
        syncs = [ln for ln in hits if SYNC_MARK.search(ln)]
        fills = [m.group(1) for ln in hits
                 for m in [FILL_MARK.search(ln)] if m]
        if qfull:
            confirmed += 1
        out_rows.append({
            "burst_id": bid,
            "gap_start_seq": g["gap_start_seq"],
            "gap_len": g["gap_len"],
            "gap_start_s": g["gap_start_s"],
            "gap_span_s": g.get("gap_span_s"),
            "gap_utc_console_clock": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "queue_full_lines": len(qfull),
            "sync_lines": len(syncs),
            "notecard_fill_pct": fills[0] if fills else "",
            "verdict": ("queue-full CONFIRMED" if qfull
                        else "sync activity only" if syncs else "none"),
        })

    out = os.path.join(args.out_dir, f"gap_console_{spot}.csv")
    os.makedirs(args.out_dir, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)

    print(f"# {spot}: {len(out_rows)} gaps, {confirmed} console-confirmed "
          f"queue-full ({100.0 * confirmed / len(out_rows):.0f}%)")
    for r in out_rows:
        print(f"  {r['burst_id']:>16} seq {r['gap_start_seq']:>4} "
              f"len {r['gap_len']:>3}  {r['gap_utc_console_clock']}  "
              f"qfull={r['queue_full_lines']:<3} sync={r['sync_lines']:<3} "
              f"{r['verdict']}")
    print(f"# wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
