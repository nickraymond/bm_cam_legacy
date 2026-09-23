"""Scratch: Spotter->Notecard hand-off stalls per video burst, from SD MS.log.

Hand-off = MS `Queuing message <id>` (one per message the MS task dequeues to the Notecard).
Stall  = gap between consecutive hand-offs inside a burst. Rejections charged to a gap =
`Queue MS_Q_CELLULAR_ONLY is full` lines inside it. Bursts = clusters of CELLULAR_ONLY adds
(excluding the 6124 B HDR push) with gaps < 20 s and >= 100 adds.
A gap is tagged `sync` when a LEGACY report was queued <= 60 s before it ends (report sync stall).
Also: Notecard `pct full` drops (= outbound drained) -> delay from burst end to the next drop.
Usage: stall_analysis.py LABEL START END MS.log [MS.log ...]  -> JSON on stdout
"""
import json, re, sys
from datetime import datetime
lab, t0s, t1s, files = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
P = lambda s: datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp() + float("0" + s[19:23])
T0, T1 = P(t0s), P(t1s)
TS = re.compile(r"^\.?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3})Z \[MS\]")
deq, full, adds, legacy, pct = [], [], [], [], []
for f in files:
    for line in open(f, errors="replace"):
        m = TS.match(line)
        if not m: continue
        t = P(m.group(1))
        if not T0 <= t <= T1: continue
        if "Queuing message" in line: deq.append(t)
        elif "MS_Q_CELLULAR_ONLY is full" in line: full.append(t)
        elif "to queue MS_Q_CELLULAR_ONLY" in line and "len: 6124" not in line: adds.append(t)
        elif "to queue MS_Q_LEGACY" in line: legacy.append(t)
        elif "pct full" in line:
            pct.append((t, float(re.search(r"is ([\d.]+) pct", line).group(1))))
deq.sort(); full.sort(); adds = sorted(adds + full)  # a sync stall rejects for 40+ s: cluster on arrivals, accepted or not
bursts, cur = [], [adds[0]]
for t in adds[1:]:
    if t - cur[-1] < 20: cur.append(t)
    else: bursts.append(cur); cur = [t]
bursts.append(cur)
bursts = [(b[0], b[-1]) for b in bursts if len(b) >= 100]
buckets = {"2-3": [0, 0], "3-3.5": [0, 0], "3.5-4": [0, 0], "4-6": [0, 0], "6-9": [0, 0], "9-15": [0, 0], ">=15": [0, 0]}
sync_b = {"n": 0, "rej": 0}
per_burst, gaps_total, rej_total = [], 0, 0
for b0, b1 in bursts:
    d = [b0] + [t for t in deq if b0 - 1 <= t <= b1 + 3]  # b0: burst start counts as a hand-off slot opening
    rej_b = sum(1 for t in full if b0 - 5 <= t <= b1 + 5)
    stalls4 = 0
    for a, z in zip(d, d[1:]):
        g = z - a; gaps_total += 1
        r = sum(1 for t in full if a < t <= z)
        if g < 2: continue
        if any(0 <= z - L <= 60 or a <= L <= z for L in legacy):
            if g >= 3.5: sync_b["n"] += 1; sync_b["rej"] += r; sync_b.setdefault("len", []).append(round(g))
            continue
        k = "2-3" if g < 3 else "3-3.5" if g < 3.5 else "3.5-4" if g < 4 else "4-6" if g < 6 else "6-9" if g < 9 else "9-15" if g < 15 else ">=15"
        buckets[k][0] += 1; buckets[k][1] += r
        stalls4 += g >= 3.5
    # Notecard drain: first pct drop after the burst end
    after = [(t, p) for t, p in pct if t >= b1]
    drain = None
    for (ta, pa), (tb, pb) in zip(after, after[1:]):
        if pb < pa: drain = round(tb - b1); break
    rej_total += rej_b
    per_burst.append({"start": datetime.fromtimestamp(b0).strftime("%H:%M:%S"), "dur": round(b1 - b0),
                      "rej": rej_b, "stalls_ge4": stalls4, "drain_s": drain})
drains = sorted(x["drain_s"] for x in per_burst if x["drain_s"] is not None)
print(json.dumps({"label": lab, "bursts": len(bursts), "handoff_gaps": gaps_total, "rejections": rej_total,
                  "stall_buckets_count_rej": buckets, "sync_stalls_ge4": sync_b,
                  "drain_s_median": drains[len(drains)//2] if drains else None, "drain_s_max": drains[-1] if drains else None,
                  "pct_max": max((p for _, p in pct), default=None), "per_burst": per_burst}))
