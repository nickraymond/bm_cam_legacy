#!/usr/bin/env python3
# filename: test_queue_drain.py
# description: Sprint10 Phase E — cellular queue drain / sync-blackout characterization.
"""
Sprint10 Phase E — characterize the Spotter cellular queue drain.

WHY THIS EXISTS
Sprint09 measured UART *throughput* with small bursts (30 msgs) and locked
384 chars / 1.0 s. The 07-27/28 soak then found that image-scale bursts
(~190 msgs) lose a single CONSECUTIVE RUN of messages ~140-150 s into the
transmit, ~6-7 s long — a Notecard sync session blacking out the Spotter's
2-slot cellular queue. Slower pacing shrinks the number of messages caught
in that fixed window but never reached zero at 1.0/1.25/1.5 s.

This harness sends fixed-size, sequence-numbered messages at a given pace
and records the EXACT send time of every message, so backend arrivals can
be joined back to answer:
  - is the blackout time-triggered or count-triggered? (run the same count
    at different delays: time-triggered => same wall-clock onset, different
    index; count-triggered => same index)
  - how long is it, how often does it fire, does it repeat within a burst?
  - what is the sustained drain rate, and which (delay, count) pairs
    deliver 100 %?

RUNS ON THE PI (imports bm_serial, opens the UART). Run it on the camera
Pi with the RC cron DISARMED and power_halt OFF — see PHASE_E.md.

WIRE FORMAT (matches Sprint09's harness so the backend decode is known):
    TST,<burst_id>,<seq:05d>,<pad>*<crc8hex>
`burst_id` encodes the run: <run>C<count>D<delay_ms> e.g. `A1C200D1500`.

OUTPUTS (per burst, written next to the script unless --out-dir given):
    sendlog_<burst_id>.jsonl   one line per message: seq, utc, t_offset_s
    manifest_<run>.json        matrix, config, per-burst wall times

EXAMPLES
    # single burst: 200 messages at 1.5 s
    python3 test_queue_drain.py --count 200 --delay-ms 1500 --run A1

    # mechanism discriminator (Phase E step 1): same count, 3 delays
    python3 test_queue_drain.py --matrix "200@1000,200@3000,200@4000" --run DISC

    # full matrix (Phase E step 2) — counts x delays, ~2.5-3 h with drains
    python3 test_queue_drain.py --run FULL \
      --matrix "100@1000,200@1000,300@1000,100@1500,200@1500,300@1500,\
100@2000,200@2000,300@2000,100@3000,200@3000,300@3000,300@4000,300@5000"

SAFETY
  - `--dry-run` prints the plan and sends nothing.
  - Messages go out cellular-only (network_type 0x02), same path as image
    chunks. Cellular messages are free on this account (Nick, 2026-07-27).
  - `--drain-s` (default 300) between bursts is LOAD-BEARING: it lets the
    Notecard finish syncing so each burst starts from a comparable state.
    Do not trim it below 180 s without saying so in the run notes.
"""

import argparse
import json
import os
import sys
import time

# Hardware imports are LAZY so `--dry-run` works anywhere (plan review on
# a laptop); the real run still fails loudly if they are missing.
def _hw_imports():
    try:
        import serial
    except ImportError:
        sys.exit("pyserial not installed: sudo apt install python3-serial")
    try:
        from bm_serial import BristlemouthSerial
    except ImportError:
        sys.exit("bm_serial.py must be importable "
                 "(run from /home/pi/BM_Devel_Pi)")
    return serial, BristlemouthSerial

DEFAULT_SIZE = 384  # production image-chunk size — do not vary in Phase E


def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build_payload(seq, size, burst_id):
    """Fixed-size ASCII payload, Sprint09-compatible so the backend
    decode/regex is already known: TST,<burst>,<seq>,<pad>*<crc8>."""
    head = f"TST,{burst_id},{seq:05d},"
    pad_len = max(0, size - len(head) - 3)
    pad = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * (pad_len // 36 + 1))[:pad_len]
    body = head + pad
    return body + "*" + f"{crc8(body.encode()):02X}"


def parse_matrix(spec):
    """'200@1500,300@2000' -> [(200, 1500), (300, 2000)]"""
    out = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        count, delay = item.split("@")
        out.append((int(count), int(delay)))
    return out


def run_burst(bm, count, delay_ms, burst_id, size, out_dir, network_type):
    """Send one burst, logging the exact send time of every message."""
    path = os.path.join(out_dir, f"sendlog_{burst_id}.jsonl")
    t0 = time.time()
    mono0 = time.monotonic()
    with open(path, "w", encoding="ascii") as log:
        for seq in range(count):
            payload = build_payload(seq, size, burst_id)
            send_mono = time.monotonic()
            bm.spotter_tx(payload.encode(), network_type=network_type)
            log.write(json.dumps({
                "seq": seq,
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "t_offset_s": round(send_mono - mono0, 3),
            }) + "\n")
            log.flush()  # survive a power cut mid-burst
            if delay_ms:
                # Pace from the slot start so send time doesn't drift.
                slack = (delay_ms / 1000.0) - (time.monotonic() - send_mono)
                if slack > 0:
                    time.sleep(slack)
    wall = time.time() - t0
    rec = {
        "burst_id": burst_id, "count": count, "delay_ms": delay_ms,
        "size": size, "wall_s": round(wall, 1),
        "msgs_per_min": round(count / (wall / 60.0), 1) if wall else None,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "sendlog": path,
    }
    print(json.dumps(rec), flush=True)
    return rec


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--run", required=True, help="short run tag, e.g. DISC or FULL")
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--delay-ms", type=int, default=None)
    p.add_argument("--matrix", default=None,
                   help="comma list of count@delay_ms, one burst each")
    p.add_argument("--size", type=int, default=DEFAULT_SIZE)
    p.add_argument("--drain-s", type=int, default=300,
                   help="pause between bursts (>=180; load-bearing)")
    p.add_argument("--port", default="/dev/ttyAMA0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--network-type", default="cellular_only")
    p.add_argument("--out-dir", default=".")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    if args.matrix:
        bursts = parse_matrix(args.matrix)
    elif args.count and args.delay_ms is not None:
        bursts = [(args.count, args.delay_ms)]
    else:
        p.error("give --matrix, or both --count and --delay-ms")
    if args.size != DEFAULT_SIZE:
        print(f"[WARN] size {args.size} != production {DEFAULT_SIZE}; Phase E "
              f"holds size fixed so delay is the only variable.")

    os.makedirs(args.out_dir, exist_ok=True)
    total_msgs = sum(c for c, _ in bursts)
    total_s = sum(c * d / 1000.0 for c, d in bursts) + \
        args.drain_s * (len(bursts) - 1)
    print(f"# plan: {len(bursts)} burst(s), {total_msgs} messages, "
          f"~{total_s / 60:.0f} min incl. {args.drain_s}s drains")
    for c, d in bursts:
        print(f"#   {args.run}C{c}D{d}: {c} msgs @ {d} ms "
              f"= {c * d / 1000.0 / 60:.1f} min")
    if args.dry_run:
        print("# dry-run: nothing sent.")
        return 0

    serial, BristlemouthSerial = _hw_imports()
    uart = serial.Serial(args.port, args.baud, timeout=2)
    bm = BristlemouthSerial(uart=uart)
    manifest = {
        "run": args.run, "size": args.size, "drain_s": args.drain_s,
        "network_type": args.network_type,
        "host": os.uname().nodename,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bursts": [],
    }
    man_path = os.path.join(args.out_dir, f"manifest_{args.run}.json")
    try:
        for i, (count, delay_ms) in enumerate(bursts):
            burst_id = f"{args.run}C{count}D{delay_ms}"
            print(f"# burst {i + 1}/{len(bursts)}: {burst_id}", flush=True)
            manifest["bursts"].append(
                run_burst(bm, count, delay_ms, burst_id, args.size,
                          args.out_dir, args.network_type))
            with open(man_path, "w", encoding="ascii") as f:
                json.dump(manifest, f, indent=2)  # rewrite after each burst
            if i < len(bursts) - 1:
                print(f"# draining {args.drain_s}s...", flush=True)
                time.sleep(args.drain_s)
    finally:
        bm.deinit()
    print(f"# done. manifest: {man_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
