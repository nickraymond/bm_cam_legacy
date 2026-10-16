#!/usr/bin/env python3
"""wake_energy.py: joules per camera wake from the bridge `power |` console samples (gate card G7).

Purpose
    Pre-registered energy extraction for GATE_CARD_offwin_heals.md G7 (off-window heal-wake energy vs an
    in-window image wake). Written and checked on old console logs BEFORE any gate data exists.

Inputs
    One or more Spotter console capture files (nereus000 spotter-monitor / hil_console output). Lines like:
      ... 0e582dd12c1e1480, power | tick: 850273, rtc: 2026-10-06T21:25:03.847, addr: 65, voltage: 23.8992, current: 0.03375
    --node <bridge id>: the camera's bridge (SPOT-31593C = 0e582dd12c1e1480, SPOT-33507C = c3c564b91856226c).
    The other node in the same file (e.g. e6fe83ea6b4a2b7f, ~0 A) is not the camera port.

Outputs
    CSV on stdout (or --csv FILE), one row per wake:
      file, node, t_on (UTC), t_halt (UTC), run_s, i_idle_a, e_run_j, e_gross_run_j, e_window_j, window_s, n_samples, halt_found
    e_run_j       = sum V*(I - I_idle)*dt from bus-on to halt   (energy the running Pi adds above its halted floor)
    e_gross_run_j = sum V*I*dt from bus-on to halt              (everything the camera port drew while running)
    e_window_j    = sum V*I*dt over the whole bus-on run        (scheduled wakes only: incl. the halted tail to the :10 cut;
                                                                 on a held bus this just runs to the next power cycle)

Method (fixed before the test)
    - Samples are ~10 s apart; each sample holds until the next (dt from the bridge tick, capped at 15 s).
    - A wake starts at the first sample with V > 5 V after a bus-off/gap (> 30 s) or a bridge tick reset (held bus:
      `bridge cfg commit` reboots the bridge), or else at the first sample > I_HALT_MAX after a halted plateau.
    - Halt = the first sample from which I <= I_HALT_MAX holds for >= 6 samples (60 s) or until the bus drops.
      I_HALT_MAX = 0.025 A: on the 10/3-10/12 logs the running Pi sits at >= 0.033 A and the halted Pi at ~0.018 A.
    - I_idle = median I over the halted plateau (up to 60 s after the halt; scheduled: until the cut).

Assumptions / limitations
    - Bus-on is only known to the sample (<= 10 s late): up to ~10 s of boot current is missed, the same for every
      wake, so ratios are unaffected to first order.
    - The bridge port measures the camera Pi (+ its converter) only. Spotter cellular energy for the messages is NOT
      in these samples.
    - A wake whose halt is never seen (bus cut while running) is reported with halt_found=0 and excluded from G7.

Example
    python3 runs/soak_noaa_20261017/wake_energy.py --node 0e582dd12c1e1480 runs/weekend_20261010/console/wake_SPOT-31593C_*.txt
"""
import argparse
import csv
import re
import statistics
import sys
from datetime import datetime, timezone

# Host capture stamp (nereus000 clock, 1 s) ... node, power | tick: <bridge ms since boot>, rtc: <may be 0>, ... V, I
PWR = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) .*?([0-9a-f]{16}), power \| tick: (\d+), rtc: [^,]*, "
                 r"addr: \d+, voltage: ([0-9.\-]+), current: ([0-9.\-]+)")
V_ON = 5.0          # bus considered on above this
I_HALT_MAX = 0.025  # A; running >= 0.033, halted ~0.018 on the bench logs
HALT_RUN = 6        # samples (60 s) of halted current to call a halt
GAP_S = 30.0        # a sample gap longer than this ends a bus-on run
DT_CAP = 15.0       # s; one sample never counts for longer than this


def parse(path, node):
    """Return (t, V, I, tick) samples for one node, in file order, de-duplicated.

    t = the host capture stamp (always present; the Spotter epoch and the bridge rtc are 0 / uptime-only after a
    Spotter restart with no GPS). dt between samples comes from the bridge tick (ms) when it increases, which is
    exact; a tick reset = the bridge rebooted (`bridge cfg commit` = the bus power cycle of a held-bus boot).
    """
    out, seen = [], set()
    with open(path, errors="replace") as fh:
        for line in fh:
            m = PWR.search(line)
            if not m or m.group(2) != node:
                continue
            key = (m.group(1), m.group(3))
            if key in seen:
                continue
            seen.add(key)
            t = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            out.append((t, float(m.group(4)), float(m.group(5)), int(m.group(3))))
    return out


def step(a, b):
    """Seconds from sample a to sample b: bridge tick delta when the tick ran on, else the host-stamp delta."""
    if b[3] > a[3] and 0 < (b[3] - a[3]) / 1000.0 < 60:
        return (b[3] - a[3]) / 1000.0
    return b[0] - a[0]


def bus_runs(samples):
    """Split samples into bus-on runs (V > V_ON, no gap > GAP_S, no bridge tick reset)."""
    runs, cur = [], []
    for s in samples:
        on = s[1] > V_ON
        if on and cur and (step(cur[-1], s) > GAP_S or s[3] < cur[-1][3]):
            runs.append(cur)
            cur = []
        if on:
            cur.append(s)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def split_wakes(run):
    """A held bus can carry several boots: split at a rise above I_HALT_MAX after a halted plateau."""
    wakes, start, halted_n = [], 0, 0
    for i, (_, _, cur, _) in enumerate(run):
        if cur <= I_HALT_MAX:
            halted_n += 1
        else:
            if halted_n >= HALT_RUN and i > start:
                wakes.append(run[start:i])
                start = i
            halted_n = 0
    wakes.append(run[start:])
    return wakes


def energy(wake):
    """Return the per-wake numbers (see module docstring)."""
    t = [s[0] for s in wake]
    dt = [min(step(wake[i], wake[i + 1]), DT_CAP) for i in range(len(wake) - 1)] + [10.0]
    halt = None
    for i in range(len(wake)):
        tail = wake[i:i + HALT_RUN]
        if tail and all(s[2] <= I_HALT_MAX for s in tail) and (len(tail) == HALT_RUN or i + len(tail) == len(wake)):
            if any(s[2] > I_HALT_MAX for s in wake[:i]):  # must have run first
                halt = i
                break
    if halt is None:
        i_idle = 0.0
        h = len(wake)
    else:
        plateau = [s[2] for s in wake[halt:halt + HALT_RUN]]
        i_idle = statistics.median(plateau)
        h = halt
    e_run = sum(wake[i][1] * (wake[i][2] - i_idle) * dt[i] for i in range(h))
    e_gross = sum(wake[i][1] * wake[i][2] * dt[i] for i in range(h))
    e_win = sum(wake[i][1] * wake[i][2] * dt[i] for i in range(len(wake)))
    iso = lambda x: datetime.fromtimestamp(x, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "t_on": iso(t[0]),
        "t_halt": iso(t[h]) if halt is not None else "",
        "run_s": round(sum(dt[:h]), 1),
        "i_idle_a": round(i_idle, 5),
        "e_run_j": round(e_run, 1),
        "e_gross_run_j": round(e_gross, 1),
        "e_window_j": round(e_win, 1),
        "window_s": round(sum(dt), 1),
        "n_samples": len(wake),
        "halt_found": int(halt is not None),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--node", required=True, help="camera bridge node id (16 hex)")
    ap.add_argument("--csv", help="write CSV here instead of stdout")
    ap.add_argument("--min-samples", type=int, default=6, help="ignore bus-on blips shorter than this")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    fields = ["file", "node", "t_on", "t_halt", "run_s", "i_idle_a", "e_run_j", "e_gross_run_j",
              "e_window_j", "window_s", "n_samples", "halt_found"]
    fh = open(a.csv, "w", newline="") if a.csv else sys.stdout
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    seen, n = set(), 0
    for path in a.files:
        for run in bus_runs(parse(path, a.node)):
            for wake in split_wakes(run):
                if len(wake) < a.min_samples:
                    continue
                row = energy(wake)
                if row["t_on"] in seen:  # consecutive capture files can overlap
                    continue
                seen.add(row["t_on"])
                w.writerow({"file": path.split("/")[-1], "node": a.node, **row})
                n += 1
    print(f"[wake_energy] {n} wake(s) from {len(a.files)} file(s), node {a.node}", file=sys.stderr)
    if n == 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
