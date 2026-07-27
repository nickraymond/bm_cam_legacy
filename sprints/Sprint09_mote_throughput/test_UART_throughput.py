#!/usr/bin/env python3
"""UART / Spotter-queue throughput test for the BM camera link.

Speaks the real bm_serial COBS protocol by importing Nick's bm_serial.py
(place this script in the same directory). Two phases:

  Phase A (link integrity, no cellular quota used — writes to Spotter SD):
    python3 test_uart_throughput.py --phase log --count 200 --size 300 --gap-ms 0

  Phase B (real spotter_tx path, finds the pacing floor — uses cellular quota):
    python3 test_uart_throughput.py --phase tx --count 30 --size 900 \
        --sweep "5000,2000,1000,500,250" --network-type cellular_only

Verify Phase A by counting lines in uart_test.log on the Spotter SD card.
Verify Phase B by counting delivered messages per run-id in the Sofar backend.
"""

import argparse
import json
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial not installed: pip3 install pyserial")

try:
    from bm_serial import BristlemouthSerial
except ImportError:
    sys.exit("bm_serial.py must be in the same directory as this script")


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build_payload(seq: int, size: int, run_id: str) -> str:
    """Fixed-size ASCII payload: TST,<run>,<seq>,<pad>*<crc8hex>"""
    head = f"TST,{run_id},{seq:05d},"
    pad_len = max(0, size - len(head) - 3)  # 3 = '*' + 2 crc hex chars
    pad = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * (pad_len // 36 + 1))[:pad_len]
    body = head + pad
    return body + "*" + f"{crc8(body.encode()):02X}"


def run_burst(bm, phase, count, size, gap_ms, run_id, network_type):
    t0 = time.time()
    sent_bytes = 0
    for seq in range(count):
        payload = build_payload(seq, size, run_id)
        if phase == "log":
            bm.spotter_log("uart_test.log", payload)
        else:  # tx
            bm.spotter_tx(payload.encode(), network_type=network_type)
        sent_bytes += len(payload)
        if gap_ms:
            time.sleep(gap_ms / 1000.0)
    wall = time.time() - t0
    result = {
        "phase": phase,
        "run_id": run_id,
        "count": count,
        "payload_size": size,
        "gap_ms": gap_ms,
        "sent_bytes": sent_bytes,
        "wall_s": round(wall, 3),
        "effective_Bps": round(sent_bytes / wall) if wall > 0 else None,
    }
    print(json.dumps(result))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phase", choices=["log", "tx"], required=True,
                   help="log = spotter_log to SD (safe); tx = spotter_tx (cellular)")
    p.add_argument("--port", default="/dev/ttyAMA0")
    p.add_argument("--baud", type=int, default=115200,
                   help="must match the mote (serial_bridge is hardcoded 115200)")
    p.add_argument("--count", type=int, default=100, help="messages per burst")
    p.add_argument("--size", type=int, default=300, help="payload bytes per message")
    p.add_argument("--gap-ms", type=int, default=0)
    p.add_argument("--sweep", default=None,
                   help="comma list of gap-ms values, one burst per value")
    p.add_argument("--network-type", default="cellular_only",
                   help="tx phase only: cellular_only or legacy")
    p.add_argument("--drain-s", type=int, default=60,
                   help="pause between sweep bursts so the Spotter queue drains")
    p.add_argument("--run-id", default=None)
    args = p.parse_args()

    # B1 probes the 900-1200 B range ON PURPOSE (SPEC Phase B1): bm_core pins
    # the cellular cap at 1000 B and >=1000 is expected to fail — the probe
    # proves where the accept/reject boundary really is. Hard stop above 1200.
    if args.phase == "tx" and args.size > 1200:
        sys.exit("tx payloads capped at 1200 B for the B1 probe (SPEC Phase B1)")
    if args.phase == "log" and args.size > 1500:
        sys.exit("keep log payloads <= 1500 B")

    run_id = args.run_id or time.strftime("%H%M%S")
    uart = serial.Serial(args.port, args.baud, timeout=2)
    bm = BristlemouthSerial(uart=uart)
    net = args.network_type if args.phase == "tx" else None

    print(f"# phase={args.phase} port={args.port} baud={args.baud} "
          f"count={args.count} size={args.size} run_id={run_id}", file=sys.stderr)
    if args.phase == "log":
        print(f"# verify: uart_test.log on Spotter SD — expect {args.count} "
              f"lines tagged TST,{run_id}", file=sys.stderr)
    else:
        print(f"# verify: Sofar backend message count per run-id "
              f"(TST,{run_id}G<gap>)", file=sys.stderr)

    try:
        if args.sweep:
            gaps = [int(g) for g in args.sweep.split(",")]
            for i, gap in enumerate(gaps):
                burst_id = f"{run_id}G{gap}"
                print(f"# sweep {i + 1}/{len(gaps)}: gap={gap}ms (id {burst_id})",
                      file=sys.stderr)
                run_burst(bm, args.phase, args.count, args.size, gap, burst_id, net)
                if i < len(gaps) - 1:
                    print(f"# draining queue {args.drain_s}s...", file=sys.stderr)
                    time.sleep(args.drain_s)
        else:
            run_burst(bm, args.phase, args.count, args.size, args.gap_ms,
                      run_id, net)
    finally:
        bm.deinit()


if __name__ == "__main__":
    main()