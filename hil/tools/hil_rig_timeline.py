#!/usr/bin/env python3
"""hil_rig_timeline.py — the co-plotted rig timeline (Nick 2026-10-04: "one picture, shared time axis").

Builds inline SVG (no chart library, no CDN) for the dashboard: one shared time axis, stacked lanes (nereus000,
SPOT-33507C + bmcam003, SPOT-31593C + bmcam004), and inside each lane small tracks that each have ONE y-scale
(temperatures and voltages never share an axis). The same metric has the same colour in every lane.

Inputs (all in --health-dir on nereus000): metrics.jsonl (one row per 5-min health run), events.jsonl (bus/Pi
on/off, camera temps from <END>/<WS>, charger changes, resets: hil_rig_events.py), ALERTS.log, annotations.jsonl
(hand-written rig events, e.g. the move into the shade), config.json.
Output: build(health_dir, hours) → an <svg> string. Called by hil_rig_dashboard.py for 6 h / 24 h / 3 d.
Limits:  5-min sampling for nereus000 and the Spotter values (a 1-min event between samples is only visible as an
         annotation); camera temps exist only for wakes whose END/WS the console printed; Spotter battery is the
         hourly Sofar sample.
"""

import datetime
import html
import json
import os
import re

UTC = datetime.timezone.utc
# metric -> colour (reference palette, light/dark via CSS vars in the page)
C = {"cpu": "var(--m-cpu)", "sensor": "var(--m-sensor)", "batt": "var(--m-batt)", "volt": "var(--m-volt)",
     "wake": "var(--m-wake)", "pion": "var(--m-pion)", "fault": "var(--st-serious)", "warn": "var(--st-warning)",
     "crit": "var(--st-critical)"}
W, ML, MR = 1100, 150, 16


def P(s):
    try:
        d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def esc(x):
    return html.escape("" if x is None else str(x), quote=True)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(health_dir):
    rows = []
    p = os.path.join(health_dir, "metrics.jsonl")
    if os.path.exists(p):
        for ln in open(p):
            try:
                rows.append(json.loads(ln))
            except ValueError:
                pass
    ev = []
    p = os.path.join(health_dir, "events.jsonl")
    if os.path.exists(p):
        for ln in open(p):
            try:
                ev.append(json.loads(ln))
            except ValueError:
                pass
    alerts = []
    p = os.path.join(health_dir, "ALERTS.log")
    if os.path.exists(p):
        for ln in open(p):
            m = re.match(r"^(\S+) RIG-HEALTH (\w+):? ?(.*)$", ln.strip())
            if m:
                alerts.append((P(m.group(1)), m.group(2).upper(), m.group(3)))
    ann = []
    p = os.path.join(health_dir, "annotations.jsonl")
    if os.path.exists(p):
        for ln in open(p):
            try:
                a = json.loads(ln); ann.append((P(a["t"]), a.get("t_end") and P(a["t_end"]), a["text"], a.get("lane")))
            except (ValueError, KeyError):
                pass
    cfg = json.load(open(os.path.join(health_dir, "config.json")))
    return rows, ev, alerts, ann, cfg


def intervals(ev, spot, on_kind, off_kind, t1):
    out, start = [], None
    for e in ev:
        if e.get("spot") != spot:
            continue
        if e["kind"] == on_kind and start is None:
            start = P(e["t"])
        elif e["kind"] == off_kind and start is not None:
            out.append((start, P(e["t"]))); start = None
    if start is not None:
        out.append((start, t1))
    return out


def episodes(points, gap_s):
    """Merge sorted (t, payload) points closer than gap_s into (start, end, [payloads])."""
    out = []
    for t, pl in points:
        if out and (t - out[-1][1]).total_seconds() <= gap_s:
            out[-1] = (out[-1][0], t, out[-1][2] + [pl])
        else:
            out.append((t, t, [pl]))
    return out


class Track:
    def __init__(self, name, unit, h):
        self.name, self.unit, self.h = name, unit, h
        self.series = []   # (colour, label, [(t, v)], style)
        self.bands = []    # (t0, t1, colour, title, opacity)
        self.bars = []     # (t0, t1, colour, title, frac)


def build(health_dir, hours):
    rows, ev, alerts, ann, cfg = load(health_dir)
    t1 = datetime.datetime.now(UTC)
    t0 = t1 - datetime.timedelta(hours=hours)
    X = lambda t: ML + (W - ML - MR) * max(0.0, min(1.0, (t - t0).total_seconds() / (t1 - t0).total_seconds()))
    rows = [r for r in rows if P(r.get("ts")) and P(r["ts"]) >= t0 - datetime.timedelta(minutes=10)]
    ev = sorted((e for e in ev if P(e.get("t"))), key=lambda e: e["t"])
    spots = list(cfg["spotters"].items())

    def series(key, scale=1.0, dedupe_key=None):
        out, last = [], None
        for r in rows:
            v = fnum(r.get(key))
            if v is None:
                continue
            if dedupe_key:
                k = r.get(dedupe_key)
                if k == last:
                    continue
                last = k
                t = P(k) or P(r["ts"])
            else:
                t = P(r["ts"])
            out.append((t, v * scale))
        return out

    lanes = []
    # --- nereus000 lane
    tc = Track("CPU", "°C", 54); tc.series.append((C["cpu"], "nereus000 CPU", series("n000_temp_c"), "line"))
    tp = Track("Power", "V", 54)
    tp.series.append((C["volt"], "VIN (supply)", series("lp_vin_mv", 0.001), "line"))
    tp.series.append((C["batt"], "VBAT (LiFePO4)", series("lp_vbat_mv", 0.001), "line"))
    onb = [(P(r["ts"]), 1) for r in rows if str(r.get("lp_external_power")).lower() in ("false", "0")]
    for a, b, _ in episodes(onb, 400):
        tp.bands.append((a - datetime.timedelta(minutes=5), b, C["crit"], "nereus000 on battery", 0.18))
    lanes.append(("nereus000", None, [tc, tp]))
    # --- Spotter + camera lanes
    for spot, sc in spots:
        tt = Track("Camera", "°C", 54)
        ct = [(P(e["t"]), e["cpu_c"]) for e in ev if e.get("spot") == spot and e["kind"] == "camtemp"
              and P(e["t"]) >= t0 and e.get("cpu_c") is not None]
        st_ = [(P(e["t"]), e["sensor_c"]) for e in ev if e.get("spot") == spot and e["kind"] == "camtemp"
               and P(e["t"]) >= t0 and e.get("sensor_c") is not None]
        tt.series.append((C["cpu"], f"{sc['host']} CPU", ct, "dot"))
        tt.series.append((C["sensor"], f"{sc['host']} image sensor", st_, "dot"))
        tb = Track("Battery", "V", 40); tb.series.append((C["batt"], f"{spot} battery", series(f"{spot}_batt_v", dedupe_key=f"{spot}_batt_at"), "line"))
        tv = Track("Bus", "V", 40); tv.series.append((C["volt"], f"{spot} BM bus (while on)", series(f"{spot}_bus_v_on"), "line"))
        tw = Track("Wakes", "", 20)
        for a, b in intervals(ev, spot, "bus_on", "bus_off", t1):
            if b >= t0:
                tw.bars.append((a, b, C["wake"], f"bus on {a:%H:%M:%S}–{b:%H:%M:%S}Z ({(b - a).total_seconds() / 60:.1f} min)", 1.0))
        for a, b in intervals(ev, spot, "pi_on", "pi_off", t1):
            if b >= t0:
                tw.bars.append((a, b, C["pion"], f"{sc['host']} up {a:%H:%M:%S}–{b:%H:%M:%S}Z ({(b - a).total_seconds():.0f} s)", 0.55))
        # charger faults (lane-wide bands)
        lane_bands, cur = [], None
        for e in ev:
            if e.get("spot") == spot and e["kind"] == "charger":
                if cur and cur[1] != "OK":
                    lane_bands.append((cur[0], P(e["t"]), cur[1]))
                cur = (P(e["t"]), e["state"])
        if cur and cur[1] != "OK":
            lane_bands.append((cur[0], t1, cur[1]))
        merged = []
        for a, b, s in lane_bands:
            if merged and (a - merged[-1][1]).total_seconds() <= 120:
                merged[-1] = (merged[-1][0], b, s, merged[-1][3] + 1)
            else:
                merged.append((a, b, s, 1))
        lanes.append((f"{spot} · {sc['host']}", (spot, sc, merged), [tt, tb, tv, tw]))

    # ---- render
    LANE_GAP, HEAD, AXIS = 14, 22, 26
    y = 8
    out = []
    lane_geom = []
    for title, extra, tracks in lanes:
        lane_top = y
        y += HEAD
        tgeom = []
        for tr in tracks:
            tgeom.append((tr, y)); y += tr.h + 6
        lane_geom.append((title, extra, tracks, lane_top, y, tgeom))
        y += LANE_GAP
    H = y + AXIS
    out.append(f'<svg class="tl" viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Rig timeline, last {hours} h" '
               f'data-t0="{t0.timestamp():.0f}" data-t1="{t1.timestamp():.0f}" data-ml="{ML}" data-mr="{MR}" data-w="{W}">')
    # time grid
    step = 1 if hours <= 6 else (3 if hours <= 24 else 12)
    g = t0.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    while g < t1:
        if g.hour % step == 0:
            x = X(g)
            out.append(f'<line x1="{x:.1f}" y1="4" x2="{x:.1f}" y2="{H - AXIS + 4}" class="grid"/>')
            lbl = g.strftime("%H:%M") if g.hour or hours <= 24 else g.strftime("%a %d")
            out.append(f'<text x="{x:.1f}" y="{H - 8}" class="axis" text-anchor="middle">{lbl}Z</text>')
        g += datetime.timedelta(hours=1)
    for title, extra, tracks, top, bot, tgeom in lane_geom:
        out.append(f'<rect x="{ML}" y="{top}" width="{W - ML - MR}" height="{bot - top}" class="lane"/>')
        out.append(f'<text x="8" y="{top + 15}" class="lanet">{esc(title)}</text>')
        if extra:
            spot, sc, merged = extra
            for a, b, s, n in merged:
                if b < t0:
                    continue
                x0, x1 = X(a), max(X(b), X(a) + 2)
                out.append(f'<rect x="{x0:.1f}" y="{top + HEAD - 4}" width="{x1 - x0:.1f}" height="{bot - top - HEAD + 4}" '
                           f'fill="{C["fault"]}" fill-opacity="0.22" class="band"><title>{esc(spot)} charger {esc(s)} '
                           f'{a:%m-%d %H:%M}–{b:%H:%M}Z ({(b - a).total_seconds() / 60:.0f} min, {n} flap{"s" if n > 1 else ""})</title></rect>')
                out.append(f'<text x="{x0 + 3:.1f}" y="{top + HEAD + 8}" class="bandt">charger {esc(s.lower().replace("_", " "))}</text>')
            # resets / charge-mode episodes
            rs = [(P(e["t"]), e.get("text", "")) for e in ev if e.get("spot") == spot and e["kind"] == "reset" and P(e["t"]) >= t0]
            for a, b, texts in episodes(rs, 600):
                x = X(a)
                out.append(f'<path d="M{x - 5:.1f},{top + 6} l10,10 m0,-10 l-10,10" class="xmark"><title>{esc(spot)} '
                           f'{a:%m-%d %H:%M}Z: {esc(texts[0][:90])} ({len(texts)} line{"s" if len(texts) > 1 else ""})</title></path>')
        # alert strip (top of lane): episodes of WARN/CRIT whose reasons name this lane
        keys = ["nereus000"] if not extra else [extra[0], extra[1]["host"]]
        pts = [(t, (lv, rsn)) for t, lv, rsn in alerts if t and t >= t0 and lv in ("WARN", "CRIT") and any(k in rsn for k in keys)]
        for a, b, pl in episodes(pts, 400):
            lv = "CRIT" if any(p[0] == "CRIT" for p in pl) else "WARN"
            x0, x1 = X(a), max(X(b + datetime.timedelta(minutes=5)), X(a) + 4)
            out.append(f'<rect x="{x0:.1f}" y="{top + 2}" width="{x1 - x0:.1f}" height="6" rx="2" fill="{C["crit" if lv == "CRIT" else "warn"]}">'
                       f'<title>{lv} {a:%m-%d %H:%M}–{b:%H:%M}Z: {esc(pl[-1][1][:220])}</title></rect>')
        for tr, ty in tgeom:
            vals = [v for _, _, pts_, _ in tr.series for _, v in pts_ if pts_]
            out.append(f'<text x="14" y="{ty + tr.h / 2 + 4:.1f}" class="trk">{esc(tr.name)}{(" " + esc(tr.unit)) if tr.unit else ""}</text>')
            for a, b, col, ttl, op in tr.bands:
                if b >= t0:
                    out.append(f'<rect x="{X(a):.1f}" y="{ty}" width="{max(X(b) - X(a), 2):.1f}" height="{tr.h}" fill="{col}" fill-opacity="{op}"><title>{esc(ttl)}</title></rect>')
            for a, b, col, ttl, frac in tr.bars:
                hh = tr.h * frac
                out.append(f'<rect x="{X(a):.1f}" y="{ty + (tr.h - hh) / 2:.1f}" width="{max(X(b) - X(a), 1.5):.1f}" height="{hh:.1f}" rx="1.5" fill="{col}"><title>{esc(ttl)}</title></rect>')
            if not vals:
                if not tr.bars and not tr.bands:
                    out.append(f'<text x="{ML + 6}" y="{ty + tr.h / 2 + 4:.1f}" class="none">no data in this range</text>')
                continue
            lo, hi = min(vals), max(vals)
            pad = max((hi - lo) * 0.15, 0.5 if tr.unit == "°C" else 0.02)
            lo, hi = lo - pad, hi + pad
            Y = lambda v: ty + tr.h - (v - lo) / (hi - lo) * tr.h
            out.append(f'<text x="{ML - 8}" y="{ty + 8}" class="tick" text-anchor="end">{hi - pad:.{1 if tr.unit == "°C" else 2}f}</text>')
            if f"{lo + pad:.2f}" != f"{hi - pad:.2f}":
                out.append(f'<text x="{ML - 8}" y="{ty + tr.h}" class="tick" text-anchor="end">{lo + pad:.{1 if tr.unit == "°C" else 2}f}</text>')
            for col, lbl, pts_, style in tr.series:
                pts_ = [(t, v) for t, v in pts_ if t >= t0]
                if not pts_:
                    continue
                if style == "line":
                    segs, cur = [], []
                    for i, (t, v) in enumerate(pts_):  # break the line on gaps > 20 min
                        if cur and (t - pts_[i - 1][0]).total_seconds() > 1200:
                            segs.append(cur); cur = []
                        cur.append(f"{X(t):.1f},{Y(v):.1f}")
                    segs.append(cur)
                    for sg in segs:
                        if len(sg) == 1:
                            x_, y_ = sg[0].split(","); out.append(f'<circle cx="{x_}" cy="{y_}" r="2" fill="{col}"/>')
                        else:
                            out.append(f'<polyline points="{" ".join(sg)}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>')
                    t_, v_ = pts_[-1]
                    out.append(f'<circle cx="{X(t_):.1f}" cy="{Y(v_):.1f}" r="7" fill="transparent"><title>{esc(lbl)}: {v_:.2f} at {t_:%H:%M}Z (latest)</title></circle>')
                    every = max(1, len(pts_) // 60)
                    for t, v in pts_[::every]:
                        out.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="5" fill="transparent"><title>{esc(lbl)}: {v:.2f} at {t:%m-%d %H:%M}Z</title></circle>')
                else:
                    for t, v in pts_:
                        out.append(f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="4" fill="{col}" stroke="var(--surface)" stroke-width="2"><title>{esc(lbl)}: {v:.1f} °C at {t:%m-%d %H:%M}Z</title></circle>')
    # annotations (all lanes)
    last_x, row = -999, 0
    for a, b, text, lane in sorted(ann, key=lambda z: z[0] or t0):
        if a and a >= t0:
            x = X(a)
            row = row + 1 if x - last_x < 170 else 0   # stagger labels that would overprint
            last_x = x
            out.append(f'<line x1="{x:.1f}" y1="4" x2="{x:.1f}" y2="{H - AXIS}" class="ann"><title>{esc(text)} {a:%m-%d %H:%M}Z</title></line>')
            out.append(f'<text x="{x + 3:.1f}" y="{H - AXIS - 4 - 12 * row}" class="annt">{esc(text)} ({a:%H:%M}Z)</text>')
    out.append(f'<line class="xhair" x1="0" x2="0" y1="4" y2="{H - AXIS}" style="display:none"/><text class="xhairt" x="0" y="14" style="display:none"></text>')
    out.append("</svg>")
    return "".join(out)
