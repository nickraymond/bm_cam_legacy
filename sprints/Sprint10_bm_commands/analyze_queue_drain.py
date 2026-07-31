#!/usr/bin/env python3
# filename: analyze_queue_drain.py
# description: Sprint10 Phase E — join backend arrivals to send logs; find blackouts.
"""
Sprint10 Phase E — analyze cellular queue drain / sync blackouts.

Runs on the MAC (needs SOFAR_API_TOKEN_BM_REEF). Takes the send logs +
manifest produced on the Pi by test_queue_drain.py, fetches what actually
arrived at api/sensor-data, and reports per burst:

  - delivered / sent, loss %
  - every GAP (consecutive run of missing seqs) with: first/last seq, the
    wall-clock offset into the burst where it started (from the send log),
    and its duration in seconds -> the blackout window
  - sustained delivered-messages-per-minute (drain rate)
  - a CSV row per burst + a CSV row per gap for plotting

MECHANISM TEST (the point of the first run): compare `gap_start_s` and
`gap_start_seq` across bursts of the same count at different delays.
  time-triggered  -> gap_start_s similar, gap_start_seq scales with 1/delay
  count-triggered -> gap_start_seq similar, gap_start_s scales with delay

USAGE
    export SOFAR_API_TOKEN_BM_REEF=...
    python3 analyze_queue_drain.py --spotter-id SPOT-33507C \
        --manifest runs/sprint10_phaseE/manifest_FULL.json \
        --sendlog-dir runs/sprint10_phaseE \
        --out-dir runs/sprint10_phaseE

Notes
  - Backend lag is 13-30 min; run the analysis AFTER the last burst has
    had time to land, and re-run it later to catch stragglers (it is
    idempotent).
  - Payloads are the Sprint09 TST format, hex-encoded in `value`.
"""

import argparse
import csv
import json
import os
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

TST_RE = re.compile(r"TST,(?P<burst>[A-Za-z0-9]+),(?P<seq>\d{5}),")


def _ctx():
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
    return ctx


def fetch_arrivals(spotter_id, token, hours):
    """{burst_id: {seq: timestamp}} from api/sensor-data."""
    now = datetime.now(timezone.utc)
    url = "https://api.sofarocean.com/api/sensor-data?" + urlencode({
        "spotterId": spotter_id,
        "startDate": (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDate": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "token": token,
    })
    with urlopen(url, timeout=90, context=_ctx()) as resp:
        rows = json.load(resp).get("data", [])
    out = {}
    for r in rows:
        try:
            text = bytes.fromhex(str(r.get("value", "")).strip()).decode(
                "utf-8", "replace")
        except ValueError:
            continue
        m = TST_RE.search(text)
        if m:
            out.setdefault(m.group("burst"), {})[int(m.group("seq"))] = \
                r.get("timestamp")
    return out


def load_sendlog(path):
    """{seq: t_offset_s}"""
    out = {}
    try:
        with open(path, "r", encoding="ascii") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                out[rec["seq"]] = rec["t_offset_s"]
    except FileNotFoundError:
        pass
    return out


def find_gaps(sent_seqs, got_seqs, offsets):
    """Consecutive runs of missing seqs -> blackout windows."""
    gaps, run = [], []
    for s in sorted(sent_seqs):
        if s not in got_seqs:
            run.append(s)
        elif run:
            gaps.append(run)
            run = []
    if run:
        gaps.append(run)
    out = []
    for run in gaps:
        first, last = run[0], run[-1]
        t0, t1 = offsets.get(first), offsets.get(last)
        out.append({
            "gap_start_seq": first, "gap_end_seq": last, "gap_len": len(run),
            "gap_start_s": t0,
            "gap_span_s": (round(t1 - t0, 1) if t0 is not None and t1 is not None
                           else None),
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spotter-id", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--sendlog-dir", default=None,
                    help="defaults to the manifest's directory")
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)

    token = os.environ.get("SOFAR_API_TOKEN_BM_REEF")
    if not token:
        print("[ERROR] set SOFAR_API_TOKEN_BM_REEF")
        return 2
    with open(args.manifest) as f:
        manifest = json.load(f)
    sl_dir = args.sendlog_dir or os.path.dirname(os.path.abspath(args.manifest))
    out_dir = args.out_dir or sl_dir
    os.makedirs(out_dir, exist_ok=True)

    arrivals = fetch_arrivals(args.spotter_id, token, args.hours)
    burst_rows, gap_rows = [], []
    print(f"# {args.spotter_id}: {len(arrivals)} burst id(s) seen at backend\n")
    for b in manifest["bursts"]:
        bid, count, delay_ms = b["burst_id"], b["count"], b["delay_ms"]
        offsets = load_sendlog(
            os.path.join(sl_dir, os.path.basename(b.get("sendlog", ""))))
        got = arrivals.get(bid, {})
        sent = set(range(count))
        gaps = find_gaps(sent, got, offsets)
        delivered = len(got)
        loss_pct = 100.0 * (count - delivered) / count if count else 0.0
        wall_min = (b.get("wall_s") or 0) / 60.0
        row = {
            "burst_id": bid, "count": count, "delay_ms": delay_ms,
            "delivered": delivered, "lost": count - delivered,
            "loss_pct": round(loss_pct, 2),
            "delivered_per_min": round(delivered / wall_min, 1) if wall_min else None,
            "n_gaps": len(gaps),
            "first_gap_start_s": gaps[0]["gap_start_s"] if gaps else None,
            "first_gap_start_seq": gaps[0]["gap_start_seq"] if gaps else None,
            "first_gap_len": gaps[0]["gap_len"] if gaps else 0,
        }
        burst_rows.append(row)
        for g in gaps:
            gap_rows.append(dict(burst_id=bid, count=count,
                                 delay_ms=delay_ms, **g))
        star = "  <-- 100%" if delivered == count else ""
        print(f"{bid:>16}  {delivered:>3}/{count:<3} lost={count - delivered:<3} "
              f"({loss_pct:4.1f}%)  gaps={len(gaps)}  "
              f"first@ seq {row['first_gap_start_seq']} "
              f"t={row['first_gap_start_s']}s len={row['first_gap_len']}{star}")

    b_csv = os.path.join(out_dir, f"bursts_{args.spotter_id}.csv")
    g_csv = os.path.join(out_dir, f"gaps_{args.spotter_id}.csv")
    if burst_rows:
        with open(b_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(burst_rows[0]))
            w.writeheader()
            w.writerows(burst_rows)
    if gap_rows:
        with open(g_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(gap_rows[0]))
            w.writeheader()
            w.writerows(gap_rows)
    print(f"\n# wrote {b_csv}")
    if gap_rows:
        print(f"# wrote {g_csv}")
        starts = [g["gap_start_s"] for g in gap_rows if g["gap_start_s"]]
        seqs = [g["gap_start_seq"] for g in gap_rows]
        if starts:
            print(f"# gap-start seconds: min={min(starts):.0f} "
                  f"max={max(starts):.0f} mean={sum(starts) / len(starts):.0f}")
            print(f"# gap-start seqs:    min={min(seqs)} max={max(seqs)}")
            print("# -> tight SECONDS spread = time-triggered; "
                  "tight SEQ spread = count-triggered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
