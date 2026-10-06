#!/usr/bin/env python3
"""hil_t1_burst.py — T1 (hil/gates/T1_burst_queue_bmcam003.md): synthetic, timed bursts through the real Pi → Spotter
path (bm_serial COBS, spotter/transmit-data, cellular-only), run ON a bench Pi with the bus held on and the capture
cycle stopped.

Payload: the Sprint09 format `TST,<run>,<seq>,<A–Z0–9 pad>*<crc8>` (sprints/Sprint09_mote_throughput/
test_UART_throughput.py build_payload), 384 B: no `<START`/`<I…>`/`<CF`/`:`/`=` → the backend creates no media,
chunks, command answers or heals (checked against staging bm_image_parser.py, 2026-10-06).
Inputs:  --plan CSV (start_utc,arm,shape[,count]) — shape `steady` (one message every --gap-s) or `pair` (2 messages
         0.1 s apart, then a pause so the average rate equals steady); --app (deployed BM_Devel_Pi, for bm_serial.py);
         --count 120, --size 384, --gap-s 1.3, --out CSV of every send (run, arm, shape, seq, utc, rc)
Outputs: --out CSV; one summary line per burst on stdout (sent, wall s). Accept / reject is read from the Spotter
         console afterwards (nereus000 capture), not here: the Spotter does not answer the Pi per message.
Example: python3 /tmp/hil_t1_burst.py --plan /tmp/t1_plan.csv --out /home/pi/t1/sends.csv
Limits:  host-guarded (bmcam003/bmcam004); waits for each start time (a start already >60 s in the past is skipped
         and logged); the UART port comes from the unit's camera_schedule.yaml (bm_serial.load_uart_config).
"""
import argparse
import csv
import datetime as dt
import os
import socket
import sys
import time

ALLOWED = {"bmcam003", "bmcam004"}


def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build_payload(seq, size, run_id):
    head = f"TST,{run_id},{seq:05d},"
    pad_len = max(0, size - len(head) - 3)
    pad = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * (pad_len // 36 + 1))[:pad_len]
    body = head + pad
    return body + "*" + f"{crc8(body.encode()):02X}"


def utc(ts=None):
    return dt.datetime.fromtimestamp(ts if ts is not None else time.time(), dt.timezone.utc)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--app", default="/home/pi/BM_Devel_Pi")
    ap.add_argument("--count", type=int, default=120)
    ap.add_argument("--size", type=int, default=384)
    ap.add_argument("--gap-s", type=float, default=1.3)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if socket.gethostname() not in ALLOWED:
        raise SystemExit(f"[hil-guard] REFUSED: {socket.gethostname()} is not a bench unit")
    sys.path.insert(0, a.app)
    os.chdir(a.app)                                   # bm_serial reads camera_schedule.yaml relative to the app
    from bm_serial import BristlemouthSerial          # noqa: E402
    bm = BristlemouthSerial(network_type="cellular_only")
    plan = list(csv.DictReader(open(a.plan)))
    new = not os.path.exists(a.out)
    out = open(a.out, "a", newline="")
    w = csv.writer(out)
    if new:
        w.writerow(["run", "arm", "shape", "seq", "utc", "rc"])
    print(f"[t1] host={socket.gethostname()} bursts={len(plan)} count={a.count} size={a.size} gap={a.gap_s}s", flush=True)
    for i, p in enumerate(plan):
        start = dt.datetime.fromisoformat(p["start_utc"].replace("Z", "+00:00")).timestamp()
        count = int(p.get("count") or a.count)
        run = f"t1{p['arm'].replace('-', '')[:6]}{i:02d}"[:12].lower()
        if time.time() > start + 60:
            print(f"[t1] SKIP {run} {p['arm']}: start {p['start_utc']} already passed", flush=True)
            continue
        while time.time() < start:
            time.sleep(min(1.0, start - time.time()))
        t0 = time.time()
        for seq in range(count):
            if p["shape"] == "pair":
                due = t0 + (seq // 2) * 2 * a.gap_s + (seq % 2) * 0.1
            else:
                due = t0 + seq * a.gap_s
            while time.time() < due:
                time.sleep(min(0.05, due - time.time()))
            rc = "ok"
            try:
                bm.spotter_tx(build_payload(seq, a.size, run).encode())
            except Exception as exc:                  # log and carry on: a send error is data for T1
                rc = f"err:{type(exc).__name__}"
            w.writerow([run, p["arm"], p["shape"], seq, utc().isoformat(timespec="milliseconds"), rc])
        out.flush()
        print(f"[t1] {run} {p['arm']} {p['shape']} start={utc(t0).isoformat(timespec='seconds')} "
              f"sent={count} wall={time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
