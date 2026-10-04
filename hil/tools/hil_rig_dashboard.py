#!/usr/bin/env python3
"""hil_rig_dashboard.py — one read-only status page for the outdoor HIL rig (Nick 2026-10-03: the holistic view).

Runs ON nereus000 right after each hil_rig_health.py run (systemd ExecStartPost, every 5 min) and writes a static
HTML page; a separate `python3 -m http.server` serves only that folder on the LAN. Read-only: it reads files and
local device listings, never sends a console command, never writes outside its output folder, no outside services.

Shows: overall level + reasons (alerts.json); nereus000 CPU temp/throttle and LiFePO4 VIN/VBAT/charging; per
Spotter: USB console (by-id → tty → hub port, log freshness), charger state, battery V/W + input V + humidity
(backend telemetry, via the health state), bus V while on, BM nodes heard on its bus (console power lines + the
last "Bridge network config"), camera last wake + last <WS> CPU temp; lsusb; the last alerts; the last runs.
Inputs:  --health-dir /home/pi/hil_health, --log-root /home/pi/spotter_logs, --out <health-dir>/www/index.html
Example: python3 /home/pi/hil_health/hil_rig_dashboard.py
Limits:  as fresh as the last health run (5 min) and the console log; node names come from the last bridge network
         config the console printed (may be hours old); no history plots in v1.
"""

import argparse
import datetime
import glob
import html
import json
import os
import re
import socket
import subprocess

UTC = datetime.timezone.utc
RE_PWR = re.compile(r"^(\S+Z) .*?([0-9a-f]{16}), power \| .*voltage: ([-\d.]+), current: ([-\d.]+)")
RE_NET = re.compile(r"Bridge network config: (\[.*\])\s*$")


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return ""


def tail_bytes(path, n):
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - n))
        return f.read().decode("utf-8", "replace").splitlines()


def last_network_config(log_root, spot):
    """The newest 'Bridge network config' line across the Spotter's console files (newest file first)."""
    for p in sorted(glob.glob(os.path.join(log_root, spot, "console_*.log")), reverse=True)[:3]:
        out = sh(["bash", "-c", f"grep -a 'Bridge network config' {p} | tail -1"]).strip()
        m = RE_NET.search(out)
        if m:
            try:
                return out[:20], json.loads(m.group(1))
            except ValueError:
                pass
    return None, []


def esc(x):
    return html.escape("" if x is None else str(x))


def badge(level):
    return f'<span class="b {esc(level)}">{esc(str(level).upper())}</span>'


def age_txt(ts, now):
    if not ts:
        return "—"
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        s = int((now - t).total_seconds())
        return f"{s // 60} min ago" if s >= 60 else f"{s} s ago"
    except ValueError:
        return esc(ts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--health-dir", default="/home/pi/hil_health")
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    out = a.out or os.path.join(a.health_dir, "www", "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    now = datetime.datetime.now(UTC)
    al = json.load(open(os.path.join(a.health_dir, "alerts.json")))
    st = json.load(open(os.path.join(a.health_dir, "state.json")))
    cfg = json.load(open(os.path.join(a.health_dir, "config.json")))
    R = al.get("readings", {})
    alerts_log = tail_bytes(os.path.join(a.health_dir, "ALERTS.log"), 20000)[-12:]
    # USB
    byid = {os.path.basename(p): os.path.realpath(p) for p in glob.glob("/dev/serial/by-id/*")}
    bypath = {os.path.realpath(p): os.path.basename(p) for p in glob.glob("/dev/serial/by-path/*usb-0:*")}
    usb = {}
    for name, dev in byid.items():
        m = re.search(r"(SPOT-\w+)", name)
        port = re.search(r"usb-0:([\d.]+)", bypath.get(dev, ""))
        usb[m.group(1) if m else name] = (dev, port.group(1) if port else "?", name)
    lsusb = sh(["lsusb"]).strip().splitlines()

    spot_cards = []
    for spot, sc in cfg["spotters"].items():
        ss = st.get("spot", {}).get(spot, {})
        p = os.path.join(a.log_root, spot, f"console_{now:%Y%m%d}.log")
        fresh = int(now.timestamp() - os.path.getmtime(p)) if os.path.exists(p) else None
        nodes = {}
        for ln in tail_bytes(p, 1_500_000):
            m = RE_PWR.search(ln)
            if m:
                nodes[m.group(2)] = (m.group(1), float(m.group(3)), float(m.group(4)))
        net_at, net = last_network_config(a.log_root, spot)
        names = {}
        for n in net:
            try:
                names[f"{int(n[0]):016x}"] = n[1]
            except (ValueError, TypeError, IndexError):
                pass
        dev, port, _ = usb.get(spot, (None, None, None))
        rows = "".join(
            f"<tr><td><code>{esc(nid)}</code></td><td>{esc(names.get(nid) or ('bridge' if nid == sc['bridge'] else 'camera mote' if nid == sc['mote'] else '?'))}</td>"
            f"<td>{v:.2f} V</td><td>{i * 1000:.1f} mA</td><td>{esc(t[11:19])}Z</td></tr>"
            for nid, (t, v, i) in sorted(nodes.items()))
        b = ss.get("batt") or {}
        chg = ss.get("charger", "?")
        chg_cls = "ok" if chg == "OK" else "crit"
        spot_cards.append(f"""
<section class="card"><h2>{esc(spot)} <small>{esc(sc['host'])}</small></h2>
<table class="kv">
<tr><th>USB console</th><td>{('<span class="ok">' + esc(dev) + '</span> · hub port ' + esc(port)) if dev else '<span class="crit">NOT PLUGGED</span>'} · log {('<span class="' + ('ok' if fresh < 90 else 'crit') + '">' + str(fresh) + ' s old</span>') if fresh is not None else '—'}</td></tr>
<tr><th>Charger</th><td><span class="{chg_cls}">{esc(chg)}</span> {('since ' + esc((ss.get('charger_at') or '')[11:19]) + 'Z') if chg != 'OK' else ''}</td></tr>
<tr><th>Battery</th><td>{esc(b.get('v'))} V · {esc(b.get('w'))} W · input {esc(b.get('vin'))} V · RH {esc(b.get('rh'))} % <small>(Sofar, {age_txt(b.get('at'), now)})</small></td></tr>
<tr><th>BM bus (on)</th><td>{esc(ss.get('bus_v_on'))} V <small>({age_txt(ss.get('bus_v_at'), now)})</small></td></tr>
<tr><th>Camera wake</th><td>{esc(ss.get('last_wake'))}</td></tr>
<tr><th>Camera CPU</th><td>{esc(ss.get('ws_ct'))} °C <small>(boot-time &lt;WS&gt;, {esc((ss.get('ws_at') or '')[11:19])}Z)</small></td></tr>
</table>
<h3>BM nodes heard on this bus <small>(console power lines; names from the bridge config of {esc((net_at or '?')[:16])})</small></h3>
<table class="grid"><tr><th>node</th><th>role</th><th>V</th><th>I</th><th>last</th></tr>{rows or '<tr><td colspan=5>none in the last log window</td></tr>'}</table>
</section>""")

    lvl = al.get("level", "?")
    reasons = "".join(f"<li>{esc(r)}</li>" for r in al.get("reasons", [])) or "<li>all checks OK</li>"
    thr = R.get("n000_throttled")
    ext = R.get("lp_external_power")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<title>HIL Rig Status</title>
<style>
:root{{--bg:#f6f7f9;--fg:#1d2330;--card:#fff;--mute:#667085;--line:#e3e6eb;--ok:#18794e;--warn:#a35200;--crit:#c4221a}}
@media (prefers-color-scheme: dark){{:root{{--bg:#12151b;--fg:#e6e9ef;--card:#1b2029;--mute:#98a2b3;--line:#2b3240;--ok:#4cc38a;--warn:#f0a44b;--crit:#ff6b62}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:0 0 8px}} h3{{font-size:13px;margin:12px 0 6px;color:var(--mute)}}
small{{color:var(--mute);font-weight:normal}} code{{font-size:12px}}
.wrap{{max-width:1100px;margin:0 auto}} .row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin-top:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%}} td,th{{text-align:left;padding:3px 6px;vertical-align:top;border-bottom:1px solid var(--line)}}
.kv th{{width:120px;color:var(--mute);font-weight:500}} .grid th{{color:var(--mute);font-weight:500}}
.ok{{color:var(--ok)}} .warn{{color:var(--warn)}} .crit{{color:var(--crit);font-weight:600}}
.b{{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:700;color:#fff}} .b.ok{{background:var(--ok)}} .b.warn{{background:var(--warn)}} .b.crit{{background:var(--crit)}}
pre{{white-space:pre-wrap;font-size:12px;margin:0}}
</style></head><body><div class="wrap">
<h1>HIL rig — {esc(socket.gethostname())} {badge(lvl)}</h1>
<div><small>health run {esc(al.get('ts'))} ({age_txt(al.get('ts'), now)}) · page built {now:%Y-%m-%d %H:%M:%S}Z · refreshes every 60 s · read-only</small></div>
<section class="card" style="margin-top:12px"><h2>Alerts now</h2><ul>{reasons}</ul></section>
<div class="row">
<section class="card"><h2>nereus000 <small>console monitor</small></h2><table class="kv">
<tr><th>CPU</th><td>{esc(R.get('n000_temp_c'))} °C · throttled <span class="{'ok' if thr in ('0x0', None) else 'crit'}">{esc(thr)}</span></td></tr>
<tr><th>Power</th><td><span class="{'ok' if ext else 'crit'}">{'external (charging)' if ext else 'ON BATTERY'}</span> · VIN {esc(R.get('lp_vin_mv'))} mV (threshold {esc(R.get('lp_vin_threshold_mv'))})</td></tr>
<tr><th>LiFePO4</th><td>VBAT {esc(R.get('lp_vbat_mv'))} mV · {esc(R.get('lp_soc_est'))} · Pi draw {esc(R.get('lp_iout_ma'))} mA</td></tr>
<tr><th>Uptime</th><td>{esc(sh(['uptime', '-p']).strip())}</td></tr>
</table>
<h3>USB devices</h3><pre>{esc(chr(10).join(lsusb))}</pre></section>
{''.join(spot_cards)}
</div>
<section class="card" style="margin-top:12px"><h2>Recent alerts <small>ALERTS.log</small></h2><pre>{esc(chr(10).join(reversed(alerts_log))) or 'none'}</pre></section>
</div></body></html>"""
    tmp = out + ".tmp"
    open(tmp, "w").write(page)
    os.replace(tmp, out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
