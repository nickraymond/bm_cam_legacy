#!/usr/bin/env python3
"""hil_r2_score.py — score one still wake for R2-DELAY (hil/gates/R2_DELAY.md) from its console excerpt.

First-send loss is judged at the Spotter: the chunks of the wake's START key that the Spotter ACCEPTED (decoded
`<I{key}.{n}/{M}>` payloads after `Submitted spotter/transmit-data … cell-only queue`) vs M (START length).
Heal re-sends of OTHER keys in the same wake are ignored. Contiguous gaps = runs of missing indices.
Also: START/END times, queue_full times (`MS_Q_CELLULAR_ONLY is full`), and the Spotter's own sync / HDR / health
lines inside the burst (times), so the holes can be put next to the report/sync.
Inputs:  --console wake_<SPOT>_<ts>.txt (hil_wake_report.sh excerpt), [--key 6-char START key; default = the
         first START in the excerpt], --arm A|B, --out-json
Outputs: one JSON line (and --out-json file): key, M, accepted, missing, loss_pct, gaps [[start_idx, len, t_first]],
         handoff_stalls {n_gt_1s, longest_s, rejects_in_stalls, rejects_outside},
         max_gap, queue_full [times], start_t, end_t, spotter_events [[t, what]], clean (loss <= 2 % and max_gap < 5)
Example: python3 hil/tools/hil_r2_score.py --console runs/r2_delay_20261006/console/wake_SPOT-33507C_2026-10-07T0700.txt --arm A
Limits:  relies on the console capture being complete (a capture gap counts as a loss: cross-check with the
         backend row); the decode reuses hil_con_decode.py's hex-dump rule.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--console", required=True)
    ap.add_argument("--key")
    ap.add_argument("--arm", default="")
    ap.add_argument("--out-json")
    a = ap.parse_args()
    raw = open(a.console, errors="replace").read()
    dec = subprocess.run([sys.executable, os.path.join(HERE, "hil_con_decode.py")], input=raw,
                         capture_output=True, text=True).stdout
    start = re.search(r"CELL (\S+) .*?<START IMG>[^\n]*?length: (\d+), key=([0-9a-z]{6})", dec)
    key = a.key or (start.group(3) if start else None)
    if not key:
        print(json.dumps({"error": "no START key in the excerpt"})); return 2
    m_total = None
    for s in re.finditer(r"CELL (\S+) .*?<START IMG>[^\n]*?length: (\d+), key=([0-9a-z]{6})", dec):
        if s.group(3) == key:
            m_total, start_t = int(s.group(2)), s.group(1); break
    acc = {}
    for c in re.finditer(r"CELL (\S+) [^\n]*?<I" + key + r"\.(\d+)(?:/(\d+))?>", dec):
        acc.setdefault(int(c.group(2)), c.group(1))
        if c.group(3) and not m_total:
            m_total = int(c.group(3))
    end = re.search(r"CELL (\S+) [^\n]*?<END IMG>", dec)
    missing = [i for i in range(m_total or 0) if i not in acc]
    gaps, run = [], []
    for i in missing:
        if run and i == run[-1] + 1:
            run.append(i)
        else:
            if run:
                gaps.append(run)
            run = [i]
    if run:
        gaps.append(run)
    qf = re.findall(r"(\d{4}-\d\d-\d\dT[\d:.]+Z) \[MS\] \[ERROR\] Queue MS_Q_CELLULAR_ONLY is full", raw)
    ev = []
    for t, what in re.findall(r"(\d{4}-\d\d-\d\dT[\d:.]+Z) (\[ORC\] \[INFO\] Running health check|\[MS\] \[DEBUG\] Attempting to Sync|"
                              r"\[MS\] \[DEBUG\] Waiting for TX|\[MS\] \[INFO\] All messages sent successfully|"
                              r"\[HDR\] \[INFO\] HDR Message \d+ added|\[MS\] \[INFO\] Added message\(id: \d+ len: \d+\) to queue MS_Q_LEGACY)", raw):
        ev.append([t, re.sub(r"^\[\w+\] \[\w+\] ", "", what)])
    # Hand-off stalls (EM 2026-10-07): per cellular message, Spotter time `Added message(id N …) MS_Q_CELLULAR_ONLY`
    # → `Queuing message N` (handed to the Notecard). A stall = drain > 1 s; rejects inside a stall interval are
    # attributed to the Notecard hand-off, the rest to other causes (Spotter syncs / HDR / unknown).
    import datetime as _dt
    def _t(x):
        return _dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
    added = {i: _t(t) for t, i in re.findall(r"(\d{4}-\d\d-\d\dT[\d:.]+Z) \[MS\] \[INFO\] Added message\(id: (\d+) len: \d+\) to queue MS_Q_CELLULAR_ONLY", raw)}
    queued = {i: _t(t) for t, i in re.findall(r"(\d{4}-\d\d-\d\dT[\d:.]+Z) \[MS\] \[INFO\] Queuing message (\d+) ", raw)}
    stalls = sorted((added[i], queued[i]) for i in added if i in queued and (queued[i] - added[i]).total_seconds() > 1.0)
    qf_t = [_t(x) for x in qf]
    in_stall = sum(any(a0 <= x <= b0 for a0, b0 in stalls) for x in qf_t)
    # HDR marks (EM 2026-10-07): the Spotter's own HDR message every 5 min (:x4:59/:x9:59 on SPOT-33507C). A reject is
    # attributed to an HDR if it falls inside a hand-off stall AND within 60 s after an HDR was added.
    hdr_t = [_t(t) for t in re.findall(r"(\d{4}-\d\d-\d\dT[\d:.]+Z) \[HDR\] \[INFO\] HDR Message \d+ added", raw)]
    st_t = _t(start.group(1)) if start else None
    en = re.search(r"CELL (\S+) [^\n]*?<END IMG>", dec)
    en_t = _t(en.group(1)) if en else None
    crossed = [h.strftime("%H:%M:%S") for h in hdr_t if st_t and en_t and st_t <= h <= en_t]
    rej_hdr = sum(1 for x in qf_t if any(a0 <= x <= b0 for a0, b0 in stalls)
                  and any(0 <= (x - h).total_seconds() <= 60 for h in hdr_t))
    stall_stats = {"n_gt_1s": len(stalls),
                   "hdr_marks_crossed": crossed, "rejects_hdr_stalls": rej_hdr,
                   "rejects_other_stalls": in_stall - rej_hdr,
                   "longest_s": round(max(((b0 - a0).total_seconds() for a0, b0 in stalls), default=0.0), 2),
                   "rejects_in_stalls": in_stall, "rejects_outside": len(qf_t) - in_stall,
                   "first_stall": stalls[0][0].strftime("%H:%M:%S") if stalls else None}
    loss = 100.0 * len(missing) / m_total if m_total else None
    out = {"arm": a.arm, "key": key, "M": m_total, "accepted": len(acc), "missing": len(missing),
           "loss_pct": round(loss, 2) if loss is not None else None,
           "gaps": [[g[0], len(g), acc.get(g[0] - 1, "")] for g in gaps], "max_gap": max((len(g) for g in gaps), default=0),
           "queue_full_n": len(qf), "queue_full_first_last": [qf[0], qf[-1]] if qf else [],
           "handoff_stalls": stall_stats,
           "start_t": start_t if m_total else None, "end_t": end.group(1) if end else None, "spotter_events": ev,
           "clean": bool(loss is not None and loss <= 2.0 and max((len(g) for g in gaps), default=0) < 5)}
    s = json.dumps(out)
    print(s)
    if a.out_json:
        open(a.out_json, "w").write(s + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
