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
    --camera-logs DIR   folder of rc_cycle_*.log pulled from the unit
    --console LOG ...   Spotter console log(s) from spotter_serial_monitor.py
    --spotter-id / --node-id / --device-id    e.g. SPOT-33507C / 0xe6fe… / BMCAM_004
    --start / --end     UTC ISO window for the Sofar pull
    --api               backend base URL (default: staging)
    --out DIR           writes cycles.csv + cycles.md
    SOFAR_API_TOKEN_BM_REEF in the environment (never on the CLI). Without it
    the Sofar columns are left empty and said so.

KNOWN LIMITATIONS
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


def backend_media(api, device_id):
    out = subprocess.run(["curl", "-s", "-m", "60", f"{api}/devices/{device_id}/media?page=1&page_size=200"],
                         stdout=subprocess.PIPE, check=False).stdout
    try:
        return [m for m in json.loads(out) if m.get("type") == "video"]
    except ValueError:
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--camera-logs", required=True, type=Path)
    ap.add_argument("--console", nargs="*", default=[], type=Path)
    ap.add_argument("--spotter-id", default="SPOT-33507C")
    ap.add_argument("--node-id", default="0xe6fe83ea6b4a2b7f")
    ap.add_argument("--device-id", default="BMCAM_004")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--api", default="https://nereus-vision-staging.onrender.com")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    rows = []
    for log in sorted(args.camera_logs.glob("rc_cycle_*.log")):
        row = parse_camera_log(log.read_text(errors="replace"))
        if row.get("stage") or row.get("file"):
            row["camera_log"] = log.name
            rows.append(row)
    print(f"camera cycles: {len(rows)}")

    events = console_events(args.console)
    print(f"console events: {len(events)} from {len(args.console)} log(s)")

    groups = {}
    token = os.environ.get("SOFAR_API_TOKEN_BM_REEF")
    if token:
        import count_complete_images as cci
        groups = sofar_groups(cci.fetch(args.spotter_id, args.start, args.end, token), args.node_id)
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
            row.update(sofar_unique=len(g["got"]), sofar_missing=" ".join(map(str, missing[:40])),
                       sofar_end=g["end"], sofar_start_ts=g["start_ts"])
            if events and g["start_ts"]:
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

    args.out.mkdir(parents=True, exist_ok=True)
    cols = ["file", "boot_to_runtime_s", "budget_msgs", "chunk_msgs", "budget_used_pct", "pass2_tries",
            "frames_trimmed", "phase_s", "lane_plan", "lane_wait_s", "lane_wait_skipped", "sent", "planned",
            "keyframe_repeated", "keyframe_chunks", "uart_s", "spotter_queued", "spotter_queue_full",
            "queue_full_window", "spotter_own_tx_in_burst", "sofar_unique", "sofar_missing", "sofar_end",
            "media_id", "backend_complete", "backend_received", "backend_playable_s", "glass_to_backend_s",
            "stage", "cycle_elapsed_s", "halt", "camera_errors", "camera_log"]
    with open(args.out / "cycles.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    brief = ["file", "chunk_msgs", "budget_used_pct", "phase_s", "lane_wait_s", "sent", "spotter_queued",
             "spotter_queue_full", "sofar_unique", "backend_complete", "glass_to_backend_s", "halt"]
    lines = ["| " + " | ".join(brief) + " |", "|" + "---|" * len(brief)]
    lines += ["| " + " | ".join(str(r.get(c, "")) for c in brief) + " |" for r in rows]
    (args.out / "cycles.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'cycles.csv'} and cycles.md ({len(rows)} cycles)")


if __name__ == "__main__":
    main()
