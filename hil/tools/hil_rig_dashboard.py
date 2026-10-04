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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hil_rig_timeline  # noqa: E402  (same folder on nereus000)

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


def merged_episodes(eps, gap_s=120):
    """Merge fault episodes that restart within gap_s (charger flapping) for display: (start, end, state, flaps)."""
    P = lambda x: datetime.datetime.fromisoformat(x.replace("Z", "+00:00"))
    out = []
    for a, b, stt in eps:
        if out and out[-1][1] and (P(a) - P(out[-1][1])).total_seconds() <= gap_s:
            out[-1] = (out[-1][0], b, out[-1][2], out[-1][3] + 1)
        else:
            out.append((a, b, stt, 1))
    return out


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
<tr><th>Camera CPU</th><td>{(esc(ss.get('ws_ct')) + ' °C <small>(' + (('&lt;END&gt;' + ((', sensor ' + esc(ss['ws_src'].split('stemp ')[1]) + ' °C') if 'stemp ' in ss['ws_src'] else '')) if (ss.get('ws_src') or '').startswith('END') else 'boot-time &lt;WS&gt;') + ', ' + esc((ss.get('ws_at') or '')[11:19]) + 'Z)</small>') if ss.get('ws_ct') is not None else '— <small>(no &lt;WS&gt; heartbeat decoded yet)</small>'}</td></tr>
</table>
<h3>Charger thermal timeline <small>(console ChargerErrorState; flaps &lt; 2 min merged; hours per UTC day)</small></h3>
<div>fault hours: {', '.join(f"{esc(d)} <b>{h:.2f} h</b>" for d, h in sorted((ss.get('charger_fault_h') or {}).items())) or 'none in the last 2 days'}</div>
<table class="grid"><tr><th>state</th><th>start</th><th>end</th><th>duration</th><th>flaps</th></tr>{''.join(f"<tr><td class='crit'>{esc(st_)}</td><td>{esc(a_[5:16].replace('T',' '))}Z</td><td>{(esc(b_[5:16].replace('T',' ')) + 'Z') if b_ else '<b>ongoing</b>'}</td><td>{int(((datetime.datetime.fromisoformat((b_ or now.isoformat()).replace('Z','+00:00')) - datetime.datetime.fromisoformat(a_.replace('Z','+00:00'))).total_seconds()) // 60)} min</td><td>{n_}</td></tr>" for a_, b_, st_, n_ in reversed(merged_episodes(ss.get('charger_episodes') or [])[-8:])) or '<tr><td colspan=5>no charger faults in the last 2 days</td></tr>'}</table>
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
:root{{--surface:#fcfcfb;--m-cpu:#eb6834;--m-sensor:#4a3aa7;--m-batt:#1baf7a;--m-volt:#2a78d6;--m-wake:#c3c2b7;--m-pion:#52514e;--st-warning:#fab219;--st-serious:#ec835a;--st-critical:#d03b3b;--gridl:#e1e0d9;--axisc:#898781}}
@media (prefers-color-scheme: dark){{:root{{--bg:#0d0d0d;--fg:#e6e9ef;--card:#1a1a19;--mute:#98a2b3;--line:#2c2c2a;--ok:#4cc38a;--warn:#f0a44b;--crit:#ff6b62;--surface:#1a1a19;--m-cpu:#d95926;--m-sensor:#9085e9;--m-batt:#199e70;--m-volt:#3987e5;--m-wake:#383835;--m-pion:#c3c2b7;--gridl:#2c2c2a}}}}
.tl text{{font:11px system-ui,sans-serif;fill:var(--axisc)}} .tl .lanet{{font-weight:600;fill:var(--fg);font-size:12px}} .tl .trk{{fill:var(--mute)}}
.tl .lane{{fill:none;stroke:var(--line)}} .tl .grid{{stroke:var(--gridl);stroke-width:1}} .tl .bandt{{fill:var(--st-serious);font-size:10px}}
.tl .xmark{{stroke:var(--st-critical);stroke-width:2.5;fill:none}} .tl .ann{{stroke:var(--fg);stroke-dasharray:3 3;stroke-width:1}} .tl .annt{{fill:var(--fg);font-size:10px}}
.tl .none{{font-style:italic}} .tl .xhair{{stroke:var(--fg);stroke-width:1;opacity:.5}} .tl .xhairt{{fill:var(--fg)}}
.rng button{{font:inherit;padding:3px 10px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}} .rng button.on{{background:var(--fg);color:var(--card)}}
.lg{{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--mute);margin:6px 0}} .lg i{{display:inline-block;width:14px;height:4px;border-radius:2px;margin-right:5px;vertical-align:middle}}
details>summary{{cursor:pointer;color:var(--mute);margin:14px 0 4px}}
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
<section class="card" style="margin-top:12px"><h2 style="display:flex;justify-content:space-between;align-items:center">Rig timeline <span class="rng"><button data-r="6">6 h</button> <button data-r="24">24 h</button> <button data-r="72">3 d</button></span></h2>
<div class="lg"><span><i style="background:var(--m-cpu)"></i>CPU temp (nereus000, cameras)</span><span><i style="background:var(--m-sensor)"></i>camera image-sensor temp</span><span><i style="background:var(--m-batt)"></i>battery V (LiFePO4, Spotter)</span><span><i style="background:var(--m-volt)"></i>supply / BM bus V</span><span><i style="background:var(--m-wake)"></i>bus window</span><span><i style="background:var(--m-pion)"></i>camera Pi up</span><span><i style="background:var(--st-serious);opacity:.5"></i>charger thermal fault</span><span><i style="background:var(--st-warning)"></i>WARN</span><span><i style="background:var(--st-critical)"></i>CRIT / ✕ Spotter reset or charge mode</span><span>┊ rig event</span></div>
{''.join(f'<div class="tlw" data-r="{h}"' + ('' if h == 24 else ' style="display:none"') + '>' + f'{hil_rig_timeline.build(a.health_dir, h)}</div>' for h in (6, 24, 72))}
<small>Hover any mark for its value and time. Each track has its own scale (min/max at left); colours mean the same metric in every lane. Times UTC (PDT = UTC − 7).</small>
</section>
<details><summary>Details: tables (USB, Spotters, BM nodes, charger timeline, recent alerts)</summary>
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
</details>
</div>
<script>
(function(){{
  var pick=24; try{{pick=+localStorage.getItem('tlr')||24}}catch(e){{}}
  function show(r){{document.querySelectorAll('.tlw').forEach(function(d){{d.style.display=(+d.dataset.r===r)?'':'none'}});
    document.querySelectorAll('.rng button').forEach(function(b){{b.classList.toggle('on',+b.dataset.r===r)}});
    try{{localStorage.setItem('tlr',r)}}catch(e){{}}}}
  document.querySelectorAll('.rng button').forEach(function(b){{b.onclick=function(){{show(+b.dataset.r)}}}});
  show([6,24,72].indexOf(pick)>=0?pick:24);
  document.querySelectorAll('svg.tl').forEach(function(svg){{
    var l=svg.querySelector('.xhair'),t=svg.querySelector('.xhairt'),t0=+svg.dataset.t0,t1=+svg.dataset.t1,ml=+svg.dataset.ml,mr=+svg.dataset.mr,w=+svg.dataset.w;
    svg.addEventListener('mousemove',function(ev){{var r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)*w/r.width;
      if(x<ml||x>w-mr){{l.style.display=t.style.display='none';return}}
      var ts=new Date((t0+(x-ml)/(w-ml-mr)*(t1-t0))*1000);l.setAttribute('x1',x);l.setAttribute('x2',x);l.style.display='';
      t.setAttribute('x',x+4);t.textContent=ts.toISOString().slice(5,16).replace('T',' ')+'Z';t.style.display=''}});
    svg.addEventListener('mouseleave',function(){{l.style.display=t.style.display='none'}});
  }});
}})();
</script>
</body></html>"""
    tmp = out + ".tmp"
    open(tmp, "w").write(page)
    os.replace(tmp, out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
