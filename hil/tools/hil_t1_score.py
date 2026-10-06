#!/usr/bin/env python3
"""hil_t1_score.py — score T1 bursts (hil/gates/T1_burst_queue_bmcam003.md) from the Spotter console.

Per burst (window = first send − 2 s → last send + 30 s, from the Pi's sends.csv):
  accepted = `Submitted spotter/transmit-data message to cell-only queue` lines (one per accepted message; the
             Spotter logs it only for messages its cellular queue took), rejected = `Queue MS_Q_CELLULAR_ONLY is full`.
  accepted + rejected must equal the burst's sent count (checked: `sum_ok`). Also: Notecard % first/max/last,
  and the Spotter's own sync / health-check lines inside the window (with their times).
Inputs:  --plan t1_plan.csv, --sends sends.csv (pulled from the Pi), --console <nereus000 console excerpt, lines
         "<host UTC> <spotter text>">, --out CSV
Outputs: per-burst CSV (run, arm, shape, start, end, sent, accepted, rejected, sum_ok, nc_first, nc_max, nc_last,
         syncs, health) + an arm summary (median / min / max rejected) on stdout
Example: python3 hil/tools/hil_t1_score.py --plan runs/t1_burst_20261006/t1_plan.csv \
           --sends runs/t1_burst_20261006/pulled/sends.csv --console runs/t1_burst_20261006/console/console_1600_1900.txt \
           --out runs/t1_burst_20261006/analysis/t1_bursts.csv
Limits:  assumes only the T1 sender uses the cellular queue during the test (capture cycle stopped); window
         times are the console host's receive times (nereus000, NTP) vs the Pi's send times (NTP).
"""
import argparse
import csv
import datetime as dt
import re
import statistics
import sys


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--sends", required=True)
    ap.add_argument("--console", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sends = {}
    for r in csv.DictReader(open(a.sends)):
        sends.setdefault(r["run"], []).append(r)
    lines = []
    for ln in open(a.console, errors="replace"):
        p = ln.split(" ", 1)
        if len(p) == 2 and p[0].endswith("Z"):
            try:
                lines.append((ts(p[0]), p[1].rstrip("\n")))
            except ValueError:
                pass
    rows = []
    for run, ss in sends.items():
        t0 = ts(ss[0]["utc"]) - dt.timedelta(seconds=2)
        t1 = ts(ss[-1]["utc"]) + dt.timedelta(seconds=30)
        w = [(t, x) for t, x in lines if t0 <= t <= t1]
        acc = sum("Submitted spotter/transmit-data message to cell-only queue" in x for _, x in w)
        rej = sum("MS_Q_CELLULAR_ONLY is full" in x for _, x in w)
        nc = [float(m.group(1)) for _, x in w for m in [re.search(r"Notecard is ([0-9.]+) pct full", x)] if m]
        syncs = [t.strftime("%H:%M:%S") for t, x in w if "Attempting to Sync" in x]
        health = [t.strftime("%H:%M:%S") for t, x in w if "Running health check" in x]
        sent = sum(r["rc"] == "ok" for r in ss)
        rows.append(dict(run=run, arm=ss[0]["arm"], shape=ss[0]["shape"], start=ss[0]["utc"], end=ss[-1]["utc"],
                         sent=sent, accepted=acc, rejected=rej, sum_ok=(acc + rej == sent),
                         nc_first=nc[0] if nc else "", nc_max=max(nc) if nc else "", nc_last=nc[-1] if nc else "",
                         syncs=" ".join(syncs), health=" ".join(health)))
    rows.sort(key=lambda r: r["start"])
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['start'][11:19]} {r['arm']:<13} {r['shape']:<6} sent={r['sent']} acc={r['accepted']} "
              f"rej={r['rejected']} sum_ok={r['sum_ok']} nc={r['nc_first']}->{r['nc_max']}->{r['nc_last']} "
              f"sync=[{r['syncs']}] health=[{r['health']}]")
    for arm in dict.fromkeys(r["arm"] for r in rows):
        rej = [r["rejected"] for r in rows if r["arm"] == arm]
        print(f"[arm] {arm}: n={len(rej)} rejected median={statistics.median(rej)} min={min(rej)} max={max(rej)}")
    return 0 if all(r["sum_ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
