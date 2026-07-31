#!/usr/bin/env python3
# filename: sd_bridge_ab_coplot.py
# description: A/B camera-unit power coplot + Wh/cycle from Spotter SD bridge addr-65 traces.
"""
Compare two camera units' power draw on the 24 V BM bus from Spotter SD-card
dumps, and report energy per cycle.

WHICH SENSOR
Each Spotter's SD card logs its bridge node under bm/<bridge-node>/*_power.log.
The bridge's `addr: 65` trace reads the downstream camera-unit load at 10 s
cadence and drops to ~0 W in every bus-off window (see
tools/bridge_energy_per_cycle.py D9 rationale — the SD card is the
authoritative source; this tool is the SD-side equivalent of that console
tool, extended to render the comparison HTML used in
runs/sprint10_overnight_20260729/energy_coplot.html).

Known bridge nodes (2026-07 bench):
    0e582dd12c1e1480  SPOT-31593C  hosts bmcam000
    c3c564b91856226c  SPOT-33507C  hosts bmcam003

WHAT "PER CYCLE" MEANS
A cycle is one bus-ON window: contiguous run of power above --on-w (default
0.05 W; halted-Pi baseline reads ~0.42 W, bus-off reads ~0.0002 W), tolerating
sample gaps up to --gap-s. Only COMPLETE windows (off on both sides inside the
analysis window) count toward Wh/cycle. Energy is trapezoid-integrated over
the raw 10 s samples. The halted-Pi baseline is charged to the cycle
deliberately — it is what the battery actually pays.

The two units may run different duty cycles (e.g. 20/10 vs 15/15), so the
summary reports cycle period and Wh/h alongside Wh/cycle.

Inputs:
  --unit LABEL:BRIDGE_DIR:COLOR   (exactly twice; first unit is the
                                   "saves" row numerator in the table)
  --hours N                       analysis window = last N h of common coverage
  --out-dir DIR                   run folder for all artifacts

Outputs (in --out-dir):
  ab_coplot_data.json   60 s mean series + stats (tzoff -7, PDT display)
  ab_cycles.csv         per-cycle: unit, start/end UTC, duration s, Wh
  ab_summary.json       per-unit stats + savings + window + sources
  ab_coplot.html        self-contained canvas coplot (zoom/pan/tooltip)

Example:
  python3 tools/sd_bridge_ab_coplot.py \
      --unit "bmcam003:.../bmcam003/bm/c3c564b91856226c:#2563eb" \
      --unit "bmcam000:.../bmcam000/bm/0e582dd12c1e1480:#dc2626" \
      --hours 10 --out-dir runs/power_review_20260730

Limitations: assumes PDT (UTC-7) display; assumes addr-65 is the camera load
(true for the bench bridge nodes above — re-verify chain order per the
nereus-spotter-sd-analysis skill if the topology changes).
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

LINE_RE = re.compile(
    r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z.*addr:\s*65,\s*"
    r"voltage:\s*([\d.\-]+),\s*current:\s*([\d.\-]+)"
)

TZOFF_H = -7  # display timezone: PDT


def isoms(s):
    return int(
        datetime.strptime(s[:23], "%Y-%m-%dT%H:%M:%S.%f")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def load_bridge(bridge_dir):
    """All addr-65 samples in bridge_dir/*_power.log -> sorted [(ms, W)]."""
    files = sorted(glob.glob(os.path.join(bridge_dir, "*_power.log")))
    if not files:
        sys.exit(f"ERROR: no *_power.log in {bridge_dir}")
    pts = {}
    for f in files:
        with open(f, errors="replace") as fh:
            for line in fh:
                m = LINE_RE.match(line)
                if m:
                    pts[isoms(m.group(1))] = abs(
                        float(m.group(2)) * float(m.group(3))
                    )
    if not pts:
        sys.exit(f"ERROR: no addr-65 samples parsed from {bridge_dir}")
    print(f"  {bridge_dir}: {len(files)} files, {len(pts)} addr-65 samples, "
          f"{utc(min(pts))} .. {utc(max(pts))}")
    return sorted(pts.items())


def minute_means(samples, t0, t1):
    """60 s mean series [[ms, W]] over [t0, t1); None (null) for empty bins."""
    bins = {}
    for t, w in samples:
        if t0 <= t < t1:
            b = t - (t - t0) % 60000
            bins.setdefault(b, []).append(w)
    out = []
    b = t0
    while b < t1:
        v = bins.get(b)
        out.append([b, round(sum(v) / len(v), 4) if v else None])
        b += 60000
    return out


def find_cycles(samples, t0, t1, on_w, gap_ms):
    """Complete bus-ON windows inside [t0,t1] -> [{start,end,dur_s,wh}].

    A window is complete only if bounded by below-threshold samples (or a
    real gap start/end never counts — a window truncated by the analysis
    edge or by missing data is reported separately, not averaged in.
    """
    win = [(t, w) for t, w in samples if t0 <= t <= t1]
    cycles, cur, partial = [], None, 0

    def close(cur, complete):
        nonlocal partial
        if not complete:
            partial += 1
            return
        pts = cur
        wh = 0.0
        for (ta, wa), (tb, wb) in zip(pts, pts[1:]):
            wh += (wa + wb) / 2 * (tb - ta) / 3600000.0
        cycles.append(
            {
                "start_ms": pts[0][0],
                "end_ms": pts[-1][0],
                "dur_s": round((pts[-1][0] - pts[0][0]) / 1000, 1),
                "wh": round(wh, 4),
            }
        )

    saw_off_before = False
    prev_t = None
    for t, w in win:
        gap = prev_t is not None and (t - prev_t) > gap_ms
        if w >= on_w:
            if cur is not None and gap:
                close(cur, False)  # data gap inside an ON run: not complete
                cur = [(t, w)]
                saw_off_before = False
            elif cur is None:
                cur = [(t, w)]
                if gap:
                    saw_off_before = False
            else:
                cur.append((t, w))
        else:
            if cur is not None:
                close(cur, saw_off_before and not gap)
                cur = None
            saw_off_before = True
        prev_t = t
    if cur is not None:
        close(cur, False)  # runs into end of window
    return cycles, partial


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--unit", action="append", required=True,
                    metavar="LABEL:BRIDGE_DIR:COLOR")
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--on-w", type=float, default=0.05,
                    help="power threshold for bus-ON (W)")
    ap.add_argument("--gap-s", type=float, default=90,
                    help="max sample gap inside a window (s)")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    if len(a.unit) != 2:
        sys.exit("ERROR: exactly two --unit args required")

    units = []
    for spec in a.unit:
        rest, color = spec.rsplit(":", 1)
        label, path = rest.split(":", 1)
        units.append({"label": label, "dir": path, "color": color})

    os.makedirs(a.out_dir, exist_ok=True)
    print(f"Loading bridge traces ({a.hours:g} h window) ...")
    for u in units:
        u["samples"] = load_bridge(u["dir"])

    # analysis window: last N hours of COMMON coverage
    t1 = min(u["samples"][-1][0] for u in units)
    t0c = max(u["samples"][0][0] for u in units)
    t0 = max(t0c, t1 - int(a.hours * 3600000))
    span_h = (t1 - t0) / 3600000
    print(f"Window: {utc(t0)} .. {utc(t1)}  ({span_h:.2f} h common)")
    if span_h < a.hours - 0.1:
        print(f"WARN: common coverage {span_h:.2f} h is less than "
              f"requested {a.hours:g} h")

    gap_ms = int(a.gap_s * 1000)
    rows = []
    for u in units:
        u["mins"] = minute_means(u["samples"], t0, t1)
        u["cycles"], u["partial"] = find_cycles(
            u["samples"], t0, t1, a.on_w, gap_ms
        )
        n = len(u["cycles"])
        if n == 0:
            sys.exit(f"ERROR: no complete cycles for {u['label']}")
        u["wh_cycle"] = sum(c["wh"] for c in u["cycles"]) / n
        u["on_s"] = sum(c["dur_s"] for c in u["cycles"]) / n
        starts = [c["start_ms"] for c in u["cycles"]]
        u["period_s"] = (
            (starts[-1] - starts[0]) / (n - 1) / 1000 if n > 1 else None
        )
        # total over the window: integrate everything (off windows ~0 anyway)
        w = [(t, x) for t, x in u["samples"] if t0 <= t <= t1]
        tot = 0.0
        for (ta, wa), (tb, wb) in zip(w, w[1:]):
            if tb - ta <= gap_ms:
                tot += (wa + wb) / 2 * (tb - ta) / 3600000.0
        u["wh_total"] = tot
        u["wh_per_h"] = tot / span_h
        print(f"  {u['label']}: {n} complete cycles ({u['partial']} partial "
              f"dropped), on {u['on_s']/60:.1f} min, period "
              f"{(u['period_s'] or 0)/60:.1f} min, {u['wh_cycle']:.4f} Wh/cycle, "
              f"{u['wh_total']:.3f} Wh total")
        for c in u["cycles"]:
            rows.append([u["label"], utc(c["start_ms"]), utc(c["end_ms"]),
                        c["dur_s"], c["wh"]])

    # ---- artifacts ----
    with open(os.path.join(a.out_dir, "ab_cycles.csv"), "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["unit", "on_start_utc", "on_end_utc", "on_dur_s", "wh"])
        wtr.writerows(rows)

    u0, u1 = units
    save_pct = (1 - u0["wh_cycle"] / u1["wh_cycle"]) * 100
    save_wh = u1["wh_cycle"] - u0["wh_cycle"]
    ratio = u1["wh_cycle"] / u0["wh_cycle"]

    data = {
        "series": [
            {"label": u["label"], "color": u["color"], "pts": u["mins"]}
            for u in units
        ],
        "stats": [
            {
                "label": u["label"],
                "wh": round(u["wh_cycle"], 4),
                "total": round(u["wh_total"], 3),
                "wh_per_h": round(u["wh_per_h"], 4),
                "n_cycles": len(u["cycles"]),
                "on_min": round(u["on_s"] / 60, 1),
                "period_min": round(u["period_s"] / 60, 1)
                if u["period_s"] else None,
            }
            for u in units
        ],
        "tzoff": TZOFF_H,
    }
    with open(os.path.join(a.out_dir, "ab_coplot_data.json"), "w") as f:
        json.dump(data, f)

    summary = {
        "window_utc": [utc(t0), utc(t1)],
        "window_h": round(span_h, 2),
        "on_w_threshold": a.on_w,
        "gap_s": a.gap_s,
        "units": [
            {
                "label": u["label"],
                "bridge_dir": u["dir"],
                "n_complete_cycles": len(u["cycles"]),
                "n_partial_dropped": u["partial"],
                "wh_per_cycle": round(u["wh_cycle"], 4),
                "wh_total": round(u["wh_total"], 3),
                "wh_per_h": round(u["wh_per_h"], 4),
                "on_min_mean": round(u["on_s"] / 60, 2),
                "period_min_mean": round(u["period_s"] / 60, 2)
                if u["period_s"] else None,
            }
            for u in units
        ],
        "savings": {
            "lower_unit": u0["label"],
            "pct_per_cycle": round(save_pct, 1),
            "wh_per_cycle": round(save_wh, 4),
            "ratio": round(ratio, 2),
        },
    }
    with open(os.path.join(a.out_dir, "ab_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    html = build_html(data, summary, t0, t1)
    out_html = os.path.join(a.out_dir, "ab_coplot.html")
    with open(out_html, "w") as f:
        f.write(html)
    print(f"Wrote {out_html} ({os.path.getsize(out_html)} bytes) "
          f"+ ab_summary.json + ab_cycles.csv + ab_coplot_data.json")


def build_html(data, summary, t0, t1):
    """Same canvas engine as runs/sprint10_overnight_20260729/energy_coplot.html."""
    def pdt(ms):
        return datetime.fromtimestamp(
            ms / 1000 + TZOFF_H * 3600, tz=timezone.utc
        ).strftime("%H:%M")

    d0 = datetime.fromtimestamp(t0 / 1000, tz=timezone.utc)
    sub = (
        f"{d0.strftime('%Y-%m-%d')} {d0.strftime('%H:%M')}&ndash;"
        f"{datetime.fromtimestamp(t1/1000, tz=timezone.utc).strftime('%H:%M')} UTC "
        f"({pdt(t0)}&ndash;{pdt(t1)} PDT) &middot; bridge <code>addr:65</code>, "
        f"60 s means &middot; scroll to zoom, drag to pan, double-click to reset"
    )
    s = summary
    head = f"""<!doctype html><meta charset=utf-8>
<title>bmcam000 vs bmcam003 &mdash; power, last {s['window_h']:g} h</title>
<style>
 body{{font:14px -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;margin:24px;background:#fff;color:#111}}
 h1{{font-size:19px;margin:0 0 4px}} .sub{{color:#666;margin-bottom:16px}}
 #wrap{{position:relative}} canvas{{border:1px solid #e5e7eb;border-radius:6px;cursor:crosshair}}
 .lg{{margin:12px 0 4px}} .lg span{{display:inline-block;margin-right:18px}}
 .sw{{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-1px;margin-right:6px}}
 table{{border-collapse:collapse;margin-top:14px}} td,th{{border:1px solid #e5e7eb;padding:6px 12px;text-align:right}}
 th:first-child,td:first-child{{text-align:left}} tr:nth-child(even){{background:#fafafa}}
 .note{{color:#666;margin-top:12px;max-width:820px;line-height:1.5}}
 #tt{{position:absolute;pointer-events:none;background:#111;color:#fff;padding:6px 9px;border-radius:4px;font-size:12px;display:none;white-space:pre}}
</style>
<h1>bmcam000 vs bmcam003 &mdash; camera-unit power draw on the 24 V BM bus</h1>
<div class=sub>{sub}</div>
<div class=lg id=lg></div>
<div id=wrap><canvas id=cv width=1180 height=420></canvas><div id=tt></div></div>
<table id=tb></table>
<div class=note id=nt></div>
<script>
const D={json.dumps(data)};
const S={json.dumps(s)};
const TZ=D.tzoff*3600*1000;
"""
    engine = """const cv=document.getElementById('cv'), cx=cv.getContext('2d'), tt=document.getElementById('tt');
const L=62,R=18,T=16,Bm=34;
let all=D.series.flatMap(s=>s.pts.filter(p=>p[1]!=null));
let x0=Math.min(...all.map(p=>p[0])), x1=Math.max(...all.map(p=>p[0]));
let ymax=Math.max(...all.map(p=>p[1]))*1.08;
let vx0=x0, vx1=x1;
function fmt(ms){const d=new Date(ms+TZ);return String(d.getUTCHours()).padStart(2,'0')+':'+String(d.getUTCMinutes()).padStart(2,'0');}
function X(t){return L+(t-vx0)/(vx1-vx0)*(cv.width-L-R);}
function Y(v){return T+(1-v/ymax)*(cv.height-T-Bm);}
function draw(){
 cx.clearRect(0,0,cv.width,cv.height);
 cx.strokeStyle='#eef0f3';cx.fillStyle='#6b7280';cx.font='11px sans-serif';cx.lineWidth=1;
 for(let i=0;i<=5;i++){const v=ymax*i/5,y=Y(v);
  cx.beginPath();cx.moveTo(L,y);cx.lineTo(cv.width-R,y);cx.stroke();
  cx.textAlign='right';cx.fillText(v.toFixed(2)+' W',L-8,y+4);}
 const span=vx1-vx0, step=span>4*36e5?36e5:span>1.5*36e5?18e5:6e5;
 for(let t=Math.ceil(vx0/step)*step;t<=vx1;t+=step){const x=X(t);
  cx.strokeStyle='#f3f4f6';cx.beginPath();cx.moveTo(x,T);cx.lineTo(x,cv.height-Bm);cx.stroke();
  cx.fillStyle='#6b7280';cx.textAlign='center';cx.fillText(fmt(t),x,cv.height-Bm+16);}
 cx.fillStyle='#9ca3af';cx.textAlign='center';cx.fillText('time (PDT)',(L+cv.width-R)/2,cv.height-4);
 D.series.forEach(s=>{cx.strokeStyle=s.color;cx.lineWidth=1.6;cx.beginPath();let pen=false;
  s.pts.forEach(p=>{if(p[1]==null||p[0]<vx0-6e4||p[0]>vx1+6e4){pen=false;return;}
   const x=X(p[0]),y=Y(p[1]); if(!pen){cx.moveTo(x,y);pen=true;}else cx.lineTo(x,y);});
  cx.stroke();});
}
function reset(){vx0=x0;vx1=x1;draw();}
cv.addEventListener('wheel',e=>{e.preventDefault();
 const f=e.deltaY<0?0.82:1.22, t=vx0+(e.offsetX-L)/(cv.width-L-R)*(vx1-vx0);
 vx0=t-(t-vx0)*f; vx1=t+(vx1-t)*f; if(vx1-vx0<3e5){const c=(vx0+vx1)/2;vx0=c-1.5e5;vx1=c+1.5e5;}
 draw();},{passive:false});
let drag=null;
cv.addEventListener('mousedown',e=>drag={x:e.offsetX,a:vx0,b:vx1});
addEventListener('mouseup',()=>drag=null);
cv.addEventListener('mousemove',e=>{
 if(drag){const d=(e.offsetX-drag.x)/(cv.width-L-R)*(drag.b-drag.a);vx0=drag.a-d;vx1=drag.b-d;draw();return;}
 const t=vx0+(e.offsetX-L)/(cv.width-L-R)*(vx1-vx0);
 let out=fmt(t)+' PDT';
 D.series.forEach(s=>{let best=null,bd=1e18;
  s.pts.forEach(p=>{if(p[1]==null)return;const d=Math.abs(p[0]-t);if(d<bd){bd=d;best=p;}});
  if(best&&bd<12e4) out+='\\n'+s.label+': '+best[1].toFixed(3)+' W';});
 tt.textContent=out;tt.style.display='block';
 tt.style.left=(e.offsetX+14)+'px';tt.style.top=(e.offsetY+10)+'px';
});
cv.addEventListener('mouseleave',()=>tt.style.display='none');
cv.addEventListener('dblclick',reset);
document.getElementById('lg').innerHTML=D.series.map(s=>
 '<span><i class=sw style="background:'+s.color+'"></i>'+s.label+'</span>').join('');
const st=D.stats, sv=S.savings;
document.getElementById('tb').innerHTML=
 '<tr><th>unit</th><th>cycles</th><th>on / period (min)</th><th>Wh / cycle</th><th>Wh / h</th><th>Wh over '+S.window_h+' h</th></tr>'+
 st.map(s=>'<tr><td>'+s.label+'</td><td>'+s.n_cycles+'</td><td>'+s.on_min+' / '+(s.period_min??'&mdash;')+
   '</td><td>'+s.wh.toFixed(4)+'</td><td>'+s.wh_per_h.toFixed(4)+'</td><td>'+s.total.toFixed(3)+'</td></tr>').join('')+
 '<tr><td><b>'+sv.lower_unit+' saves</b></td><td colspan=5><b>'+sv.pct_per_cycle.toFixed(1)+
 '% per cycle ('+sv.wh_per_cycle.toFixed(4)+' Wh), ratio '+sv.ratio.toFixed(2)+'&times;</b></td></tr>';
document.getElementById('nt').innerHTML=
 'Each trough at <b>0 W</b> is a Spotter bus-off window; energy per cycle is the bridge <code>addr:65</code> '+
 'trace trapezoid-integrated over each complete bus-ON window (halted-Pi baseline included &mdash; that is what '+
 'the battery pays). Partial windows at the edges of the '+S.window_h+' h span are dropped, not averaged in. '+
 'Cycle cadence is measured from the trace, so a schedule difference between units shows up in the '+
 '&ldquo;on&nbsp;/&nbsp;period&rdquo; column rather than skewing Wh/cycle.';
reset();
</script>
"""
    return head + engine


if __name__ == "__main__":
    main()
