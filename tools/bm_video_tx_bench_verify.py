#!/usr/bin/env python3
"""
bm_video_tx_bench_verify.py -- diff what the Pi sent against what the Spotter
console received, and rebuild the clip from the console capture.

PURPOSE
    Step 3 verifier (2026-09-19). Inputs are two independent records of the
    same burst:
      * the wire file the Pi sent          (tools/bm_video_tx_loopback.py)
      * the Spotter USB console log        (tools/spotter_serial_monitor.py)
    The clip is rebuilt ONLY from the console log, then compared to the
    original payload by sha256. A match proves Pi -> UART -> BM bus -> Spotter
    is byte-exact for video-sized lines at the tested pacing.

INPUTS
    --wire      wire/<variant>.txt that was sent
    --payload   payloads/<variant>.h264 it was built from
    --console   Spotter console log (console_YYYYMMDD.log)
    --tag       burst marker used by the sender (BEGIN/END lines)
    --send-log  optional sender CSV (idx, utc, ...) for pacing comparison
    --fps       frame rate (raw .h264 has no timestamps)
    --out-dir   where to write results

OUTPUTS
    bench_verify.json     counts, sha256 verdict, pacing stats
    bench_per_message.csv idx, sent, received, exact, spotter_epoch, gap_s
    recovered_from_console.h264 (+ .mp4 for viewing)

CONSOLE LINE FORMAT (measured on Spotter v2.16.6, 2026-09-20)
    <monitor utc> <spotter epoch.ms> <node id hex>, <payload>
    The Spotter's own log output can interleave MID-LINE (a `Message: ` prefix
    was seen glued onto a payload line), so messages are found by pattern, not
    by column.

KNOWN LIMITATIONS
    * spotter/printf is not the production transmit topic; this says nothing
      about the transmit queue, Notecard, or cellular (step 4).
    * If interleaving ever lands INSIDE a base64 run the message will show as
      corrupted here even though the UART was fine -- check the raw line.
"""

import argparse
import base64
import csv
import hashlib
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

MSG_RE = re.compile(r"(\d{9,}\.\d+) ([0-9a-f]{16}), <I(\d+)>([A-Za-z0-9+/=]*)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wire", required=True, type=Path)
    ap.add_argument("--payload", required=True, type=Path)
    ap.add_argument("--console", required=True, type=Path)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--send-log", type=Path)
    ap.add_argument("--fps", type=int, required=True)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    sent = {}
    for line in args.wire.read_text().splitlines():
        m = re.match(r"<I(\d+)>(.*)$", line)
        if m:
            sent[int(m.group(1))] = m.group(2)

    text = args.console.read_text(errors="replace")
    begin = text.rfind(f"{args.tag} BEGIN")
    end = text.rfind(f"{args.tag} END")
    if begin < 0:
        sys.exit(f"no '{args.tag} BEGIN' in {args.console}")
    window = text[begin:end if end > begin else len(text)]
    print(f"[verify] tag={args.tag} BEGIN found, END "
          f"{'found' if end > begin else 'MISSING'}; window "
          f"{len(window):,} chars")

    got, epochs, dupes, nodes = {}, {}, 0, set()
    for m in MSG_RE.finditer(window):
        idx = int(m.group(3))
        if idx in got:
            dupes += 1
            continue
        got[idx], epochs[idx] = m.group(4), float(m.group(1))
        nodes.add(m.group(2))

    rows, exact, corrupted, missing = [], 0, [], []
    prev = None
    for idx in sorted(sent):
        rx = got.get(idx)
        ok = rx == sent[idx]
        exact += ok
        if rx is None:
            missing.append(idx)
        elif not ok:
            corrupted.append(idx)
        ep = epochs.get(idx)
        gap = (ep - prev) if (ep is not None and prev is not None) else None
        prev = ep if ep is not None else prev
        rows.append([idx, len(sent[idx]), "" if rx is None else len(rx),
                     ok, "" if ep is None else f"{ep:.3f}",
                     "" if gap is None else f"{gap:.3f}"])

    with open(args.out_dir / "bench_per_message.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["idx", "sent_chars", "rx_chars", "exact", "spotter_epoch",
                    "gap_s"])
        w.writerows(rows)

    # Rebuild from the CONSOLE ONLY (skip gaps -- raw .h264 policy, step 2).
    rebuilt = b"".join(base64.b64decode(got[i]) for i in sorted(got)
                       if i in sent and got[i] == sent[i])
    rec = args.out_dir / "recovered_from_console.h264"
    rec.write_bytes(rebuilt)
    payload = args.payload.read_bytes()
    sha_tx = hashlib.sha256(payload).hexdigest()
    sha_rx = hashlib.sha256(rebuilt).hexdigest()

    view = args.out_dir / "recovered_from_console.mp4"
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-err_detect", "ignore_err", "-y", "-r",
         str(args.fps), "-i", str(rec), "-c:v", "libx264", "-crf", "18",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(view)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    frames = 0
    for l in res.stdout.replace("\r", "\n").splitlines():
        if l.startswith("frame="):
            frames = int(l.split("frame=")[1].split()[0])

    gaps = [float(r[5]) for r in rows if r[5] != ""]
    span = (max(epochs.values()) - min(epochs.values())) if epochs else 0.0
    summary = {
        "tag": args.tag, "console": str(args.console),
        "wire": str(args.wire), "payload": str(args.payload),
        "sent_msgs": len(sent), "received_msgs": len(got),
        "exact_msgs": exact, "missing_idx": missing,
        "corrupted_idx": corrupted, "duplicates": dupes,
        "node_ids_seen": sorted(nodes),
        "payload_bytes": len(payload), "rebuilt_bytes": len(rebuilt),
        "sha256_sent": sha_tx, "sha256_rebuilt": sha_rx,
        "byte_exact": sha_tx == sha_rx,
        "frames_decoded_from_console_rebuild": frames,
        "spotter_clock_span_s": round(span, 3),
        "gap_s": ({"mean": round(statistics.mean(gaps), 3),
                   "min": round(min(gaps), 3), "max": round(max(gaps), 3),
                   "stdev": round(statistics.pstdev(gaps), 3)}
                  if gaps else None),
        "effective_payload_bytes_per_s":
            round(len(rebuilt) / span, 1) if span else None,
        "verdict": "PASS" if sha_tx == sha_rx else
                   ("WARN" if exact >= len(sent) * 0.5 else "FAIL"),
    }
    if args.send_log and args.send_log.is_file():
        with open(args.send_log) as fh:
            summary["sender_rows"] = sum(1 for _ in fh) - 1
    (args.out_dir / "bench_verify.json").write_text(
        json.dumps(summary, indent=1))

    print(f"[verify] sent {len(sent)}  received {len(got)}  exact {exact}  "
          f"missing {len(missing)}  corrupted {len(corrupted)}  dupes {dupes}")
    print(f"[verify] rebuilt {len(rebuilt):,} / {len(payload):,} B   "
          f"byte-exact: {sha_tx == sha_rx}   frames decoded: {frames}")
    if gaps:
        g = summary["gap_s"]
        print(f"[verify] spotter-clock gap mean {g['mean']} s (min {g['min']}"
              f", max {g['max']}, sd {g['stdev']}); span {span:.1f} s; "
              f"{summary['effective_payload_bytes_per_s']} payload B/s")
    print(f"[verify] VERDICT {summary['verdict']}")
    if missing[:10] or corrupted[:10]:
        print(f"[verify] missing {missing[:20]}  corrupted {corrupted[:20]}")
    sys.exit(0 if summary["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
