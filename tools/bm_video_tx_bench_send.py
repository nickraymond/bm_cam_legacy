#!/usr/bin/env python3
"""
bm_video_tx_bench_send.py -- Pi-side bench sender for framed video messages.

PURPOSE
    Step 3 of the video-transmit test ladder (2026-09-19): push the EXACT wire
    lines produced by tools/bm_video_tx_loopback.py (wire/<variant>.txt) from
    the Pi, over the real UART and Bristlemouth bus, to the Spotter -- WITHOUT
    touching cellular -- so the Mac-side USB console log can be diffed against
    what was sent.

    Transport = bm_serial.spotter_print() (topic spotter/printf): one line on
    the Spotter USB console, never enters the transmit queue, zero cellular
    quota (see its docstring; bench-verified on Spotter v2.16.6, 2026-08-01).

    This is NOT the production transmit path (spotter/transmit-data). It proves
    Pi -> UART -> BM bus -> Spotter integrity and pacing for video-sized lines.
    Queue / Notecard / cellular behaviour is step 4.

RUN ON THE PI, from the deployed runtime dir (imports the unit's bm_serial.py):
    cd ~/BM_Devel_Pi && python3 /tmp/bm_video_tx_bench_send.py \
        --wire /tmp/h264_1key.txt --delay 1.0 [--limit 5] --out /tmp/send_log.csv

PRECONDITION
    Nothing else may own the UART. The cron runtime (rc_progressive_jpeg.py)
    holds /dev/ttyAMA0 -- stop it first and restart it afterwards. This script
    refuses to run if it sees that process.

OUTPUTS
    --out CSV: idx, utc, wire_len, bytes_written, sha256_16 of the line
    stdout: progress line per message, summary, nonzero exit on any failure

KNOWN LIMITATIONS
    * spotter/printf line-length limit is not documented in this repo; the
      --limit probe exists to find out before sending a whole clip.
    * One-way: no ack from the Spotter. Delivery is judged from the console log.
"""

import argparse
import csv
import hashlib
import subprocess
import sys
import time
from datetime import datetime, timezone


def runtime_is_running():
    res = subprocess.run(["pgrep", "-f", "rc_progressive_jpeg.py"],
                         stdout=subprocess.PIPE, text=True)
    return bool(res.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wire", required=True)
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between messages (production: 1.0)")
    ap.add_argument("--limit", type=int, default=0,
                    help="send only the first N lines (0 = all)")
    ap.add_argument("--tag", default="VTX",
                    help="marker lines printed before/after the burst")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if runtime_is_running():
        print("REFUSING: rc_progressive_jpeg.py is running and owns the UART.")
        sys.exit(2)

    with open(args.wire, "rb") as fh:
        lines = [l.rstrip(b"\n").decode("ascii") for l in fh if l.strip()]
    if args.limit:
        lines = lines[:args.limit]
    print(f"[send] wire={args.wire} lines={len(lines)} delay={args.delay}s "
          f"longest={max(map(len, lines))} chars")

    sys.path.insert(0, ".")
    from bm_serial import BristlemouthSerial           # the unit's own copy
    bm = BristlemouthSerial()
    print(f"[send] uart={bm.uart.port} @ {bm.uart.baudrate}")

    rows, t0 = [], time.time()
    bm.spotter_print(f"{args.tag} BEGIN n={len(lines)}")
    time.sleep(args.delay)
    for i, line in enumerate(lines):
        n = bm.spotter_print(line)
        bm.uart.flush()
        utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        rows.append([i, utc + "Z", len(line), n,
                     hashlib.sha256(line.encode()).hexdigest()[:16]])
        if i % 10 == 0 or i == len(lines) - 1:
            print(f"[send] {i + 1:4d}/{len(lines)}  {utc}Z  wrote {n} B",
                  flush=True)
        time.sleep(args.delay)
    bm.spotter_print(f"{args.tag} END sent={len(rows)}")
    bm.uart.flush()
    dur = time.time() - t0

    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["idx", "utc", "wire_len", "bytes_written", "sha256_16"])
        w.writerows(rows)
    print(f"[send] done: {len(rows)} msgs in {dur:.1f} s -> {args.out}")
    if len(rows) != len(lines):
        sys.exit(3)


if __name__ == "__main__":
    main()
