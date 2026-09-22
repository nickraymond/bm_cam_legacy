#!/usr/bin/env python3
"""
bm_video_soak_report.py -- one row per video_tx cycle, camera glass to computer screen.

PURPOSE
    Sprint22 duty-cycle soak. Four INDEPENDENT records describe each cycle and
    none of them can see the whole path; this tool joins them so a loss can be
    attributed to a stage instead of guessed at:

      camera log      what the Pi did and BELIEVES it sent  (rc_cycle_*.log)
      Spotter console what the Spotter accepted or REJECTED  (queue-full is
                      visible ONLY here; the Pi gets no error)
      Sofar API       what actually arrived                  (raw messages)
      staging API     what the backend stored / can play

    Cycles are keyed by the clip's START filename
    (`<capture UTC>_video_<N>s.h264`), which all four records carry or imply.

INPUTS
    --camera-logs DIR   folder of rc_cycle_*.log pulled from the unit (OPTIONAL since
                        Sprint25: without it, cycles come from the Spotter SD BM_TX.log)
    --console LOG ...   Spotter console log(s) from spotter_serial_monitor.py
    --sd-log DIR ...    Spotter SD `log/` folder(s) (Sprint25, Sprint24 step 7). Better
                        than the console: no mid-line interleaving. Sources per cycle:
                          MS.log      queued / queue_full / hourly LEGACY reports / Rx checks
                          BM_TX.log   every payload the camera handed over (hex, decoded
                                      here) -> burst window, submitted + unique chunks
                          HDR.log     the Spotter's own 5-minute 6 KB push
                          BRIDGE_SYS  bus power on/off
                          ORC.log     hourly "Running health check" (boot-anchored)
                          SYS.log     resets
    --sofar-json FILE   raw Sofar rows already dumped (skips the fetch; scratch only —
                        the hex payloads are bench footage, never commit them)
    --slice-hours N     fetch Sofar in N-hour slices (the API caps one call at ~5000 rows)
    --run-tag TAG       printed on the cut sheet
    --spotter-id / --node-id / --device-id    e.g. SPOT-33507C / 0xe6fe… / BMCAM_004
    --start / --end     UTC ISO window for the Sofar pull
    --api               backend base URL (default: staging)
    --out DIR           writes cycles.csv + cycles.md + loss_by_stage.md +
                        timeline_<spotter>.svg (cut sheet: bus windows, bursts coloured
                        by loss, queue-full ticks, HDR pushes, reports, Rx checks; a
                        second panel folds everything onto the 5-minute grid)
    SOFAR_API_TOKEN_BM_REEF in the environment (never on the CLI). Without it
    the Sofar columns are left empty and said so.

NEW COLUMNS (Sprint24 step 7 / Sprint25), all from the SD log unless noted
    burst_start_utc / burst_end_utc / burst_s   first START -> last chunk/END submission
    phase_s                     burst start, seconds after the last 5-minute boundary
    sd_submitted / sd_unique    payloads handed to the Spotter / distinct chunk indices
    queue_full_count_in_cycle   `Queue MS_Q_CELLULAR_ONLY is full` inside the burst (+-5 s)
    hdr_in_burst                HDR pushes inside the burst (0 when aligned bursts miss them)
    report_utc / report_kind    LEGACY report(s) from 60 s before the burst to its end:
                                boot(171 B) / hourly(50 B) / health(37 B, from the
                                orchestrator's hourly health check) / network(340 B,
                                bridge topology change or `bridge cfg commit`)
    rx_check_utc                first `Checking for Rx Messages` in that same window
    report_offset_s             burst start minus the most recent report before it
    loss_stage                  OK / spotter-reject (missing <= queue_full) / bus-cut
                                (burst shorter than planned, no END) / after-spotter
                                (accepted by the Spotter, absent at Sofar) /
                                no-START-at-sofar (whole group invisible downstream)

KNOWN LIMITATIONS
    * BM_TX.log is written through the Spotter's logging queue, which drops lines
      under load (SYS.log: "Logging queue hit new lowest ever spaces available").
      sd_submitted can therefore under-count; queue_full comes from MS.log (never
      seen dropped). Sofar unique is the ground truth for what arrived.
    * BM_TX `Len:` differs from the decoded byte count by a fixed framing amount;
      the decoded ASCII is what is trusted.
    * The console log interleaves lines mid-line; counts are by regex over the
      whole window, never by column.
    * Console timestamps come from the monitoring host's clock (1 s) plus the
      Spotter's own ms timestamp inside the line; the inner one is used.
    * A cycle whose START never reached Sofar has no Sofar/backend columns —
      that is itself the finding.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ISO = "%Y-%m-%dT%H:%M:%SZ"
RE_FILE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)_video_([\d.]+)s\.h264")
RE_PAYLOAD = re.compile(r"\[VTX\] payload: (\d+) B = (\d+) msgs \(([\d.]+)% of budget\) (\d+) pass-2 tries, "
                        r"prescale ([\d.]+)s encode ([\d.]+)s(?:, TRIMMED (\d+) frames)?")
RE_BUDGET = re.compile(r"\[VTX\] message budget: cap=(\d+) affordable_now=(-?\d+).*?-> (\d+) chunk msgs")
RE_PHASE = re.compile(r"\[PHASE\] phase=([\d.]+)s burst=([\d.]+)s lane=([\d.]+)s -> (\S+) wait=([\d.]+)s")
RE_TX = re.compile(r"\[VTX\] transmit done: sent=(\d+)/(\d+) complete=(\w+) keyframe_repeat=(\d+)/(\d+) "
                   r"uart=([\d.]+)s file=(\S+)")
RE_END = re.compile(r"\[VTX\] cycle end: stage=(\S+) elapsed=([\d.]+)s of (\d+)s; halt=(\S+)")
RE_CRON_START = re.compile(r"\[RC-CRON\] start_utc=(\S+)")
RE_UPTIME = re.compile(r"\[RC-CRON\] uptime_s=([\d.]+)")
RE_SPOT_TS = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+Z \[")


def parse_camera_log(text):
    """One rc_cycle_*.log -> dict (empty when the cycle never reached video_tx)."""
    row = {}
    m = RE_CRON_START.search(text)
    if m:
        row["runtime_start_utc"] = m.group(1)[:19] + "Z"
    m = RE_UPTIME.search(text)
    if m:
        row["boot_to_runtime_s"] = float(m.group(1))
    m = RE_BUDGET.search(text)
    if m:
        row.update(cap=int(m.group(1)), affordable=int(m.group(2)), budget_msgs=int(m.group(3)))
    m = RE_PAYLOAD.search(text)
    if m:
        row.update(payload_bytes=int(m.group(1)), chunk_msgs=int(m.group(2)), budget_used_pct=float(m.group(3)),
                   pass2_tries=int(m.group(4)), prescale_s=float(m.group(5)), encode_s=float(m.group(6)),
                   frames_trimmed=int(m.group(7) or 0))
    m = RE_PHASE.search(text)
    if m:
        row.update(phase_s=float(m.group(1)), burst_s=float(m.group(2)), lane_plan=m.group(4),
                   lane_wait_s=float(m.group(5)))
    row["lane_wait_skipped"] = "skipping the" in text and "lane wait" in text
    m = RE_TX.search(text)
    if m:
        row.update(sent=int(m.group(1)), planned=int(m.group(2)), complete_send=m.group(3) == "True",
                   keyframe_repeated=int(m.group(4)), keyframe_chunks=int(m.group(5)),
                   uart_s=float(m.group(6)), file=m.group(7))
    m = RE_END.search(text)
    if m:
        row.update(stage=m.group(1), cycle_elapsed_s=float(m.group(2)), halt=m.group(4))
    errs = [l.strip()[:160] for l in text.splitlines() if "[VTX][ERROR]" in l or "Traceback" in l]
    if errs:
        row["camera_errors"] = " | ".join(errs[:3])
    return row


def console_events(paths):
    """[(datetime, kind)] for kind in queued / queue_full / spotter_tx_done."""
    events = []
    for path in paths:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                kind = ("queue_full" if "MS_Q_CELLULAR_ONLY is full" in line else
                        "queued" if "to queue MS_Q_CELLULAR_ONLY" in line and "Added message" in line else
                        "spotter_tx_done" if "transmitted successfully" in line else None)
                if not kind:
                    continue
                m = RE_SPOT_TS.search(line)
                if m:
                    events.append((datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc), kind))
    return events


def console_columns(events, burst_start, burst_end):
    inside = [(t, k) for t, k in events if burst_start - timedelta(seconds=5) <= t <= burst_end + timedelta(seconds=5)]
    full = sorted(t for t, k in inside if k == "queue_full")
    return {"spotter_queued": sum(1 for _, k in inside if k == "queued"),
            "spotter_queue_full": len(full),
            "queue_full_window": f"{full[0]:%H:%M:%S}-{full[-1]:%H:%M:%S}Z" if full else "",
            "spotter_own_tx_in_burst": sum(1 for _, k in inside if k == "spotter_tx_done")}


def sofar_groups(entries, node_id):
    """{filename: {start_ts, unique, missing, end}} from raw Sofar rows, in time order."""
    groups, current = {}, None
    for e in sorted(entries, key=lambda x: x.get("timestamp", "")):
        if e.get("bristlemouth_node_id") != node_id:
            continue
        try:
            text = bytes.fromhex(e.get("value", "")).decode("utf-8", "replace")
        except ValueError:
            continue
        if "<START IMG>" in text:
            current = None                         # ANY start closes the open group (a lost END must not leak)
            if "fmt=h264" not in text:
                continue
            m, n = RE_FILE.search(text), re.search(r"length: (\d+)", text)
            if m and n:
                current = groups.setdefault(m.group(0), {"start_ts": e.get("timestamp"), "length": int(n.group(1)),
                                                         "got": set(), "end": False})
            continue
        if current is None:
            continue
        m = re.match(r"<I(\d+)>", text)
        if m:
            current["got"].add(int(m.group(1)))
        elif "<END IMG>" in text:
            current["end"] = True
            current = None
    return groups


RE_SD_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{3})Z")
RE_BMTX_SUBMIT = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{3})Z \[BM_TX\] \[INFO\] Submitted .* Len: (\d+)")
RE_HEXLINE = re.compile(r"^(?: [0-9a-f]{2})+\s*$")
RE_LEGACY = re.compile(r"Added message\(id: (\d+) len: (\d+)\) to queue MS_Q_LEGACY: \(1\)!")
REPORT_KIND_BY_LEN = {171: "boot", 50: "hourly", 37: "health", 340: "network"}
GRID_S = 300


def _sd_ts(line):
    m = RE_SD_TS.match(line)
    if not m:
        return None
    return (datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            + timedelta(milliseconds=int(m.group(2))))


def _sd_files(dirs, suffix):
    out = []
    for d in dirs:
        out += sorted(Path(d).glob(f"*_{suffix}.log"))
    return out


def sd_events(dirs):
    """Spotter-side events from the SD log/ folder(s): [(datetime, kind, detail)].

    kinds: queued, queue_full, legacy_report (detail = kind by length), rx_check,
    sync, hdr_push, bus_on, bus_off, health_check, reset (detail = reason).
    Parsed by PATTERN, never by column: one MS.log line was seen truncated mid-line.
    """
    ev = []
    for f in _sd_files(dirs, "MS"):
        for line in open(f, errors="replace"):
            t = _sd_ts(line)
            if t is None:
                continue
            if "MS_Q_CELLULAR_ONLY is full" in line:
                ev.append((t, "queue_full", ""))
            elif "to queue MS_Q_CELLULAR_ONLY" in line and "Added message" in line:
                ev.append((t, "queued", ""))
            elif "Checking for Rx Messages" in line:
                ev.append((t, "rx_check", ""))
            elif "Attempting to Sync" in line:
                ev.append((t, "sync", ""))
            else:
                m = RE_LEGACY.search(line)
                if m:
                    ev.append((t, "legacy_report", REPORT_KIND_BY_LEN.get(int(m.group(2)), f"len{m.group(2)}")))
    for f in _sd_files(dirs, "HDR"):
        for line in open(f, errors="replace"):
            t = _sd_ts(line)
            if t is not None and "added to queue" in line:
                ev.append((t, "hdr_push", ""))
    for f in _sd_files(dirs, "BRIDGE_SYS"):
        for line in open(f, errors="replace"):
            t = _sd_ts(line)
            if t is not None and "Bridge bus power:" in line:
                ev.append((t, "bus_on" if line.rstrip().endswith("1") else "bus_off", ""))
    for f in _sd_files(dirs, "ORC"):
        for line in open(f, errors="replace"):
            t = _sd_ts(line)
            if t is not None and "Running health check" in line:
                ev.append((t, "health_check", ""))
    for f in _sd_files(dirs, "SYS"):
        for line in open(f, errors="replace"):
            t = _sd_ts(line)
            if t is not None and "Reset Reason:" in line:
                ev.append((t, "reset", line.split("Reset Reason:")[1].strip()))
    ev.sort(key=lambda e: e[0])
    # bus_on/bus_off lines are written twice on some firmware: dedupe exact repeats
    out = []
    for e in ev:
        if out and e[1] in ("bus_on", "bus_off") and out[-1][1] == e[1] and abs((out[-1][0] - e[0]).total_seconds()) < 1:
            continue
        out.append(e)
    return out


def sd_submissions(dirs):
    """Decode BM_TX.log hex dumps -> [{utc, len, kind, filename, index, text}] in order."""
    rows, cur = [], None

    def finish(c):
        raw = bytes.fromhex("".join(c["hex"]).replace(" ", ""))
        text = raw.decode("utf-8", "replace")
        kind, fn, idx = "other", "", None
        if text.startswith("<START IMG>"):
            kind = "start"
            m = re.search(r"filename: (\S+?),", text)
            fn = m.group(1) if m else ""
        elif text.startswith("<END IMG>"):
            kind = "end"
            m = re.search(r"filename: (\S+?),", text)
            fn = m.group(1) if m else ""
        elif text.startswith("<WS "):
            kind = "ws"
        else:
            m = re.match(r"<I(\d+)>", text)
            if m:
                kind, idx = "chunk", int(m.group(1))
        c.update(kind=kind, filename=fn, index=idx, text=text[:160])
        del c["hex"]
        rows.append(c)

    for f in _sd_files(dirs, "BM_TX"):
        for line in open(f, errors="replace"):
            m = RE_BMTX_SUBMIT.match(line)
            if m:
                if cur:
                    finish(cur)
                cur = {"utc": datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc),
                       "len": int(m.group(3)), "hex": []}
            elif cur is not None and RE_HEXLINE.match(line):
                cur["hex"].append(line.strip())
    if cur:
        finish(cur)
    return rows


def sd_bursts(subs):
    """Group decoded submissions into media bursts keyed by START filename.

    {filename: {t0, t1, submitted, unique, max_index, end, length, fmt}}; `length` and
    `fmt` come from the START text when present.
    """
    groups, g = {}, None
    for r in subs:
        if r["kind"] == "start":
            n = re.search(r"length: (\d+)", r["text"])
            fmt = re.search(r"fmt=(\w+)", r["text"])
            g = {"t0": r["utc"], "t1": r["utc"], "submitted": 0, "idx": set(), "end": False,
                 "length": int(n.group(1)) if n else None, "fmt": fmt.group(1) if fmt else ""}
            groups[r["filename"]] = g
        elif g is not None and r["kind"] == "chunk":
            g["submitted"] += 1
            g["idx"].add(r["index"])
            g["t1"] = r["utc"]
        elif g is not None and r["kind"] == "end":
            g["end"] = True
            g["t1"] = r["utc"]
            g = None
    for g in groups.values():
        g["unique"] = len(g["idx"])
        g["max_index"] = max(g["idx"]) if g["idx"] else -1
    return groups


def sd_columns(events, t0, t1):
    """Sprint24 step-7 columns for one burst [t0, t1] from the SD event list."""
    lo, hi = t0 - timedelta(seconds=5), t1 + timedelta(seconds=5)
    inside = [e for e in events if lo <= e[0] <= hi]
    full = [e[0] for e in inside if e[1] == "queue_full"]
    reports = [e for e in events if t0 - timedelta(seconds=60) <= e[0] <= t1 and e[1] == "legacy_report"]
    rx = [e[0] for e in events if t0 - timedelta(seconds=60) <= e[0] <= t1 + timedelta(seconds=60) and e[1] == "rx_check"]
    before = [e[0] for e in events if e[1] == "legacy_report" and e[0] <= t0]
    return {
        "burst_start_utc": t0.strftime(ISO), "burst_end_utc": t1.strftime(ISO),
        "burst_s": round((t1 - t0).total_seconds()),
        "phase_s": round((t0 - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() % GRID_S),
        "spotter_queued": sum(1 for e in inside if e[1] == "queued"),
        "queue_full_count_in_cycle": len(full),
        "spotter_queue_full": len(full),
        "queue_full_window": f"{full[0]:%H:%M:%S}-{full[-1]:%H:%M:%S}Z" if full else "",
        "hdr_in_burst": sum(1 for e in inside if e[1] == "hdr_push"),
        "report_utc": " ".join(e[0].strftime(ISO) for e in reports),
        "report_kind": " ".join(e[2] for e in reports),
        "rx_check_utc": rx[0].strftime(ISO) if rx else "",
        "report_offset_s": round((t0 - before[-1]).total_seconds()) if before else "",
    }


def loss_stage(row):
    """Attribute a cycle's loss to a stage from the SD + Sofar columns."""
    if row.get("sd_fmt") and row["sd_fmt"] != "h264":
        return "stills-not-parsed"                 # sofar_groups() follows video groups only
    if row.get("sofar_length") is None:
        return "no-START-at-sofar" if row.get("sd_submitted") else "no-burst"
    missing = row["sofar_length"] - row["sofar_unique"]
    if missing <= 0:
        return "OK"
    # bus-cut = the camera never got to the tail of the clip (bus off / Spotter reset mid-burst).
    # Judged by the highest chunk index handed over, not by the END line: BM_TX.log drops
    # lines under load, so a missing END alone proves nothing.
    if row.get("sd_max_index", -1) + 1 < 0.9 * row["sofar_length"]:
        return "bus-cut"
    if row.get("queue_full_count_in_cycle", 0) >= missing:
        return "spotter-reject"
    return "after-spotter"


def fetch_sofar_sliced(spotter_id, start, end, token, slice_hours):
    """Sofar sensor-data in slices (one call caps at ~5000 rows), de-duplicated."""
    import count_complete_images as cci
    t = datetime.strptime(start, ISO).replace(tzinfo=timezone.utc)
    t_end = datetime.strptime(end, ISO).replace(tzinfo=timezone.utc)
    rows, seen = [], set()
    while t < t_end:
        u = min(t + timedelta(hours=slice_hours), t_end)
        for r in cci.fetch(spotter_id, t.strftime(ISO), u.strftime(ISO), token):
            k = (r.get("timestamp"), r.get("bristlemouth_node_id"), (r.get("value") or "")[:64])
            if k not in seen:
                seen.add(k)
                rows.append(r)
        print(f"  Sofar {t:%m-%d %H:%M}-{u:%H:%M}: {len(rows)} rows so far")
        t = u
    return rows


def _svg_esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_timeline_svg(path, rows, events, *, title, run_tag, spotter_id, t_start, t_end, notes):
    """Cut sheet: panel 1 = wall-clock timeline (bus windows, bursts coloured by loss,
    queue-full ticks, HDR pushes, reports, Rx checks, resets); panel 2 = everything
    folded onto the 5-minute grid. Pure SVG, no dependencies. Bars are NOT to scale
    in height; time axes are linear.
    """
    W, LM, RM = 1600, 90, 30
    span = max(1.0, (t_end - t_start).total_seconds())

    def X(t):
        return LM + (W - LM - RM) * ((t - t_start).total_seconds() / span)

    colour = {"OK": "#2a9d3f", "spotter-reject": "#e0a800", "bus-cut": "#7a7a7a", "after-spotter": "#c0392b",
              "no-START-at-sofar": "#8e44ad", "no-burst": "#bbbbbb"}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="620" font-family="Helvetica, Arial, sans-serif" font-size="12">',
           '<rect width="100%" height="100%" fill="white"/>',
           f'<text x="{LM}" y="24" font-size="18" font-weight="bold">{_svg_esc(title)}</text>',
           f'<text x="{LM}" y="42">run {_svg_esc(run_tag)} · {_svg_esc(spotter_id)} · {t_start:%Y-%m-%d %H:%M}Z to {t_end:%Y-%m-%d %H:%M}Z · '
           f'{len(rows)} bursts · cut sheet is a review artifact: time axes linear, bar heights not to scale</text>']
    # ---- panel 1 lanes
    lanes = [("bus ON", 70), ("bursts", 100), ("queue full", 140), ("HDR push", 165), ("LEGACY report", 190), ("Rx check", 215), ("reset", 240)]
    for name, y in lanes:
        out.append(f'<text x="4" y="{y+4}" font-size="11">{name}</text>')
        out.append(f'<line x1="{LM}" y1="{y}" x2="{W-RM}" y2="{y}" stroke="#eee"/>')
    # hour ticks
    t = t_start.replace(minute=0, second=0, microsecond=0)
    while t <= t_end:
        if t >= t_start:
            out.append(f'<line x1="{X(t):.1f}" y1="60" x2="{X(t):.1f}" y2="255" stroke="#ddd"/>')
            out.append(f'<text x="{X(t):.1f}" y="268" font-size="10" text-anchor="middle">{t:%H}Z</text>')
        t += timedelta(hours=1)
    on = None
    for ts, kind, det in events:
        if kind == "bus_on":
            on = ts
        elif kind == "bus_off" and on is not None:
            out.append(f'<rect x="{X(on):.1f}" y="62" width="{max(1.0, X(ts)-X(on)):.1f}" height="14" fill="#9ec9ff"/>')
            on = None
        elif kind == "queue_full":
            out.append(f'<line x1="{X(ts):.1f}" y1="132" x2="{X(ts):.1f}" y2="148" stroke="#e0a800"/>')
        elif kind == "hdr_push":
            out.append(f'<line x1="{X(ts):.1f}" y1="158" x2="{X(ts):.1f}" y2="172" stroke="#5b8def"/>')
        elif kind == "legacy_report":
            c = {"hourly": "#c0392b", "health": "#ff6f00", "network": "#8e44ad", "boot": "#000"}.get(det, "#555")
            out.append(f'<line x1="{X(ts):.1f}" y1="180" x2="{X(ts):.1f}" y2="200" stroke="{c}" stroke-width="2"><title>{_svg_esc(det)} {ts:%H:%M:%S}Z</title></line>')
        elif kind == "rx_check":
            out.append(f'<line x1="{X(ts):.1f}" y1="208" x2="{X(ts):.1f}" y2="222" stroke="#333"/>')
        elif kind == "reset":
            out.append(f'<line x1="{X(ts):.1f}" y1="230" x2="{X(ts):.1f}" y2="250" stroke="#000" stroke-width="2"><title>{_svg_esc(det)} {ts:%H:%M:%S}Z</title></line>')
    if on is not None:
        out.append(f'<rect x="{X(on):.1f}" y="62" width="{max(1.0, X(t_end)-X(on)):.1f}" height="14" fill="#9ec9ff"/>')
    for r in rows:
        if not r.get("burst_start_utc"):
            continue
        a = datetime.strptime(r["burst_start_utc"], ISO).replace(tzinfo=timezone.utc)
        b = datetime.strptime(r["burst_end_utc"], ISO).replace(tzinfo=timezone.utc)
        st = r.get("loss_stage", "no-burst")
        miss = "" if r.get("sofar_length") is None else f' missing {r["sofar_length"] - (r.get("sofar_unique") or 0)}'
        out.append(f'<rect x="{X(a):.1f}" y="92" width="{max(2.0, X(b)-X(a)):.1f}" height="18" fill="{colour.get(st, "#999")}">'
                   f'<title>{_svg_esc(r.get("file",""))} {st}{miss} qfull={r.get("queue_full_count_in_cycle","")} report={_svg_esc(r.get("report_kind",""))}</title></rect>')
    # legend
    lx = LM
    for name, c in colour.items():
        out.append(f'<rect x="{lx}" y="282" width="12" height="12" fill="{c}"/><text x="{lx+16}" y="292" font-size="11">{name}</text>')
        lx += 150
    out.append('<text x="{}" y="292" font-size="11">reports: <tspan fill="#c0392b">hourly</tspan> <tspan fill="#ff6f00">health</tspan> <tspan fill="#8e44ad">network</tspan> <tspan>boot</tspan></text>'.format(lx))
    # ---- panel 2: fold onto the 5-minute grid
    PY0, PH = 330, 200
    out.append(f'<text x="{LM}" y="{PY0-8}" font-size="14" font-weight="bold">Folded onto the 5-minute grid (seconds after a :00/:05 boundary) — where each event lands relative to the boundary</text>')

    def PX(sec):
        return LM + (W - LM - RM) * (sec / GRID_S)
    for sec in range(0, GRID_S + 1, 30):
        out.append(f'<line x1="{PX(sec):.1f}" y1="{PY0}" x2="{PX(sec):.1f}" y2="{PY0+PH}" stroke="#eee"/>')
        out.append(f'<text x="{PX(sec):.1f}" y="{PY0+PH+14}" font-size="10" text-anchor="middle">+{sec}s</text>')
    plane = [("bursts (start→end)", PY0 + 20), ("queue full", PY0 + 70), ("HDR push", PY0 + 100), ("LEGACY report", PY0 + 130), ("Rx check", PY0 + 160)]
    for name, y in plane:
        out.append(f'<text x="4" y="{y+4}" font-size="11">{name}</text>')
    for r in rows:
        if not r.get("burst_start_utc") or r.get("burst_s") is None:
            continue
        ph = r["phase_s"]
        end = ph + r["burst_s"]
        st = r.get("loss_stage", "no-burst")
        y = PY0 + 12 + (hash(r.get("file", "")) % 7) * 2
        if end <= GRID_S:
            out.append(f'<rect x="{PX(ph):.1f}" y="{y}" width="{max(2.0, PX(end)-PX(ph)):.1f}" height="4" fill="{colour.get(st, "#999")}" opacity="0.7"/>')
        else:
            out.append(f'<rect x="{PX(ph):.1f}" y="{y}" width="{max(2.0, PX(GRID_S)-PX(ph)):.1f}" height="4" fill="{colour.get(st, "#999")}" opacity="0.7"/>')
            out.append(f'<rect x="{PX(0):.1f}" y="{y}" width="{max(2.0, PX(end-GRID_S)-PX(0)):.1f}" height="4" fill="{colour.get(st, "#999")}" opacity="0.7"/>')
    import collections
    hist = collections.defaultdict(collections.Counter)
    for ts, kind, det in events:
        if kind in ("queue_full", "hdr_push", "legacy_report", "rx_check"):
            sec = int((ts - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() % GRID_S)
            hist[kind][sec // 5 * 5] += 1
    for kind, y, c in (("queue_full", PY0 + 70, "#e0a800"), ("hdr_push", PY0 + 100, "#5b8def"), ("legacy_report", PY0 + 130, "#c0392b"), ("rx_check", PY0 + 160, "#333")):
        mx = max(hist[kind].values() or [1])
        for sec, n in sorted(hist[kind].items()):
            h = max(2.0, 22.0 * n / mx)
            out.append(f'<rect x="{PX(sec):.1f}" y="{y+12-h:.1f}" width="{max(2.0, PX(5)-PX(0)):.1f}" height="{h:.1f}" fill="{c}"><title>{kind} +{sec}s: {n}</title></rect>')
        out.append(f'<text x="{W-RM}" y="{y+4}" font-size="10" text-anchor="end">max {mx}/5 s bin</text>')
    y = PY0 + PH + 34
    for line in notes:
        out.append(f'<text x="{LM}" y="{y}" font-size="11">{_svg_esc(line)}</text>')
        y += 15
    out.append("</svg>")
    Path(path).write_text("\n".join(out))


def backend_media(api, device_id):
    out = subprocess.run(["curl", "-s", "-m", "60", f"{api}/devices/{device_id}/media?page=1&page_size=72"],
                         stdout=subprocess.PIPE, check=False).stdout
    try:
        data = json.loads(out)
        # Staging caps page_size at 72 (200 returned a validation-error dict, and
        # iterating that dict crashed here). Anything but a list is an API error.
        if not isinstance(data, list):
            print(f"[WARN] backend media query for {device_id} failed: {str(data)[:200]}")
            return []
        return [m for m in data if m.get("type") == "video"]
    except ValueError:
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--camera-logs", type=Path, default=None)
    ap.add_argument("--console", nargs="*", default=[], type=Path)
    ap.add_argument("--sd-log", nargs="*", default=[], type=Path, help="Spotter SD log/ folder(s)")
    ap.add_argument("--sofar-json", type=Path, default=None, help="raw Sofar rows already dumped (scratch only)")
    ap.add_argument("--slice-hours", type=int, default=4)
    ap.add_argument("--run-tag", default="")
    ap.add_argument("--title", default="video_tx soak — camera glass to Sofar")
    ap.add_argument("--spotter-id", default="SPOT-33507C")
    ap.add_argument("--node-id", default="0xe6fe83ea6b4a2b7f")
    ap.add_argument("--device-id", default="BMCAM_004")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--api", default="https://nereus-vision-staging.onrender.com")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    rows = []
    if args.camera_logs is not None:
        for log in sorted(args.camera_logs.glob("rc_cycle_*.log")):
            row = parse_camera_log(log.read_text(errors="replace"))
            if row.get("stage") or row.get("file"):
                row["camera_log"] = log.name
                rows.append(row)
    print(f"camera cycles: {len(rows)}" + ("" if args.camera_logs else " (no --camera-logs: cycles come from the SD BM_TX.log)"))

    events = console_events(args.console)
    print(f"console events: {len(events)} from {len(args.console)} log(s)")

    sd_ev, bursts = [], {}
    if args.sd_log:
        sd_ev = sd_events(args.sd_log)
        subs = sd_submissions(args.sd_log)
        bursts = sd_bursts(subs)
        import collections
        kinds = collections.Counter(e[1] for e in sd_ev)
        print(f"SD events: {len(sd_ev)} {dict(kinds)}; BM_TX submissions: {len(subs)}; bursts: {len(bursts)}")
        by_file = {r.get("file"): r for r in rows if r.get("file")}
        for name, g in bursts.items():
            row = by_file.get(name)
            if row is None:
                row = {"file": name, "planned": g["length"]}
                rows.append(row)
            row.update(sd_submitted=g["submitted"], sd_unique=g["unique"], sd_max_index=g["max_index"],
                       sd_end=g["end"], sd_fmt=g["fmt"], sd_length=g["length"])
            row.update(sd_columns(sd_ev, g["t0"], g["t1"]))
        rows.sort(key=lambda r: r.get("burst_start_utc") or r.get("runtime_start_utc") or r.get("file") or "")

    groups = {}
    token = os.environ.get("SOFAR_API_TOKEN_BM_REEF")
    if args.sofar_json is not None:
        raw = json.loads(args.sofar_json.read_text())
        groups = sofar_groups(raw, args.node_id)
        print(f"Sofar rows from {args.sofar_json.name}: {len(raw)}; video groups: {len(groups)}")
        token = token or "from-json"
    elif token:
        groups = sofar_groups(fetch_sofar_sliced(args.spotter_id, args.start, args.end, token, args.slice_hours), args.node_id)
        print(f"Sofar video groups: {len(groups)}")
    else:
        print("SOFAR_API_TOKEN_BM_REEF not set: Sofar columns left EMPTY")

    media = backend_media(args.api, args.device_id)
    print(f"backend video rows: {len(media)}")

    for row in rows:
        name = row.get("file")
        m = RE_FILE.search(name or "")
        if name and name in groups:
            g = groups[name]
            missing = [i for i in range(g["length"]) if i not in g["got"]]
            row.update(sofar_unique=len(g["got"]), sofar_length=g["length"], sofar_missing=" ".join(map(str, missing[:40])),
                       sofar_end=g["end"], sofar_start_ts=g["start_ts"])
            if events and g["start_ts"] and not row.get("burst_start_utc"):
                t0 = datetime.strptime(g["start_ts"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                row.update(console_columns(events, t0, t0 + timedelta(seconds=row.get("uart_s", 200))))
        elif name and token:
            row["sofar_unique"] = 0                    # START never arrived: the group is invisible downstream
        if m:
            stamp = m.group(1)
            for item in media:
                # The backend names the object by MESSAGE time, so match on chunk count + proximity instead.
                if item.get("expected_chunks") == row.get("planned") and abs(
                        (datetime.fromisoformat(item["captured_at_utc"]) -
                         datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)).total_seconds()) < 900:
                    row.update(media_id=item["media_id"], backend_complete=item.get("is_complete"),
                               backend_received=item.get("received_chunks"),
                               backend_playable_s=item.get("duration_seconds"),
                               backend_received_at=item.get("received_at_utc"))
                    got = datetime.fromisoformat(item["received_at_utc"])
                    shot = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
                    row["glass_to_backend_s"] = round((got - shot).total_seconds())
                    break

    for row in rows:
        row["loss_stage"] = loss_stage(row)

    args.out.mkdir(parents=True, exist_ok=True)
    cols = ["file", "boot_to_runtime_s", "budget_msgs", "chunk_msgs", "budget_used_pct", "pass2_tries",
            "frames_trimmed", "phase_s", "lane_plan", "lane_wait_s", "lane_wait_skipped", "sent", "planned",
            "keyframe_repeated", "keyframe_chunks", "uart_s",
            "burst_start_utc", "burst_end_utc", "burst_s", "sd_fmt", "sd_length", "sd_submitted", "sd_unique",
            "sd_max_index", "sd_end", "spotter_queued", "queue_full_count_in_cycle", "spotter_queue_full",
            "queue_full_window", "hdr_in_burst", "report_utc", "report_kind", "report_offset_s", "rx_check_utc",
            "spotter_own_tx_in_burst", "sofar_unique", "sofar_length", "sofar_missing", "sofar_end", "loss_stage",
            "media_id", "backend_complete", "backend_received", "backend_playable_s", "glass_to_backend_s",
            "stage", "cycle_elapsed_s", "halt", "camera_errors", "camera_log"]
    with open(args.out / "cycles.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    brief = ["file", "phase_s", "burst_s", "sd_submitted", "sd_unique", "queue_full_count_in_cycle", "hdr_in_burst",
             "report_kind", "report_offset_s", "sofar_unique", "sofar_length", "loss_stage", "backend_complete"]
    lines = ["| " + " | ".join(brief) + " |", "|" + "---|" * len(brief)]
    lines += ["| " + " | ".join(str(r.get(c, "")) for c in brief) + " |" for r in rows]
    (args.out / "cycles.md").write_text("\n".join(lines) + "\n")

    # loss by stage: where does loss fall
    import collections
    stages = collections.Counter(r["loss_stage"] for r in rows)
    lost = collections.Counter()
    for r in rows:
        if r.get("sofar_unique") is not None and r.get("sofar_length"):
            lost[r["loss_stage"]] += max(0, r["sofar_length"] - r["sofar_unique"])
    video = [r for r in rows if r.get("sd_fmt", "h264") == "h264" and (r.get("sd_submitted") or r.get("sent"))]
    complete = sum(1 for r in video if r["loss_stage"] == "OK")
    tbl = [f"# loss by stage — {args.spotter_id} / {args.node_id} / {args.device_id} — run {args.run_tag}", "",
           f"video bursts: {len(video)}; complete at Sofar: {complete} ({100.0 * complete / max(1, len(video)):.0f} %)", "",
           "| stage | cycles | chunks lost at Sofar |", "|---|---|---|"]
    tbl += [f"| {k} | {v} | {lost.get(k, 0)} |" for k, v in stages.most_common()]
    coll = [r for r in video if r.get("report_kind")]
    tbl += ["", f"bursts with a LEGACY report inside/just before: {len(coll)} "
                f"(kinds: {dict(collections.Counter(k for r in coll for k in r['report_kind'].split()))})",
            f"bursts with an HDR push inside: {sum(1 for r in video if r.get('hdr_in_burst'))}"]
    (args.out / "loss_by_stage.md").write_text("\n".join(tbl) + "\n")

    if sd_ev:
        t_start = datetime.strptime(args.start, ISO).replace(tzinfo=timezone.utc)
        t_end = datetime.strptime(args.end, ISO).replace(tzinfo=timezone.utc)
        notes = [f"bursts coloured by loss_stage; report markers: hourly = the Spotter's hourly LEGACY report, health = the boot-anchored hourly health check alert, network = bridge topology change / cfg commit.",
                 f"video bursts {len(video)}, complete {complete}; chunks lost by stage {dict(lost)}; SD BM_TX.log can under-count submissions (Spotter logging queue drops lines under load)."]
        svg = args.out / f"timeline_{args.spotter_id}.svg"
        write_timeline_svg(svg, rows, sd_ev, title=args.title, run_tag=args.run_tag, spotter_id=args.spotter_id,
                           t_start=t_start, t_end=t_end, notes=notes)
        print(f"wrote {svg}")
    print(f"wrote {args.out / 'cycles.csv'}, cycles.md, loss_by_stage.md ({len(rows)} cycles; stages {dict(stages)})")


if __name__ == "__main__":
    main()
