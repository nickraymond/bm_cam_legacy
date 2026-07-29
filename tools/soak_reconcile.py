#!/usr/bin/env python3
# filename: soak_reconcile.py
# description: Sprint10 soak — reconcile backend data against sends/cycles.
"""
Sprint10 24 h soak — backend reconciliation sweep (SOAK_PLAN_24H.md).

Pulls api/sensor-data for a Spotter and classifies every row:
  ack      command acks {"id":N,"ok":..}          -> id, ok, st, node
  chunk    image chunks  <I{i}>base64             -> index i
  start    <START,...> / START envelopes          -> filename/meta
  end      <END,...> / END envelopes
  ws       wake-status <WS...> messages
  other    anything else (listed, never silently dropped)

Emits a JSON summary (stdout or --out) with the report's headline
numbers for one unit: acks seen (by id), image chunk coverage per
START..END group (complete / missing indexes), and row counts.

Usage:
  python3 tools/soak_reconcile.py --spotter-id SPOT-33507C --hours 6 \
      --out runs/sprint10_soak_20260727/reconcile_33507C_<ts>.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

from sofar_poll_acks import (_ssl_context, decode_value, extract_ack,  # noqa: E402
                             normalize_node_id)
from urllib.parse import urlencode  # noqa: E402
from urllib.request import urlopen  # noqa: E402

CHUNK_RE = re.compile(r"^<I(?:([0-9a-z]{1,6})\.)?(\d+)>")
START_FIELDS_RE = re.compile(
    r"length: (?P<length>\d+)|gid: (?P<gid>[0-9a-z]{1,6})|"
    r"filename: (?P<fn>[^,]+)")


def classify(text):
    if text is None:
        return "undecodable", None
    s = text.strip()
    m = CHUNK_RE.match(s)
    if m:
        return "chunk", (m.group(1), int(m.group(2)))  # (gid|None, index)
    if s.startswith("<WS") or s.startswith("WS,"):
        return "ws", s[:40]
    if "START" in s[:12]:
        return "start", s[:80]
    if "END" in s[:12]:
        return "end", s[:80]
    ack = extract_ack(s)
    if ack is not None:
        return "ack", ack
    return "other", s[:60]


def sweep(spotter_id, token, hours):
    now = datetime.now(timezone.utc)
    url = "https://api.sofarocean.com/api/sensor-data?" + urlencode({
        "spotterId": spotter_id,
        "startDate": (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDate": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "token": token,
    })
    with urlopen(url, timeout=60, context=_ssl_context()) as resp:
        return json.load(resp).get("data", [])


def _start_fields(text):
    """Pull length/gid/filename out of a START message body."""
    out = {}
    for m in START_FIELDS_RE.finditer(text or ""):
        for k in ("length", "gid", "fn"):
            if m.group(k):
                out[k] = int(m.group(k)) if k == "length" else m.group(k).strip()
    return out


def reconcile(rows):
    """Group chunks into images. gid-tagged chunks (`<Igid.i>`) attribute
    exactly to their gid's group regardless of arrival order; legacy
    chunks fall back to arrival-order attribution between STARTs."""
    out = {"rows": len(rows), "counts": {}, "acks": [], "images": [],
           "other": [], "undecodable": 0}
    current = None            # legacy arrival-order group
    by_gid = {}               # gid -> group dict
    for r in sorted(rows, key=lambda r: r.get("timestamp", "")):
        kind, val = classify(decode_value(r.get("value")))
        out["counts"][kind] = out["counts"].get(kind, 0) + 1
        ts = r.get("timestamp", "?")
        if kind == "ack":
            out["acks"].append({
                "ts": ts, "id": val["id"], "ok": val.get("ok"),
                "e": val.get("e"), "st": val.get("st"),
                "node": normalize_node_id(r.get("bristlemouth_node_id"))})
        elif kind == "start":
            f = _start_fields(val)
            group = {"start_ts": ts, "start": val, "chunks": set(),
                     "end": None, "gid": f.get("gid"),
                     "declared_length": f.get("length"),
                     "filename": f.get("fn")}
            out["images"].append(group)
            if f.get("gid"):
                by_gid[f["gid"]] = group
            else:
                current = group
        elif kind == "chunk":
            gid, idx = val
            if gid is not None:
                group = by_gid.get(gid)
                if group is None:  # straggler whose START is outside window
                    group = {"start_ts": ts, "start": f"(no START; gid {gid})",
                             "chunks": set(), "end": None, "gid": gid,
                             "declared_length": None, "filename": None}
                    out["images"].append(group)
                    by_gid[gid] = group
                group["chunks"].add(idx)
            else:
                if current is None:
                    current = {"start_ts": ts, "start": "(no START seen)",
                               "chunks": set(), "end": None, "gid": None,
                               "declared_length": None, "filename": None}
                    out["images"].append(current)
                current["chunks"].add(idx)
        elif kind == "end":
            # END has no gid by design; correlate to the newest group
            # missing an END (legacy behavior preserved).
            target = current
            if target is None or target.get("end") is not None:
                open_groups = [g for g in out["images"] if g["end"] is None]
                target = open_groups[-1] if open_groups else None
            if target is not None:
                target["end"] = val
                m = re.search(r"sent_buffers: (\d+)", val or "")
                if m:
                    target["sent_buffers"] = int(m.group(1))
            if target is current:
                current = None
        elif kind == "other":
            out["other"].append({"ts": ts, "text": val})
        elif kind == "undecodable":
            out["undecodable"] += 1
    for img in out["images"]:
        chunks = img.pop("chunks")
        # Loss accounting compares against what the DEVICE says it sent
        # (END sent_buffers), else START planned length, else max index.
        n = img.get("sent_buffers") or img.get("declared_length") or \
            ((max(chunks) + 1) if chunks else 0)
        img["chunk_count"] = len(chunks)
        img["max_index"] = (max(chunks) if chunks else -1)
        img["missing"] = sorted(set(range(n)) - chunks)[:30]
        img["complete"] = bool(chunks) and not img["missing"] and \
            img["end"] is not None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spotter-id", required=True)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    token = os.environ.get("SOFAR_API_TOKEN_BM_REEF")
    if not token:
        print("[ERROR] set SOFAR_API_TOKEN_BM_REEF")
        return 2
    rows = sweep(args.spotter_id, token, args.hours)
    out = reconcile(rows)
    out["spotter_id"] = args.spotter_id
    out["swept_hours"] = args.hours
    out["swept_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = json.dumps(out, indent=2, default=str)
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as f:
            f.write(text)
        comp = sum(1 for i in out["images"] if i["complete"])
        print(f"[reconcile] {args.spotter_id}: rows={out['rows']} "
              f"acks={len(out['acks'])} images={comp}/{len(out['images'])} "
              f"complete -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
