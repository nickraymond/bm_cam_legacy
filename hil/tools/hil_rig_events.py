#!/usr/bin/env python3
"""hil_rig_events.py — turn Spotter console lines into a small rig event log for the dashboard timeline.

Events (one JSON object per line in <health-dir>/events.jsonl):
  bus_on / bus_off   the bridge's bus voltage crosses 5 V           {t, spot, kind}
  pi_on / pi_off     the bridge's bus current crosses 0.025 A (= the camera Pi up / halted)
  camtemp            decoded <END …> cpu_temp_c (+ stemp) or <WS … ct=…>   {t, spot, kind, cpu_c, sensor_c, src}
  charger            ChargerErrorState change                        {t, spot, kind, state}
  reset              rebootctl / charge mode / reset lines           {t, spot, kind, text}
Used two ways: imported by hil_rig_health.py (each 5-min run feeds the new console lines), and as a CLI to
BACKFILL the log from the console files once. Read-only on the console logs.

Example (backfill, on nereus000): python3 /home/pi/hil_health/hil_rig_events.py --backfill-days 3
Limits:  power lines arrive every ~10 s, so on/off edges are ±10 s; a camtemp needs the hex dump decoded, which
         only works for messages the Spotter actually printed (a queue-full drop is not printed).
"""

import argparse
import datetime
import json
import os
import re

UTC = datetime.timezone.utc
RE_PWR = re.compile(r"([0-9a-f]{16}), power \| .*voltage: ([-\d.]+), current: ([-\d.]+)")
RE_CHG = re.compile(r"ChargerErrorState changed from (\w+) to (\w+)")
RE_BAD = re.compile(r"rebootctl|Charge mode|CHARGE MODE|reset N\. Source", re.I)
RE_HEX = re.compile(r"^(\S+Z)\s+((?:[0-9a-f]{2} )+[0-9a-f]{2})\s*$")


class Scanner:
    """Stateful per-Spotter line scanner; `st` is a dict kept by the caller between calls."""

    def __init__(self, spot, bridge, st):
        self.spot, self.bridge, self.st = spot, bridge, st
        self.buf, self.hdr = [], None

    def _decoded(self, out):
        if not self.buf:
            return
        txt = bytes.fromhex("".join(self.buf).replace(" ", "")).decode("ascii", "replace")
        self.buf = []
        e = re.search(r"<END[^>]*>.*?\bcpu_temp_c:\s*([\d.]+)(?:.*?\bstemp:\s*([\d.]+))?", txt)
        if e:
            out.append({"t": self.hdr, "spot": self.spot, "kind": "camtemp", "cpu_c": float(e.group(1)),
                        "sensor_c": float(e.group(2)) if e.group(2) else None, "src": "END"})
            return
        w = re.search(r"<WS [^>]*?ct=([\d.]+)", txt)
        if w:
            out.append({"t": self.hdr, "spot": self.spot, "kind": "camtemp", "cpu_c": float(w.group(1)),
                        "sensor_c": None, "src": "WS"})

    def feed(self, lines):
        out = []
        for ln in lines:
            m = RE_HEX.match(ln)
            if m and self.hdr:
                self.buf.append(m.group(2)); continue
            self._decoded(out)
            self.hdr = ln[:20] if "[BM_TX]" in ln and "Message:" in ln else None
            t = ln[:20]
            if len(t) < 20 or t[19] != "Z":
                continue
            p = RE_PWR.search(ln)
            if p and p.group(1) == self.bridge:
                v, i = float(p.group(2)), float(p.group(3))
                for kind, on in (("bus", v > 5), ("pi", i > 0.025)):
                    prev = self.st.get(kind)
                    if prev != on:
                        if prev is not None or on:  # the very first reading only counts if it is "on"
                            out.append({"t": t, "spot": self.spot, "kind": f"{kind}_{'on' if on else 'off'}"})
                        self.st[kind] = on
                continue
            c = RE_CHG.search(ln)
            if c:
                out.append({"t": t, "spot": self.spot, "kind": "charger", "state": c.group(2)}); continue
            if RE_BAD.search(ln):
                out.append({"t": t, "spot": self.spot, "kind": "reset", "text": ln[21:140].strip()})
        self._decoded(out)
        return out


def backfill(health_dir, log_root, days):
    cfg = json.load(open(os.path.join(health_dir, "config.json")))
    now = datetime.datetime.now(UTC)
    evs = []
    for spot, sc in cfg["spotters"].items():
        sc_ = Scanner(spot, sc["bridge"], {})
        for k in range(days, -1, -1):
            p = os.path.join(log_root, spot, f"console_{now - datetime.timedelta(days=k):%Y%m%d}.log")
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    evs += sc_.feed(f.read().splitlines())
    evs.sort(key=lambda e: e["t"])
    path = os.path.join(health_dir, "events.jsonl")
    with open(path + ".tmp", "w") as f:
        for e in evs:
            f.write(json.dumps(e) + "\n")
    os.replace(path + ".tmp", path)
    return len(evs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--health-dir", default="/home/pi/hil_health")
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    ap.add_argument("--backfill-days", type=int, default=3)
    a = ap.parse_args()
    print(f"[events] backfilled {backfill(a.health_dir, a.log_root, a.backfill_days)} events")
