#!/usr/bin/env python3
# filename: count_complete_images.py
# description: Sprint11 metric 1 — count COMPLETE images per device at the Sofar backend.
"""
Sprint11 metric 1: how many complete images each device delivered.

WHY NOT CHUNK-DELIVERY PERCENT (DESIGN D8)
The Sprint10 overnight A/B had the two arms at 95.80 % vs 95.07 % chunk
delivery -- statistically indistinguishable -- while producing 0/12 and 6/12
COMPLETE images. These are progressive JPEGs: the stream is usable only up to
its first gap, so losing chunk 17 of 169 wastes the 152 chunks you already
paid to send. A percentage hides the only outcome that matters. This tool
therefore leads with complete images and reports chunk % only as a footnote.

An image is COMPLETE when:
  - its START IMG arrived (so we know the planned `length`), AND
  - every chunk index 0..length-1 arrived, AND
  - its END IMG arrived.
Anything else is incomplete, and we report WHERE the first gap is, because
that is what tells you whether the loss was periodic (mid-image, the blackout
signature) or sporadic.

CHUNK -> IMAGE ATTRIBUTION
With the media_gid island on, chunks are `<I{gid}.{i}>` and START carries
`gid: xxx` -- exact attribution, no guessing. With it off, chunks are
`<I{i}>` and we fall back to Sprint10's rule: a chunk belongs to the most
recent START seen before it, in arrival order. The Sofar cellular path is
NOT FIFO, so the fallback can misattribute a straggler that arrives after
the next START; the tool says so in its output rather than pretending.

Inputs:
  SOFAR_API_TOKEN_BM_REEF   env var with the API token (never on the CLI)
  --spotter-id              e.g. SPOT-33507C (repeatable; one report each)
  --start / --end           UTC ISO window
Outputs: a JSON report on stdout and, with --out, to a file. Exit nonzero
  only on an API failure -- zero complete images is a RESULT, not an error.

Example:
  export SOFAR_API_TOKEN_BM_REEF=...
  python3 tools/count_complete_images.py \\
      --spotter-id SPOT-33507C --spotter-id SPOT-31593C \\
      --start 2026-07-29T20:00:00Z --end 2026-07-30T02:00:00Z \\
      --out runs/sprint11_20260729/complete_images.json

Known limitations: the Sofar sensor-data endpoint's pagination behaviour is
unverified for very large windows (Sprint09 note) -- keep windows tight and
compare `messages_seen` against the device-side `sent=N/N` if in doubt. An
image still draining at query time reads as incomplete; re-run later before
concluding, and the report flags the last image of the window for this.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

SENSOR_DATA_URL = "https://api.sofarocean.com/api/sensor-data"

RE_START = re.compile(r"^<START IMG>\s*(.*)", re.S)
RE_END = re.compile(r"^<END IMG>\s*(.*)", re.S)
# Legacy `<I7>` and media-gid `<Iab2.7>` in one pattern.
RE_CHUNK = re.compile(r"^<I(?:([0-9a-z]{3})\.)?(\d+)>")
RE_FILENAME = re.compile(r"filename:\s*([^,]+)")
RE_LENGTH = re.compile(r"length:\s*(\d+)")
RE_GID = re.compile(r"gid:\s*([0-9a-z]{3})")
RE_SENT = re.compile(r"sent_buffers:\s*(\d+)")


def fetch(spotter_id, start, end, token, limit=5000):
    query = urlencode({"spotterId": spotter_id, "startDate": start,
                       "endDate": end, "token": token, "limit": limit})
    with urlopen(f"{SENSOR_DATA_URL}?{query}", timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data")
    if data is None:
        raise RuntimeError(f"no 'data' in response: {str(payload)[:300]}")
    return data


def decode(entry):
    """Hex payload -> ASCII text, or '' when it is not our traffic."""
    try:
        return bytes.fromhex(entry["value"]).decode("utf-8", "replace")
    except (KeyError, ValueError):
        return ""


class Image:
    def __init__(self, filename, planned, gid, started_utc):
        self.filename = filename
        self.planned = planned
        self.gid = gid
        self.started_utc = started_utc
        self.chunks = set()
        self.ended = False
        self.sent_buffers = None

    def first_gap(self):
        """Index of the first missing chunk, or None if there is none."""
        for i in range(self.planned):
            if i not in self.chunks:
                return i
        return None

    def complete(self):
        return (self.ended and self.planned > 0
                and self.first_gap() is None)

    def report(self):
        gap = self.first_gap()
        return {
            "filename": self.filename,
            "gid": self.gid,
            "started_utc": self.started_utc,
            "planned": self.planned,
            "received": len(self.chunks),
            "end_seen": self.ended,
            "sent_buffers_device": self.sent_buffers,
            "complete": self.complete(),
            "first_gap_index": gap,
            "usable_prefix_pct": (
                round(100.0 * (gap if gap is not None else self.planned)
                      / self.planned, 1) if self.planned else None),
        }


def analyze(entries):
    """Group messages into images in arrival order."""
    images = []
    by_gid = {}
    current = None          # most recent START, for the legacy fallback
    stats = {"messages_seen": len(entries), "chunks_seen": 0,
             "orphan_chunks": 0, "starts": 0, "ends": 0, "other": 0}

    for entry in sorted(entries, key=lambda e: e.get("timestamp", "")):
        text = decode(entry)
        ts = entry.get("timestamp")

        if text.startswith("<START IMG>"):
            stats["starts"] += 1
            fn = RE_FILENAME.search(text)
            ln = RE_LENGTH.search(text)
            gid = RE_GID.search(text)
            img = Image(fn.group(1).strip() if fn else "unknown",
                        int(ln.group(1)) if ln else 0,
                        gid.group(1) if gid else None, ts)
            images.append(img)
            current = img
            if img.gid:
                by_gid[img.gid] = img
            continue

        if text.startswith("<END IMG>"):
            stats["ends"] += 1
            fn = RE_FILENAME.search(text)
            sent = RE_SENT.search(text)
            name = fn.group(1).strip() if fn else None
            # END correlates by FILENAME (the P4 decision), not by order.
            target = next((i for i in reversed(images)
                           if i.filename == name), current)
            if target is not None:
                target.ended = True
                if sent:
                    target.sent_buffers = int(sent.group(1))
            continue

        m = RE_CHUNK.match(text)
        if m:
            stats["chunks_seen"] += 1
            gid, idx = m.group(1), int(m.group(2))
            target = by_gid.get(gid) if gid else current
            if target is None:
                stats["orphan_chunks"] += 1
                continue
            target.chunks.add(idx)
            continue

        stats["other"] += 1      # wake status, acks, a=inc, telemetry

    return images, stats


def summarize(images, stats, spotter_id, window):
    reports = [i.report() for i in images]
    complete = [r for r in reports if r["complete"]]
    gaps = [r["usable_prefix_pct"] for r in reports if not r["complete"]
            and r["usable_prefix_pct"] is not None]
    planned_total = sum(r["planned"] for r in reports)
    received_total = sum(min(r["received"], r["planned"]) for r in reports)
    return {
        "spotter_id": spotter_id,
        "window": window,
        # METRIC 1 — the headline, per D8.
        "images_attempted": len(reports),
        "complete_images": len(complete),
        "complete_ratio": f"{len(complete)}/{len(reports)}",
        # Footnotes. Never lead with these.
        "chunk_delivery_pct": (round(100.0 * received_total / planned_total, 2)
                               if planned_total else None),
        "mean_usable_prefix_pct_of_incomplete": (
            round(sum(gaps) / len(gaps), 1) if gaps else None),
        "attribution": ("gid (exact)" if any(r["gid"] for r in reports)
                        else "arrival-order fallback (non-FIFO path: a "
                             "straggler arriving after the next START can be "
                             "misattributed)"),
        "parser_stats": stats,
        "images": reports,
        "note_last_image": ("the final image in the window may still be "
                            "draining at query time — re-run later before "
                            "counting it as incomplete"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spotter-id", action="append", required=True)
    ap.add_argument("--start", required=True, help="UTC ISO, e.g. 2026-07-29T20:00:00Z")
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    token = os.environ.get("SOFAR_API_TOKEN_BM_REEF")
    if not token:
        print("[ERROR] SOFAR_API_TOKEN_BM_REEF not set in the environment",
              file=sys.stderr)
        return 2

    window = {"start": args.start, "end": args.end}
    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "devices": {}}
    for spotter_id in args.spotter_id:
        print(f"[fetch] {spotter_id} {args.start} .. {args.end}", file=sys.stderr)
        try:
            entries = fetch(spotter_id, args.start, args.end, token)
        except Exception as exc:
            print(f"[ERROR] {spotter_id}: {exc}", file=sys.stderr)
            return 1
        images, stats = analyze(entries)
        report = summarize(images, stats, spotter_id, window)
        out["devices"][spotter_id] = report
        print(f"[{spotter_id}] COMPLETE IMAGES: {report['complete_ratio']}"
              f"   (chunk delivery {report['chunk_delivery_pct']} % — "
              f"not the metric, see D8)", file=sys.stderr)

    text = json.dumps(out, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"[written] {args.out}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
