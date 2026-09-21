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

CELLULAR MODE (--cellular) -- Sprint22 1d staging proof. SPENDS CELLULAR QUOTA.
    Sends every wire line through bm_serial.spotter_tx (topic
    spotter/transmit-data = the production path: Spotter transmit queue ->
    Notecard -> cellular -> Sofar API). Guards, all mandatory:
      * --approve-msgs N must equal the number of lines exactly, and N <= 300
        (Nick's standing approval). A typo'd wire file cannot overspend.
      * network type is forced to CELLULAR-ONLY (0x02) on every message. The
        bare BristlemouthSerial() default is 0x01 = cellular WITH IRIDIUM
        FALLBACK -- never let a test burst fall back to satellite.
      * --start-after-boundary S waits until S seconds past a 5-minute wall
        clock boundary, and refuses if the burst would not END before the next
        one. Measured 2026-09-21: 7 consecutive chunks sent 05:35:01-05:35:08Z
        were lost (blackout-lane signature); a 129-msg clip fits in one lane.
    The BEGIN/END tag lines still go via spotter_print (console only, free).

    cd ~/BM_Devel_Pi && python3 /tmp/bm_video_tx_bench_send.py --cellular \
        --wire /tmp/wire_complete.txt --approve-msgs 129 \
        --start-after-boundary 15 --tag S22GOLD --out /tmp/send_log.csv

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


CELLULAR_MSG_CEILING = 300     # Nick's standing approval (2026-09-19)
LANE_SECONDS = 300             # cellular blackout lanes sit on 5-min boundaries
LANE_TAIL_MARGIN_S = 20        # END must land this long before the next one


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
    ap.add_argument("--cellular", action="store_true",
                    help="SPENDS CELLULAR QUOTA: send via spotter_tx "
                         "(production path), forced cellular-only")
    ap.add_argument("--approve-msgs", type=int, default=None,
                    help="cellular mode: must equal the line count exactly")
    ap.add_argument("--start-after-boundary", type=float, default=None,
                    metavar="S", help="start S seconds after a 5-minute wall "
                                      "clock boundary; refuse if the burst "
                                      "would cross the next one")
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

    if args.cellular:
        if args.limit:
            sys.exit("REFUSING: --limit with --cellular would send a broken "
                     "group over cellular. Probe with the default printf mode.")
        if args.approve_msgs != len(lines):
            sys.exit(f"REFUSING: --approve-msgs {args.approve_msgs} != "
                     f"{len(lines)} lines in {args.wire}")
        if len(lines) > CELLULAR_MSG_CEILING:
            sys.exit(f"REFUSING: {len(lines)} msgs exceeds the "
                     f"{CELLULAR_MSG_CEILING}-message approval")
    burst_s = len(lines) * args.delay
    if args.start_after_boundary is not None:
        room = LANE_SECONDS - args.start_after_boundary - LANE_TAIL_MARGIN_S
        if burst_s > room:
            sys.exit(f"REFUSING: burst {burst_s:.0f} s does not fit the "
                     f"{room:.0f} s left in a {LANE_SECONDS} s lane")

    sys.path.insert(0, ".")
    from bm_serial import BristlemouthSerial           # the unit's own copy
    bm = BristlemouthSerial()
    print(f"[send] uart={bm.uart.port} @ {bm.uart.baudrate}  mode="
          f"{'CELLULAR spotter_tx cellular_only' if args.cellular else 'printf (zero cellular)'}")

    def send(line):
        if args.cellular:
            # Production lines end in \n. network_type is explicit on EVERY
            # call: never inherit the 0x01 Iridium-fallback default.
            return bm.spotter_tx((line + "\n").encode("ascii"),
                                 network_type="cellular_only")
        return bm.spotter_print(line)

    if args.start_after_boundary is not None:
        wait = (args.start_after_boundary - time.time() % LANE_SECONDS) % LANE_SECONDS
        print(f"[send] waiting {wait:.0f} s for {args.start_after_boundary:g} s "
              f"past the next 5-minute boundary", flush=True)
        time.sleep(wait)

    rows, t0 = [], time.time()
    bm.spotter_print(f"{args.tag} BEGIN n={len(lines)}")
    time.sleep(args.delay)
    for i, line in enumerate(lines):
        n = send(line)
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
