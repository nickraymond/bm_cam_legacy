#!/usr/bin/env python3
"""
bm_cmd_buffer_test_driver.py -- BENCH ONLY: time console `bm pub bmcam/cmd`
commands against the Spotter's bus power windows (mote cmdWaitMs test,
nereus_cam mote firmware, 2026-09-25).

PURPOSE
    Runs on the console monitor Pi (nereus000). Tails the Spotter console log
    written by spotter-monitor, detects `Bridge bus power: 1/0`, and publishes a
    `ping` with a unique id at fixed offsets from each edge, via the monitor's
    cmd.txt (never opens the serial port). The camera side logs arrival uptime
    (tools/bm_cmd_bench_listener.py), so every id gives one delivery latency.

    Default offsets per cycle:
      on+5    mote up, Pi not subscribed yet   -> expect delivery at ~cmdWaitMs
      on+30   Pi subscribed (~21 s), < 60 s    -> expect delivery at ~cmdWaitMs
      on+90   mote past cmdWaitMs              -> expect immediate pass-through
      off+20  bus unpowered                    -> held by Spotter? or lost?

INPUTS
    --spot SPOT-31593C   --cycles N   --id-base 926000   --offsets on:5,on:30,on:90,off:20

OUTPUTS
    --out CSV: id, scenario, edge, edge_utc, sent_utc, offset_s  (one row per pub)
    stdout: one line per edge and per pub.

EXAMPLE
    python3 -u bm_cmd_buffer_test_driver.py --spot SPOT-31593C --cycles 5 \
        --out /home/pi/spotter_logs/mote_cmd_buffer_test/driver.csv

LIMITATIONS
    Edge times are the monitor's 1 s-resolution log stamp. Send time is when the
    command was written to cmd.txt (the monitor forwards within ~0.5 s).
"""

import argparse
import csv
import os
import time
from datetime import datetime, timezone

ROOT = "/home/pi/spotter_logs"


def now_utc():
    return datetime.now(timezone.utc)


def send(cmd_path, line):
    while os.path.exists(cmd_path) and os.path.getsize(cmd_path) > 0:
        time.sleep(0.2)
    with open(cmd_path, "w") as fh:
        fh.write(line + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--spot", required=True)
    p.add_argument("--cycles", type=int, default=5)
    p.add_argument("--id-base", type=int, default=926000)
    p.add_argument("--offsets", default="on:5,on:30,on:90,off:20")
    p.add_argument("--command", default="ping")
    p.add_argument("--out", required=True)
    a = p.parse_args()

    offsets = [(e, float(s)) for e, s in (x.split(":") for x in a.offsets.split(","))]
    d = os.path.join(ROOT, a.spot)
    cmd_path = os.path.join(d, "cmd.txt")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    new = not os.path.exists(a.out)
    out = open(a.out, "a", newline="")
    w = csv.writer(out)
    if new:
        w.writerow(["id", "scenario", "edge", "edge_utc", "sent_utc", "offset_s"])

    log_path = os.path.join(d, "console_" + now_utc().strftime("%Y%m%d") + ".log")
    fh = open(log_path)
    fh.seek(0, 2)
    next_id, on_edges, pending = a.id_base, 0, []
    print(f"[DRV] tailing {log_path}; offsets={offsets}; cycles={a.cycles}", flush=True)
    while True:
        line = fh.readline()
        if line:
            edge = ("on" if "Bridge bus power: 1" in line
                    else "off" if "Bridge bus power: 0" in line else None)
            if edge:
                t = now_utc()
                if edge == "on":
                    on_edges += 1
                    if on_edges > a.cycles:
                        print(f"[DRV] {a.cycles} cycles done", flush=True)
                        break
                print(f"[DRV] edge {edge} #{on_edges} at {t.isoformat()}", flush=True)
                for e, s in offsets:
                    if e == edge and on_edges >= 1:
                        pending.append((t.timestamp() + s, e, s, t))
                pending.sort()
        else:
            time.sleep(0.1)
        while pending and time.time() >= pending[0][0]:
            _, e, s, t = pending.pop(0)
            cid = next_id
            next_id += 1
            send(cmd_path, f'bm pub bmcam/cmd {{"id":{cid},"c":"{a.command}"}} 1 1')
            sent = now_utc()
            w.writerow([cid, f"{e}+{s:g}", e, t.isoformat(), sent.isoformat(), s])
            out.flush()
            print(f"[DRV] sent id={cid} {e}+{s:g} at {sent.isoformat()}", flush=True)
    out.close()


if __name__ == "__main__":
    main()
