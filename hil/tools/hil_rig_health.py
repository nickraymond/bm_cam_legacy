#!/usr/bin/env python3
"""hil_rig_health.py — rig health monitor for the outdoor bench (Nick 2026-10-03: heat + nereus000 battery).

Runs ON nereus000 from a systemd timer every 5 min, independent of any Claude session. Read-only on everything:
it reads nereus000's own sensors, the LiFePO4wered/Pi+ registers (lifepo4wered-cli get), the Spotter console logs
that spotter-monitor already writes, and (optional) camera temps over ssh when a key is configured. It never sends a
console command, never writes to a unit, never sends data off the rig.

Checks per run:
  nereus000  CPU temp + get_throttled; LiFePO4 VIN / VBAT / VOUT / IOUT; external power = VIN >= VIN_THRESHOLD;
             SoC estimated from VBAT (no SoC register on the Pi+); VBAT trend vs the last runs.
  Spotters   battery V / power / input V / humidity every 15 min from our backend (Sofar latest-data, hourly
             samples; no battery temperature exists there); new console lines since the last run: ChargerErrorState (current state), rebootctl, charge mode,
             BusVErrorState, the bridge bus voltage (while on) and current (Pi on = > 0.025 A).
  cameras    CPU temp at wake: the `<WS … ct=…>` heartbeat decoded from the console (boot-time value), plus
             `vcgencmd measure_temp/get_throttled` over ssh at window+3..+7 min IF --cam-ssh-key is set.
  wakes      at window+9..+14 min: did the bridge's bus current exceed 0.025 A in the window (= Pi up, the
             hil_wake_report.sh rule)? No → CRIT "missed wake". Bus voltage is checked only while the bus is on.
Outputs (dir --out, default /home/pi/hil_health):
  health.csv     one row per run (all readings + level)
  alerts.json    {"ts", "level": ok|warn|crit, "reasons": [...], "readings": {...}} — the file the EM polls
  ALERTS.log     one line per WARN/CRIT state change (and every CRIT run); also journald `logger -p user.crit`
  summary.log    one line per hour
  state.json     internal (byte offsets, ChargerErrorState, VBAT history, last wake checks)
Inputs:  --config JSON (spotters: {SPOT: {bridge, mote, host, window_minute}}; window_minute = the minute the bus window opens), --out, --log-root, --cam-ssh-key
Example: python3 /home/pi/hil_health/hil_rig_health.py --config /home/pi/hil_health/config.json
Limits:  no pack temperature (the Pi+ has no sensor); SoC is a VBAT estimate (LiFePO4 is flat 3.2–3.35 V);
         Spotter battery V/SoC/temp are not printed passively on the console (`sensors` would need a console
         command: Nick's OK). WS ct= is read ~5 s after boot, the coolest point of a cycle.
"""

import argparse
import csv
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_rig_events import Scanner  # noqa: E402  (same folder on nereus000)

UTC = datetime.timezone.utc
TH = {  # proposed thresholds (EM / Nick to confirm)
    "pi_warn_c": 70.0, "pi_crit_c": 80.0,
    "vin_margin_warn_mv": 100,          # VIN within this of VIN_THRESHOLD = sagging supply
    "vbat_crit_mv": 3100, "vbat_low_on_ext_warn_mv": 3250,  # level, not a drop (hourly top-up dips to ~3290)
    "bus_v_lo": 23.0, "bus_v_hi": 24.6,
    "pi_on_a": 0.025,
    "charger_fault_crit_min": 15,
    "spot_batt_crit_v": 3.7, "spot_batt_drop_warn_v": 0.05, "spot_batt_discharge_warn_w": 0.5,  # Li-ion; 4.10 V ~ full       # a Spotter charger fault longer than this = CRIT (shorter = WARN)
}
THROTTLE_NOW = {0: "under-voltage", 1: "freq-capped", 2: "throttled", 3: "soft-temp-limit"}
THROTTLE_SINCE = {16: "under-voltage", 17: "freq-capped", 18: "throttled", 19: "soft-temp-limit"}


def now():
    return datetime.datetime.now(UTC)


def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception as e:  # noqa: BLE001 - a sensor read must never kill the monitor
        return f"ERR {type(e).__name__}"


def pi_temp_throttle(prefix=None):
    """(temp C, throttled int) locally, or over ssh when prefix is an ssh argv list."""
    pre = prefix or []
    t = run(pre + ["vcgencmd", "measure_temp"], 15)
    th = run(pre + ["vcgencmd", "get_throttled"], 15)
    m, n = re.search(r"temp=([\d.]+)", t), re.search(r"throttled=(0x[0-9a-fA-F]+)", th)
    return (float(m.group(1)) if m else None), (int(n.group(1), 16) if n else None)


def throttle_text(v):
    if v is None:
        return "?"
    now_b = [n for b, n in THROTTLE_NOW.items() if v >> b & 1]
    since = [n for b, n in THROTTLE_SINCE.items() if v >> b & 1]
    return ("now:" + "+".join(now_b) if now_b else "") + (" since-boot:" + "+".join(since) if since else "") or "0x0"


def lifepo4():
    out = run(["lifepo4wered-cli", "get"], 10)
    regs = dict(re.findall(r"^(\w+) = (-?\d+)$", out, re.M))
    g = lambda k: int(regs[k]) if k in regs else None
    return {"vin_mv": g("VIN"), "vbat_mv": g("VBAT"), "vout_mv": g("VOUT"), "iout_ma": g("IOUT"),
            "vin_threshold_mv": g("VIN_THRESHOLD"), "vbat_shdn_mv": g("VBAT_SHDN")}


def soc_estimate(vbat_mv, on_ext):
    """Rough LiFePO4 resting-voltage SoC. While charging the voltage reads high: report 'charging'."""
    if vbat_mv is None:
        return ""
    if on_ext and vbat_mv >= 3380:
        return "charging/full"
    for v, s in ((3400, 100), (3350, 95), (3320, 85), (3300, 70), (3270, 50), (3250, 35), (3220, 25),
                 (3200, 18), (3150, 10), (3100, 7), (3000, 3)):
        if vbat_mv >= v:
            return f"~{s}%"
    return "~0%"


def spotter_battery(api, env_file, spot):
    """Latest Sofar `status` row from our backend (/systems/{spot}/spotter-telemetry): battery V/W, input V,
    humidity. Read-only, token from the rig's env file. No battery temperature exists in that feed."""
    if not api:
        return None
    try:
        import urllib.request
        tok = next(l.split("=", 1)[1].strip().strip("'\"") for l in open(env_file) if l.startswith("ADMIN_TOKEN="))
        req = urllib.request.Request(f"{api}/systems/{spot}/spotter-telemetry?hours=3&limit=200",
                                     headers={"Authorization": f"Bearer {tok}"})
        rows = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("rows") or []
        st_ = [r for r in rows if r.get("stream") == "status"]
        if not st_:
            return None
        r = max(st_, key=lambda r: r.get("timestamp_utc") or "")
        m = r.get("metrics") or r.get("data") or {}
        return {"at": r.get("timestamp_utc"), "v": m.get("battery_voltage_v"), "w": m.get("battery_power_w"),
                "vin": m.get("solar_voltage_v"), "rh": m.get("humidity_pct")}
    except Exception as e:  # noqa: BLE001
        return {"err": f"{type(e).__name__}"}


def charger_timeline(log_root, spot, t):
    """ChargerErrorState episodes from the last two console files: [(start, end|None, state)], and hours per UTC
    day spent in a non-OK state (THERMAL_FAULT etc.). Passive: the console line only, no charger current exists."""
    ev = []
    for d in (t - datetime.timedelta(days=1), t):
        p = os.path.join(log_root, spot, f"console_{d:%Y%m%d}.log")
        if os.path.exists(p):
            out = run(["bash", "-c", f"grep -a 'ChargerErrorState changed' {p}"], 30)
            for ln in out.splitlines():
                m = RE_CHG.search(ln)
                if m:
                    ev.append((ln[:20], m.group(1), m.group(2)))
    eps, cur = [], None
    for ts_, frm, to in ev:
        if cur and cur[2] != "OK":
            eps.append((cur[0], ts_, cur[2]))
        cur = (ts_, None, to)
    if cur and cur[2] != "OK":
        eps.append((cur[0], None, cur[2]))
    hours = {}
    P = lambda x: datetime.datetime.fromisoformat(x.replace("Z", "+00:00"))
    for a_, b_, stt in eps:
        a2, b2 = P(a_), (P(b_) if b_ else t)
        while a2 < b2:
            day_end = (a2 + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seg = min(b2, day_end) - a2
            hours[f"{a2:%Y-%m-%d}"] = hours.get(f"{a2:%Y-%m-%d}", 0) + seg.total_seconds() / 3600
            a2 = day_end
    return eps, {k: round(v, 2) for k, v in hours.items()}


RE_PWR = re.compile(r"([0-9a-f]{16}), power \| .*voltage: ([-\d.]+), current: ([-\d.]+)")
RE_CHG = re.compile(r"ChargerErrorState changed from (\w+) to (\w+)")
RE_BUSV = re.compile(r"BusVErrorState changed from (\w+) to (\w+)")
RE_BUSOFF = re.compile(r"Bridge bus power: 0")


def bus_level(samples, off_ts, edge_s=11):
    """Bus voltage while ON, ignoring the bus-off ramp-down. samples = [(iso_ts, volts)] for the bridge; off_ts = [iso_ts] of
    'Bridge bus power: 0' lines. A sample > 5 V followed within edge_s (one 10 s sample interval) by a < 5 V sample or a bus-off line is the
    ramp-down edge (13.5 / 16.97 / 7.55 V WARNs at :10:30, 2026-10-08/09), not the bus level. Returns the last level or None."""
    def ts(x):
        return datetime.datetime.fromisoformat(x.replace("Z", "+00:00"))
    lows = [ts(t) for t, v in samples if v < 5] + [ts(t) for t in off_ts]
    level = None
    for t, v in samples:
        if v > 5 and not any(0 <= (lo - ts(t)).total_seconds() <= edge_s for lo in lows):
            level = v
    return level
RE_BAD = re.compile(r"rebootctl|Charge mode|CHARGE MODE|reset N\. Source", re.I)


def read_new_console(path, offset):
    """Lines appended since `offset` (bytes). A new day's file starts at 0."""
    if not os.path.exists(path):
        return [], offset
    size = os.path.getsize(path)
    if offset > size:
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    return data.decode("utf-8", "replace").splitlines(), offset + len(data)


def ws_temps(lines):
    """Decode hex-dumped BM_TX messages just enough to find <WS … ct=…> heartbeats."""
    out, buf, hdr_t = [], [], None
    for ln in lines + ["#end"]:
        m = re.match(r"^(\S+Z)\s+((?:[0-9a-f]{2} )+[0-9a-f]{2})\s*$", ln)
        if m and hdr_t:
            buf.append(m.group(2)); continue
        if buf:
            txt = bytes.fromhex("".join(buf).replace(" ", "")).decode("ascii", "replace")
            w = re.search(r"<WS [^>]*?ct=([\d.]+)[^>]*?hn=(\w+)", txt)
            if w:
                out.append((hdr_t, float(w.group(1)), "WS"))
            # END carries `cpu_temp_c: 36.5, … stemp: 29` (key: value) on every transmitting wake; bm #122 moves the
            # cpu read to after the burst (peak). stemp = camera sensor temperature.
            e = re.search(r"<END[^>]*>.*?\bcpu_temp_c:\s*([\d.]+)(?:.*?\bstemp:\s*([\d.]+))?", txt)
            if e:
                out.append((hdr_t, float(e.group(1)), "END" + (f" stemp {e.group(2)}" if e.group(2) else "")))
            buf = []
        hdr_t = ln[:20] if "[BM_TX]" in ln and "Message:" in ln else None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default="/home/pi/hil_health/config.json")
    ap.add_argument("--out", default="/home/pi/hil_health")
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    ap.add_argument("--cam-ssh-key", default="")
    ap.add_argument("--api", default="https://nereus-vision-staging.onrender.com")
    ap.add_argument("--env-file", default="/home/pi/.config/nereus/heal_driver.env")
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    os.makedirs(a.out, exist_ok=True)
    st_path = os.path.join(a.out, "state.json")
    st = json.load(open(st_path)) if os.path.exists(st_path) else {}
    t = now()
    R, warn, crit = {"ts": t.isoformat(timespec="seconds")}, [], []

    # --- nereus000 ---
    temp, thr = pi_temp_throttle()
    R.update(n000_temp_c=temp, n000_throttled=hex(thr) if thr is not None else "")
    if temp is not None and temp >= TH["pi_crit_c"]: crit.append(f"nereus000 CPU {temp} C")
    elif temp is not None and temp >= TH["pi_warn_c"]: warn.append(f"nereus000 CPU {temp} C")
    if thr and thr & 0xF: crit.append(f"nereus000 throttled {throttle_text(thr)}")
    elif thr and thr & 0xF0000: warn.append(f"nereus000 {throttle_text(thr)}")

    lp = lifepo4(); R.update({f"lp_{k}": v for k, v in lp.items()})
    on_ext = lp["vin_mv"] is not None and lp["vin_threshold_mv"] is not None and lp["vin_mv"] >= lp["vin_threshold_mv"]
    R["lp_external_power"] = on_ext
    R["lp_soc_est"] = soc_estimate(lp["vbat_mv"], on_ext)
    hist = [h for h in st.get("vbat_hist", []) if (t - datetime.datetime.fromisoformat(h[0])).total_seconds() < 2400]
    if lp["vbat_mv"] is not None:
        hist.append([t.isoformat(), lp["vbat_mv"]])
    st["vbat_hist"] = hist
    if lp["vin_mv"] is None:
        crit.append("LiFePO4 not readable (lifepo4wered-cli)")
    elif not on_ext:
        since = st.setdefault("on_battery_since", t.isoformat())
        crit.append(f"nereus000 ON BATTERY (no external power) since {since[11:16]}Z: VIN {lp['vin_mv']} mV, VBAT {lp['vbat_mv']} mV {R['lp_soc_est']}")
    else:
        st.pop("on_battery_since", None)
        if lp["vin_mv"] < lp["vin_threshold_mv"] + TH["vin_margin_warn_mv"]:
            warn.append(f"nereus000 supply sagging: VIN {lp['vin_mv']} mV (threshold {lp['vin_threshold_mv']})")
        # Measured 2026-10-03: every bus window (:00-:10) the pack tops up the input (VBAT 3636 -> ~3290-3420 mV
        # while VIN is present) and recharges after; a relative-drop rule fired every hour. Use a level instead.
        if lp["vbat_mv"] is not None and lp["vbat_mv"] < TH["vbat_low_on_ext_warn_mv"]:
            warn.append(f"nereus000 VBAT {lp['vbat_mv']} mV on external power (input not covering the load)")
    # TEMPORARY rules while a bench bus is held on (config "bus_on_watch": true; EM/Nick 2026-10-05). The known hourly
    # :00-:12 top-up dip + ~15 min recovery (:00-:27) is excluded from the drop and SoC rules.
    # quiet = outside the :00-:12 window AND its ~15 min recovery (measured 10/5: 3408 mV at :15, 3620+ by :25)
    if cfg.get("bus_on_watch") and lp["vbat_mv"] is not None and on_ext and not (0 <= t.minute <= 27):
        quiet = [h for h in hist if not (0 <= datetime.datetime.fromisoformat(h[0]).minute <= 27)]
        old_q = [v for ts_, v in quiet if (t - datetime.datetime.fromisoformat(ts_)).total_seconds() >= 1500]
        if old_q and old_q[0] - lp["vbat_mv"] > 30:
            warn.append(f"[bus-on watch] nereus000 VBAT down {old_q[0] - lp['vbat_mv']} mV in 30 min on external ({old_q[0]} -> {lp['vbat_mv']})")
        soc = R["lp_soc_est"]
        low = soc.startswith("~") and int(soc[1:-1]) < 50
        st["soc_low_runs"] = (st.get("soc_low_runs", 0) + 1) if low else 0
        if st["soc_low_runs"] >= 2:
            crit.append(f"[bus-on watch] nereus000 SoC estimate {soc} (< 50 %, 2 runs)")
    if cfg.get("bus_on_watch") and lp["vbat_mv"] is not None and lp["vbat_mv"] < 3250:
        crit.append(f"[bus-on watch] nereus000 VBAT {lp['vbat_mv']} mV (< 3250)")
    if lp["vbat_mv"] is not None and lp["vbat_mv"] < TH["vbat_crit_mv"]:
        crit.append(f"nereus000 VBAT {lp['vbat_mv']} mV (< {TH['vbat_crit_mv']})")

    # --- Spotters (console) ---
    offs = st.setdefault("offsets", {})
    for spot, sc in cfg["spotters"].items():
        p = os.path.join(a.log_root, spot, f"console_{t:%Y%m%d}.log")
        key = f"{spot}:{os.path.basename(p)}"
        if key not in offs:  # first run on this file: look back ~10 min only
            offs[key] = max(0, (os.path.getsize(p) if os.path.exists(p) else 0) - 3_000_000)
        lines, offs[key] = read_new_console(p, offs[key])
        new_ev = Scanner(spot, sc["bridge"], st.setdefault("ev_state", {}).setdefault(spot, {})).feed(lines)
        if new_ev:
            with open(os.path.join(a.out, "events.jsonl"), "a") as f:
                for e in new_ev:
                    f.write(json.dumps(e) + "\n")
        ss = st.setdefault("spot", {}).setdefault(spot, {"charger": "OK"})
        if not ss.get("charger_seeded"):  # first run: the charger state may have changed hours ago
            last = run(["bash", "-c", f"grep -a 'ChargerErrorState changed' {p} | tail -1"], 30)
            m = RE_CHG.search(last)
            if m:
                ss["charger"], ss["charger_at"] = m.group(2), last[:20]
            ss["charger_seeded"] = True
        mote_i, bus_v, bus_pts, bus_off_ts = [], None, [], []  # NB: `offs` = the console-offset dict above
        for ln in lines:
            if RE_BUSOFF.search(ln):
                bus_off_ts.append(ln[:20])
            m = RE_CHG.search(ln)
            if m:
                ss["charger"], ss["charger_at"] = m.group(2), ln[:20]
            m = RE_BUSV.search(ln)
            if m:
                ss["busv"], ss["busv_at"] = m.group(2), ln[:20]
            if RE_BAD.search(ln):
                ss["last_bad"] = ln[:160]; crit.append(f"{spot}: {ln[21:120].strip()}")
            m = RE_PWR.search(ln)
            if m and m.group(1) == sc["bridge"]:
                # the bridge reports the bus it powers: V ~24 when on, ~0 when off; I > 0.025 A = the Pi is up
                v, i = float(m.group(2)), float(m.group(3))
                mote_i.append((ln[:20], i))
                bus_pts.append((ln[:20], v))
        bus_v = bus_level(bus_pts, bus_off_ts)
        for ts_, i in mote_i:  # remember Pi-on evidence per hour for the wake check
            if i > TH["pi_on_a"]:
                ss.setdefault("pi_on_hours", [])
                hh = ts_[:13]
                if hh not in ss["pi_on_hours"]:
                    ss["pi_on_hours"] = (ss["pi_on_hours"] + [hh])[-48:]
        for ts_, ct, src in ws_temps(lines):
            ss["ws_ct"], ss["ws_at"], ss["ws_src"] = ct, ts_, src
        if ss.get("tl_at") is None or (t - datetime.datetime.fromisoformat(ss["tl_at"])).total_seconds() >= 600:
            eps, hrs = charger_timeline(a.log_root, spot, t)
            # merge brief flaps (< 60 s apart) only for display; the hours count every second of fault
            ss["charger_episodes"] = eps[-30:]
            ss["charger_fault_h"] = hrs
            ss["tl_at"] = t.isoformat()
        R[f"{spot}_charger_fault_h_today"] = (ss.get("charger_fault_h") or {}).get(f"{t:%Y-%m-%d}", 0)
        R[f"{spot}_charger"] = ss["charger"]
        if bus_v is not None:
            ss["bus_v_on"], ss["bus_v_at"] = bus_v, t.isoformat(timespec="seconds")
        R[f"{spot}_bus_v_on"] = ss.get("bus_v_on")
        R[f"{spot}_cam_ws_ct"] = ss.get("ws_ct")
        R[f"{spot}_cam_ws_at"] = ss.get("ws_at")
        if ss["charger"] != "OK":  # standing rule (EM/Nick 2026-10-03): charger fault > 15 min = CRIT
            at = ss.get("charger_at") or ""
            age = (t - datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() / 60 if at else 999
            msg = f"{spot} charger {ss['charger']} since {at[11:19]}Z ({age:.0f} min)"
            (crit if age > TH["charger_fault_crit_min"] else warn).append(msg)
        elif ss.get("charger_at") and ss.get("charger_was_fault"):
            warn.append(f"{spot} charger back to OK at {ss['charger_at'][11:19]}Z")
            ss["charger_was_fault"] = False
        if ss["charger"] != "OK":
            ss["charger_was_fault"] = True
        if bus_v is not None and not (TH["bus_v_lo"] <= bus_v <= TH["bus_v_hi"]):
            warn.append(f"{spot} bus {bus_v:.2f} V outside {TH['bus_v_lo']}-{TH['bus_v_hi']}")
        if ss.get("batt_poll") is None or (t - datetime.datetime.fromisoformat(ss["batt_poll"])).total_seconds() >= 900:
            b = spotter_battery(a.api, a.env_file, spot)
            ss["batt_poll"] = t.isoformat()
            if b and "err" not in b:
                ss["batt"] = b
                ss.setdefault("batt_ref_v", b["v"])  # the reference for the "dropped > 5 %" rule
        b = ss.get("batt") or {}
        R[f"{spot}_batt_v"], R[f"{spot}_batt_w"], R[f"{spot}_in_v"], R[f"{spot}_rh"] = b.get("v"), b.get("w"), b.get("vin"), b.get("rh")
        R[f"{spot}_batt_at"] = b.get("at")
        if b.get("v") is not None:
            ref = ss.get("batt_ref_v") or b["v"]
            if b["v"] < TH["spot_batt_crit_v"]:
                crit.append(f"{spot} battery {b['v']} V")
            elif ref - b["v"] >= TH["spot_batt_drop_warn_v"]:
                warn.append(f"{spot} battery dropped {ref} -> {b['v']} V")
            if cfg.get("bus_on_watch") and spot in cfg.get("bus_on_spots", []) and b.get("w") is not None and b["w"] < -0.5:
                warn.append(f"[bus-on watch] {spot} battery discharging {b['w']} W")
            if b.get("w") is not None and b["w"] <= -TH["spot_batt_discharge_warn_w"]:
                warn.append(f"{spot} battery discharging {b['w']} W")
        ct = ss.get("ws_ct")
        if ct is not None and ct >= TH["pi_crit_c"]: crit.append(f"{sc['host']} CPU {ct} C (WS)")
        elif ct is not None and ct >= TH["pi_warn_c"]: warn.append(f"{sc['host']} CPU {ct} C (WS)")

        # --- camera over ssh (optional) and wake check ---
        wm = int(sc.get("window_minute", 0))
        mins = (t.minute - wm) % 60
        if a.cam_ssh_key and 3 <= mins <= 7 and ss.get("ssh_hour") != t.strftime("%Y-%m-%dT%H"):
            pre = ["ssh", "-i", a.cam_ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                   "-o", "StrictHostKeyChecking=accept-new", f"pi@{sc['host']}"]
            ctemp, cthr = pi_temp_throttle(pre)
            ss["ssh_hour"] = t.strftime("%Y-%m-%dT%H")
            ss["cam_temp"], ss["cam_thr"] = ctemp, (hex(cthr) if cthr is not None else None)
            if ctemp is not None and ctemp >= TH["pi_crit_c"]: crit.append(f"{sc['host']} CPU {ctemp} C")
            elif ctemp is not None and ctemp >= TH["pi_warn_c"]: warn.append(f"{sc['host']} CPU {ctemp} C")
            if cthr and cthr & 0xF: crit.append(f"{sc['host']} throttled {throttle_text(cthr)}")
        R[f"{spot}_cam_temp_c"] = ss.get("cam_temp")
        R[f"{spot}_cam_throttled"] = ss.get("cam_thr")
        iv = int(sc.get("interval_min", 60)) * 60  # bridge sampleIntervalMs / 60000 (UTC-aligned windows)
        epoch = int(t.timestamp())
        win_start = datetime.datetime.fromtimestamp(epoch - (epoch - wm * 60) % iv, UTC)
        if 9 <= (t - win_start).total_seconds() / 60 < 15 and ss.get("wake_checked") != win_start.isoformat():
            ss["wake_checked"] = win_start.isoformat()
            hh = win_start.strftime("%Y-%m-%dT%H")
            woke = hh in ss.get("pi_on_hours", [])  # Pi-on evidence is kept per UTC hour (window starts inside it)
            ss["last_wake"] = f"{hh}:{wm:02d} {'ok' if woke else 'MISSED'}"
            if not woke:
                crit.append(f"{sc['host']} did not wake at {hh[11:]}:{wm:02d}Z (no bus current on {spot})")
        R[f"{spot}_last_wake"] = ss.get("last_wake")

    level = "crit" if crit else ("warn" if warn else "ok")
    R["level"] = level
    reasons = crit + warn
    alert = {"ts": R["ts"], "level": level, "reasons": reasons, "readings": R}
    with open(os.path.join(a.out, "metrics.jsonl"), "a") as f:  # flexible-schema history for the timeline
        f.write(json.dumps(dict(R, reasons=reasons)) + "\n")
    tmp = os.path.join(a.out, "alerts.json.tmp")
    json.dump(alert, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(a.out, "alerts.json"))
    csv_path = os.path.join(a.out, "health.csv")
    new = not os.path.exists(csv_path)
    cols = st.get("cols") or sorted(R.keys())
    st["cols"] = cols
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(cols + ["reasons"])
        w.writerow([R.get(c, "") for c in cols] + [" | ".join(reasons)])
    sig = level + "|" + "|".join(sorted(r.split(" since ")[0] for r in reasons))
    if level != "ok" and (sig != st.get("last_sig") or level == "crit"):
        line = f"{R['ts']} RIG-HEALTH {level.upper()}: " + "; ".join(reasons)
        open(os.path.join(a.out, "ALERTS.log"), "a").write(line + "\n")
        run(["logger", "-p", f"user.{'crit' if level == 'crit' else 'warning'}", "-t", "RIG-HEALTH", line], 5)
    elif level == "ok" and st.get("last_level") not in (None, "ok"):
        open(os.path.join(a.out, "ALERTS.log"), "a").write(f"{R['ts']} RIG-HEALTH OK (cleared)\n")
    st["last_sig"], st["last_level"] = sig, level
    if st.get("summary_hour") != t.strftime("%Y-%m-%dT%H") and t.minute >= 55:
        st["summary_hour"] = t.strftime("%Y-%m-%dT%H")
        s = (f"{R['ts']} {level.upper()} n000 {temp}C {throttle_text(thr)} | LiFePO4 ext={on_ext} VIN {lp['vin_mv']} "
             f"VBAT {lp['vbat_mv']} {R['lp_soc_est']} IOUT {lp['iout_ma']}mA | " +
             " | ".join(f"{s_} chg={R.get(s_+'_charger')} bus={R.get(s_+'_bus_v')} ws_ct={R.get(s_+'_cam_ws_ct')} "
                        f"ssh_ct={R.get(s_+'_cam_temp_c')} wake={R.get(s_+'_last_wake')}" for s_ in cfg["spotters"]))
        open(os.path.join(a.out, "summary.log"), "a").write(s + "\n")
    json.dump(st, open(st_path, "w"))
    print(json.dumps(alert))
    return 0


if __name__ == "__main__":
    sys.exit(main())
