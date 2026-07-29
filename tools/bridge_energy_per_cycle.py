#!/usr/bin/env python3
# filename: bridge_energy_per_cycle.py
# description: Sprint11 metric 2 — Wh per cycle from the BRIDGE node's addr-65 power trace.
"""
Sprint11 metric 2: measured energy per cycle, per device.

WHICH SENSOR, AND WHY (DESIGN D9)
Integrate the BRIDGE node's addr-65 trace, not the camera node's. The camera
node's own power log is unusable on SPOT-31593C -- the two Spotters differ in
`transmitAggregations`, so one logs densely and the other does not. Measured
on this bench, 2026-07-29, over one day of console:

    bridge  c3c564b91856226c (SPOT-33507C)   6124 samples
    bridge  0e582dd12c1e1480 (SPOT-31593C)   6122 samples
    camera  53171fa3d81a8e6f (SPOT-33507C)   4724 samples
    camera  49cfe4d7cceb2771 (SPOT-31593C)    120 samples   <-- unusable

The bridge trace reads the downstream camera load, is dense on BOTH units,
and drops to 0.000 W in every bus-off window. Where both sensors exist it
agrees with the camera sensor to 4.8 % (bridge 2.156 Wh vs camera 2.259 Wh).

SD CARD vs CONSOLE
D9's authoritative source is the Spotter SD card
(`bm/<bridge-node>/*_power.log`), analysed with the `nereus-spotter-sd-analysis`
skill. This tool reads the SAME quantity from the USB console capture, which
is available live and needs no card pull. Use it during a run and to
cross-check; treat the SD numbers as authoritative when they arrive, because
console capture can have host-side gaps (the 2026-07-29 Mac-sleep incident
cost 45 min). The tool reports coverage so a gap cannot pass unnoticed.

WHAT "PER CYCLE" MEANS (DESIGN D7)
A cycle is one bus-ON window: power applied -> boot -> capture -> transmit ->
the Pi halts itself -> the bus stays powered until the window closes. The
halted-Pi baseline is charged to the cycle deliberately, because that is what
the customer's battery actually pays. This is why the bus window is the
biggest energy lever: the Pi is already halted through most of it.

Windows are found from the trace itself -- contiguous runs of non-zero power
separated by >= --gap-s of zero/absent samples -- not from an assumed
schedule, so a mis-set Spotter config shows up as an odd window length
rather than being silently averaged in.

Inputs:  --console-root (default ~/spotter_logs), --start/--end UTC ISO
Outputs: JSON summary + per-window CSV. Exit nonzero only on bad input.

Example:
  python3 tools/bridge_energy_per_cycle.py \\
      --start 2026-07-29T20:00:00Z --end 2026-07-30T02:00:00Z \\
      --out-dir runs/sprint11_20260729

VALIDATED AGAINST THE PUBLISHED SPRINT10 NUMBERS
Run over the 2026-07-29 overnight A/B window (07:49-14:00Z) this tool, from
console capture alone, reproduces the SD-derived figures in
runs/sprint10_overnight_20260729/RESULTS.md:

    bmcam003   0.1737 Wh/cycle  (published 0.1797)   -3.3 %
    bmcam000   0.2323 Wh/cycle  (published 0.2256)   +3.0 %

Independent source, same answer to ~3 %. It also reproduces the D7 claim
directly: the halted-Pi floor reads 0.424 W on the trace, and
0.424 W x 1200 s = 0.1413 Wh = 79 % of bmcam003's cycle energy.

Known limitations: console sampling is ~10 s, so a window boundary is
located to +/- one sample; energy uses trapezoidal integration over real
sample timestamps. Sub-second transients are invisible at this rate -- fine
for a Wh-per-cycle comparison, useless for inrush analysis.
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

# spot -> (bridge node id, camera node id, unit, role)
UNITS = {
    "SPOT-33507C": ("c3c564b91856226c", "53171fa3d81a8e6f", "bmcam003",
                    "Unit A candidate"),
    "SPOT-31593C": ("0e582dd12c1e1480", "49cfe4d7cceb2771", "bmcam000",
                    "Unit B control"),
}

# 2026-07-29T18:18:07Z 1785349088.515 c3c5... , power | tick: ..., rtc: ...,
#   addr: 65, voltage: 23.896000, current: 0.018000
RE_POWER = re.compile(
    r"^(?P<host_ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+"
    r"(?P<epoch>\d+\.\d+)\s+(?P<node>[0-9a-f]{16}),\s*power\s*\|.*?"
    r"addr:\s*(?P<addr>\d+),\s*voltage:\s*(?P<v>-?[\d.]+),\s*"
    r"current:\s*(?P<i>-?[\d.]+)")


def parse_iso(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def read_samples(console_root, spot, node, addr, start, end):
    """[(epoch_seconds, watts)] for one node's addr, sorted, in-window."""
    paths = sorted(glob.glob(os.path.join(console_root, spot, "console_*.log")))
    out = []
    for path in paths:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                if node not in line or ", power" not in line:
                    continue
                m = RE_POWER.match(line)
                if not m or m.group("node") != node:
                    continue
                if int(m.group("addr")) != addr:
                    continue
                ts = parse_iso(m.group("host_ts"))
                if not (start <= ts <= end):
                    continue
                watts = float(m.group("v")) * float(m.group("i"))
                out.append((ts.timestamp(), watts))
    out.sort()
    return out


def split_windows(samples, gap_s, on_threshold_w):
    """Contiguous bus-ON windows: runs of >threshold power, split on a gap.

    WHAT ENDS A WINDOW. On this hardware the bridge keeps publishing through
    the bus-off period at ~0.000 A (measured: 113 near-zero samples per hour
    against 169 at the 0.424 W halted-Pi baseline), so the POWER THRESHOLD is
    what delimits a cycle -- not the absence of samples.

    So gap_s must be generous. A missing run of console samples is a HOST-side
    dropout, not a bus event: the 2026-07-29 Mac-sleep incident cost 45 min of
    capture, and at gap_s=60 that fragmented real 1200 s windows into ~480 s
    pieces and under-reported energy by 4x. Trapezoidal integration across a
    dropout interpolates slowly-varying power, which is the right thing; the
    only real risk is a dropout long enough to swallow an entire bus-OFF
    period and fuse two cycles, and that shows up as a double-length window
    in `duration_s`, which is reported per window for exactly this reason.
    """
    windows, current, last_t = [], [], None
    for t, w in samples:
        on = w > on_threshold_w
        if last_t is not None and (t - last_t) > gap_s:
            if current:
                windows.append(current)
            current = []
        if on:
            current.append((t, w))
        elif current:
            windows.append(current)
            current = []
        last_t = t
    if current:
        windows.append(current)
    return [w for w in windows if len(w) >= 2]


def integrate(window):
    """Trapezoidal Wh over a window's real sample timestamps."""
    joules = 0.0
    for (t0, w0), (t1, w1) in zip(window, window[1:]):
        joules += 0.5 * (w0 + w1) * (t1 - t0)
    return joules / 3600.0


def analyze_unit(console_root, spot, start, end, gap_s, on_threshold_w,
                 min_window_s):
    bridge, camera, unit, role = UNITS[spot]
    samples = read_samples(console_root, spot, bridge, 65, start, end)
    if not samples:
        return {"spotter_id": spot, "unit": unit, "role": role,
                "error": "no bridge addr-65 samples in window"}

    span_s = samples[-1][0] - samples[0][0]
    expected = span_s / 10.0            # console cadence is ~10 s
    coverage = min(1.0, len(samples) / expected) if expected > 0 else 0.0

    windows = split_windows(samples, gap_s, on_threshold_w)
    rows = []
    for w in windows:
        duration = w[-1][0] - w[0][0]
        if duration < min_window_s:
            continue                     # a blip, not a cycle
        wh = integrate(w)
        rows.append({
            "start_utc": datetime.fromtimestamp(w[0][0], timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_s": round(duration, 1),
            "samples": len(w),
            "wh": round(wh, 4),
            "mean_w": round(wh * 3600.0 / duration, 4) if duration else None,
            "peak_w": round(max(x[1] for x in w), 4),
            "min_w": round(min(x[1] for x in w), 4),
        })

    # ONLY COMPLETE WINDOWS COUNT.
    # A console dropout inside a cycle splits it into fragments, and a
    # fragment's Wh is a fraction of a cycle's -- averaging fragments in
    # under-reports energy badly (measured: 0.046 vs the true 0.173 Wh on
    # the Sprint10 overnight window). The bus schedule is fixed, so a
    # complete cycle has a characteristic duration; take the longest
    # observed window as that duration and accept anything within 10 % of
    # it. Fragments are still listed per-window, just not averaged in.
    full_len = max((r["duration_s"] for r in rows), default=0.0)
    for r in rows:
        r["complete_window"] = r["duration_s"] >= 0.9 * full_len
    full = [r for r in rows if r["complete_window"]]

    def median(values):
        if not values:
            return None
        s = sorted(values)
        n = len(s)
        return round(s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]), 4)

    whs = [r["wh"] for r in full]

    return {
        "spotter_id": spot,
        "unit": unit,
        "role": role,
        "bridge_node": bridge,
        "sensor": "bridge addr-65 (DESIGN D9)",
        "source": "USB console capture (cross-check; SD card is authoritative)",
        "samples": len(samples),
        "console_coverage_est": round(coverage, 3),
        "coverage_warning": (None if coverage > 0.9 else
                             "console capture has gaps — energy per cycle is "
                             "still valid per surviving window, but the cycle "
                             "COUNT is not a run total"),
        "windows_found": len(rows),
        "complete_windows": len(full),
        "fragments_excluded": len(rows) - len(full),
        "full_window_s": round(full_len, 1),
        # METRIC 2 — the headline, over COMPLETE windows only.
        "wh_per_cycle_median": median(whs),
        "wh_per_cycle_mean": (round(sum(whs) / len(whs), 4) if whs else None),
        "wh_per_cycle_min": min(whs) if whs else None,
        "wh_per_cycle_max": max(whs) if whs else None,
        "cycles": rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--console-root", default=os.path.expanduser("~/spotter_logs"))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--gap-s", type=float, default=300.0,
                    help="console silence this long ends a window. Default "
                         "300 s: the bus-off period is marked by ~0 W "
                         "SAMPLES, not by their absence, so this only needs "
                         "to survive host-side capture dropouts")
    ap.add_argument("--on-threshold-w", type=float, default=0.05,
                    help="above this is bus-ON (halted Pi draws ~0.42 W)")
    ap.add_argument("--min-window-s", type=float, default=120.0,
                    help="shorter runs are blips, not cycles")
    args = ap.parse_args(argv)

    start, end = parse_iso(args.start), parse_iso(args.end)
    out = {"generated_utc": datetime.now(timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
           "window": {"start": args.start, "end": args.end},
           "devices": {}}
    for spot in UNITS:
        report = analyze_unit(args.console_root, spot, start, end, args.gap_s,
                              args.on_threshold_w, args.min_window_s)
        out["devices"][spot] = report
        if "error" in report:
            print(f"[{spot}] {report['error']}", file=sys.stderr)
            continue
        print(f"[{spot}] {report['unit']} ({report['role']}): "
              f"{report['wh_per_cycle_median']} Wh/cycle median over "
              f"{report['complete_windows']} complete "
              f"{report['full_window_s']:.0f} s windows "
              f"({report['fragments_excluded']} fragments excluded, "
              f"console coverage {report['console_coverage_est']})",
              file=sys.stderr)
        if report["coverage_warning"]:
            print(f"[{spot}][WARN] {report['coverage_warning']}", file=sys.stderr)

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        jpath = os.path.join(args.out_dir, "energy_bridge_console.json")
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        cpath = os.path.join(args.out_dir, "energy_by_cycle.csv")
        with open(cpath, "w", newline="", encoding="utf-8") as f:
            wtr = csv.writer(f)
            wtr.writerow(["spotter_id", "unit", "start_utc", "duration_s",
                          "samples", "wh", "mean_w", "peak_w", "min_w",
                          "complete_window"])
            for spot, rep in out["devices"].items():
                for r in rep.get("cycles", []):
                    wtr.writerow([spot, rep["unit"], r["start_utc"],
                                  r["duration_s"], r["samples"], r["wh"],
                                  r["mean_w"], r["peak_w"], r["min_w"],
                                  r["complete_window"]])
        print(f"[written] {jpath}\n[written] {cpath}", file=sys.stderr)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
