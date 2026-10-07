#!/usr/bin/env python3
"""hil_sync_profile.py — profile a Spotter's own syncs (the :05 hourly report and the health check) from the nereus000
console capture: what the Spotter itself queues, how long its sync waits for TX, and the Notecard fill around it.

Read-only. Runs where the console logs are (nereus000) or on a pulled copy.
Per sync (each `[MS] [DEBUG] Attempting to Sync.`):
  - spotter_msgs: `Added message(id N len L) to queue Q` within 10 s BEFORE the sync start that are NOT followed by a
    `[BM_TX] Submitted` line (i.e. the Spotter's own: LEGACY report, HDR, health), as "Q:len" (HDR flagged);
  - tx_wait_s: sync start → `All messages sent successfully!` (or `-` if none within 15 min); `waiting_for_tx` = the
    `Waiting for TX` line seen;
  - nc_before / nc_after: the last `Notecard is N pct full` before the sync and the first one ≥ 60 s after its end;
  - hdr_slot_hold_s: for the Spotter's own MS_Q_CELLULAR_ONLY message(s) at the sync (HDR), the time from `Added
    message(id N …)` to `Queuing message N` (handed to the Notecard) = how long it holds 1 of the 2 cellular slots;
  - queue_full_in_sync: `MS_Q_CELLULAR_ONLY is full` lines between sync start and end (end = start + 15 min if open).
Inputs:  --log console_YYYYMMDD.log (one or more), --since/--until UTC ISO (host receive time, field 1)
Outputs: CSV on stdout (spotter, sync_start, kind, spotter_msgs, waiting_for_tx, tx_wait_s, nc_before, nc_after,
         queue_full_in_sync)
Example: python3 hil_sync_profile.py --log /home/pi/spotter_logs/SPOT-31593C/console_20261007.log --since 2026-10-07T00:00
Limits:  times are the Spotter's own stamps (field 2) when present; a sync whose end falls outside the input is `-`.
"""
import argparse
import csv
import datetime as dt
import re
import sys

TS = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)")


def t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", nargs="+", required=True)
    ap.add_argument("--since", default="1970-01-01T00:00:00Z")
    ap.add_argument("--until", default="2100-01-01T00:00:00Z")
    ap.add_argument("--spot", default="")
    a = ap.parse_args()
    lo, hi = a.since if a.since.endswith("Z") else a.since + "Z", a.until if a.until.endswith("Z") else a.until + "Z"
    ev = []
    for path in a.log:
        for ln in open(path, errors="replace"):
            p = ln.rstrip("\n").split(" ", 1)
            if len(p) < 2 or not (lo <= p[0] <= hi):
                continue
            m = TS.search(p[1][:40])
            if not m:
                continue
            ev.append((t(m.group(1)), p[1]))
    out = csv.writer(sys.stdout)
    out.writerow(["spotter", "sync_start", "kind", "spotter_msgs", "waiting_for_tx", "tx_wait_s", "nc_before",
                  "nc_after", "queue_full_in_sync", "hdr_slot_hold_s"])
    for i, (ts, x) in enumerate(ev):
        if "Attempting to Sync" not in x:
            continue
        before = [(tt, xx) for tt, xx in ev[max(0, i - 400):i] if (ts - tt).total_seconds() <= 10]
        own, kind, cell_ids = [], "other", []
        for j, (tt, xx) in enumerate(before):
            m = re.search(r"Added message\(id: (\d+) len: (\d+)\) to queue (\w+)", xx)
            if m:
                nxt = before[j + 1][1] if j + 1 < len(before) else ""
                if "Submitted" not in nxt:
                    own.append(f"{m.group(3).replace('MS_Q_', '')}:{m.group(2)}" + ("(HDR)" if m.group(2) == "6129" else ""))
                    if m.group(3) == "MS_Q_CELLULAR_ONLY":
                        cell_ids.append((m.group(1), tt))
            if "Running health check" in xx:
                kind = "health"
            if "MS_Q_LEGACY" in xx and kind == "other":
                kind = "report"
        end, waiting = None, False
        for tt, xx in ev[i + 1:]:
            if (tt - ts).total_seconds() > 900:
                break
            if "Waiting for TX" in xx:
                waiting = True
            if "All messages sent successfully" in xx:
                end = tt
                break
        stop = end or ts + dt.timedelta(seconds=900)
        ncb = [re.search(r"Notecard is ([0-9.]+) pct", xx) for tt, xx in ev[max(0, i - 2000):i]]
        ncb = [float(m.group(1)) for m in ncb if m]
        nca = [float(m.group(1)) for tt, xx in ev[i:] if (tt - stop).total_seconds() >= 60
               for m in [re.search(r"Notecard is ([0-9.]+) pct", xx)] if m][:1]
        qf = sum("MS_Q_CELLULAR_ONLY is full" in xx for tt, xx in ev[i:] if tt <= stop)
        holds = []
        for cid, tadd in cell_ids:
            q = next((tt for tt, xx in ev[i:] if (tt - ts).total_seconds() <= 1800 and f"Queuing message {cid} " in xx + " "), None)
            holds.append(round((q - tadd).total_seconds(), 1) if q else "-")
        out.writerow([a.spot, ts.strftime("%Y-%m-%dT%H:%M:%SZ"), kind, " ".join(own) or "-", waiting,
                      round((end - ts).total_seconds(), 1) if end else "-", ncb[-1] if ncb else "",
                      nca[0] if nca else "", qf, " ".join(str(h) for h in holds) or "-"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
