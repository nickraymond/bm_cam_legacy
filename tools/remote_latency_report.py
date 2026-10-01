#!/usr/bin/env python3
# filename: remote_latency_report.py
# description: Sprint26 S6b (PLAN_S6 §5 step 3) — Sofar-lane command latency per hop: send log x Spotter console x backend -> CSV + summary.
"""
Remote (Sofar lane) command latency, one row per command sent.

Joins three records by command id:
  1. the send log (runs/sofar_command_sends.jsonl written by
     tools/sofar_send_command.py, or any JSONL with the same fields: ts|utc,
     spotter_id, message, http_status). t_post = when Sofar answered.
  2. the Spotter consoles (spotter_serial_monitor.py layout:
     <log-root>/<SPOT>/console_YYYYMMDD.log, "<monitor UTC> <spotter text>"):
       t_console  `[SYS] [INFO] Remote message received(N)! "bm pub bmcam/cmd {..."id":N..} 1 1`
                  (the Spotter fetched it from its cellular mailbox)
       t_answer   `[bmcamNNN] OK|REJECTED id=N ...` (the unit's console answer)
       for an rsd: the heal chunks the unit submitted to the cellular queue after
       its answer, decoded from the `[BM_TX] ... Submitted spotter/transmit-data`
       hex dumps; chunk prefix `<I{key}.{i}>` or W9 `<I{key}.{i}/{M}>`
       (t_chunks_first / t_chunks_last, chunks_on_console / chunks_asked), and the
       unit's `<HL ... id=N>` (t_hl_console).
     A line's time is the Spotter's own ms timestamp when the line carries one
     (ISO or unix epoch), else the monitor's receive time.
  3. the backend (optional; --api + --env-file with ADMIN_TOKEN, read-only GETs):
       t_ack      remote/service ids: /admin/devices/{d}/commands `ack.at`;
                  rsd ids: the first <HL> heal_status for the id in
                  /systems/{spot}/heal-events (backend times are the Sofar row
                  times, not when the backend polled them)
       t_media_complete  rsd ids: when every healed media was seen complete, from
                  the conductor's events.jsonl `complete` events (--conductor-events,
                  poll resolution ~60 s). The backend exposes no completion time
                  (media.last_received_at is not in any API yet); without the
                  conductor log only `media_complete` yes/no is filled.

Outputs (--out DIR, default runs/remote_latency_<UTC>/): latency.csv (one row per
command), summary.json (counts + p50/p95 per hop), run_manifest.json (inputs,
args). Also printed.

Example (nereus000, after a Sofar-lane session):
  python3 remote_latency_report.py --send-log ~/sofar_command_sends.jsonl \\
      --log-root /home/pi/spotter_logs --rig SPOT-33507C=BMCAM_003 \\
      --rig SPOT-31593C=BMCAM_004 --since 2026-10-01T00:00:00Z \\
      --conductor-events /home/pi/spotter_logs/conductor/<run>/events.jsonl
  add --api https://nereus-vision-staging.onrender.com (token: --env-file) for t_ack.

Known limitations: a command id re-sent through Sofar gives one row per send, all
matched to the FIRST console receive / answer after each post; console lines are
matched by id text only; the console log must cover the whole window (the monitor
writes one file per UTC day); a heal chunk submitted by a later, unrelated rsd for
the same key/index is counted for the earlier one if it falls inside --chunk-window-min.
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bm_bench_conductor import DEFAULT_API, Backend, parse_utc, pct, read_token  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEND_LOG = os.path.join(REPO_ROOT, "runs", "sofar_command_sends.jsonl")

RE_MONITOR = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) ?(.*)$")
RE_SPOTTER_ISO = re.compile(r"^\.?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z) ")
RE_SPOTTER_EPOCH = re.compile(r"^(?:Message: )?(\d{10}\.\d{1,3}) [0-9a-f]{16}, ")
RE_REMOTE_RX = re.compile(r'Remote message received\((\d+)\)! "(.*)$')
RE_ANSWER = re.compile(r"\[(bmcam\d+)\] (OK|REJECTED) id=(\d+)\b(.*)$")
RE_ID = re.compile(r'"id":(\d+)')
RE_VERB = re.compile(r'"c":"([a-z]+)"')
# chunk prefix: legacy keyed <I{key}.{i}> and W9 <I{key}.{i}/{M}> (DESIGN §10 O11)
RE_CHUNK = re.compile(r"<I([0-9a-z]{6})\.(\d+)(?:/(\d+))?>")
RE_HL = re.compile(r"<HL ([^>]*)>")
RE_HEX = re.compile(r"^((?:[0-9a-f]{2} ?)+)$")

COLUMNS = ["spotter_id", "device_id", "command_id", "verb", "http_status", "t_post",
           "t_console", "t_answer", "answer", "t_ack", "ack", "t_chunks_first",
           "t_chunks_last", "chunks_asked", "chunks_on_console", "t_hl_console",
           "media_ids", "media_complete", "t_media_complete",
           "post_to_console_s", "console_to_answer_s", "post_to_answer_s",
           "post_to_ack_s", "post_to_media_complete_s"]
HOPS = ["post_to_console_s", "console_to_answer_s", "post_to_answer_s", "post_to_ack_s",
        "post_to_media_complete_s"]


def iso(t):
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def line_time(line):
    """(unix time, spotter text) of one monitor line. The Spotter's own stamp wins."""
    m = RE_MONITOR.match(line)
    if not m:
        return None, line
    t, body = parse_utc(m.group(1)), m.group(2)
    s = RE_SPOTTER_ISO.match(body)
    if s:
        return parse_utc(s.group(1)), body
    e = RE_SPOTTER_EPOCH.match(body)
    if e:
        return float(e.group(1)), body
    return t, body


def expand_ranges(text):
    """'5,17,79-85' -> [5, 17, 79, ..., 85]."""
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def parse_console(lines):
    """Console lines -> {"remote_rx": [(t, msg)], "answers": [(t, host, ok, id, rest)],
    "cells": [(t, payload)]}. A cellular payload is the hex dump that follows a
    `Submitted spotter/transmit-data` line, timed by that line."""
    out = {"remote_rx": [], "answers": [], "cells": []}
    dump = None                                    # [t, bytearray]

    def flush():
        nonlocal dump
        if dump is not None:
            out["cells"].append((dump[0], bytes(dump[1]).decode("ascii", "replace")))
            dump = None

    for raw in lines:
        line = raw.rstrip("\n")
        t, body = line_time(line)
        text = body.strip()
        if dump is not None:
            h = RE_HEX.match(text)
            if h:
                dump[1].extend(int(x, 16) for x in h.group(1).split())
                continue
            if "[BM_TX] [DEBUG] Message:" in text:
                continue
            flush()
        if "Submitted spotter/transmit-data" in text:
            dump = [t, bytearray()]
            continue
        rx = RE_REMOTE_RX.search(text)
        if rx:
            out["remote_rx"].append((t, rx.group(2)))
            continue
        a = RE_ANSWER.search(text)
        if a:
            out["answers"].append((t, a.group(1), a.group(2) == "OK", int(a.group(3)),
                                   a.group(4).strip()))
    flush()
    return out


def console_files(log_root, spotter, since, until):
    day = dt.datetime.fromtimestamp(since, dt.timezone.utc).date()
    last = dt.datetime.fromtimestamp(until, dt.timezone.utc).date() + dt.timedelta(days=1)
    while day <= last:
        path = os.path.join(log_root, spotter, f"console_{day:%Y%m%d}.log")
        if os.path.exists(path):
            yield path
        day += dt.timedelta(days=1)


def read_sends(path, since, until, spotters):
    """Send log records in the window -> [{spotter_id, t_post, message, http_status, id, verb}]."""
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                print(f"[WARN] send log line {n}: not JSON, skipped")
                continue
            t = rec.get("ts") or parse_utc(rec.get("completed_at") or rec.get("utc")
                                          or rec.get("created_at"))
            msg = rec.get("message") or ""
            cid = RE_ID.search(msg)
            if t is None or cid is None or not (since <= float(t) <= until):
                continue
            if spotters and rec.get("spotter_id") not in spotters:
                continue
            verb = RE_VERB.search(msg)
            out.append({"spotter_id": rec.get("spotter_id"), "t_post": float(t), "message": msg,
                        "http_status": rec.get("http_status"), "id": int(cid.group(1)),
                        "verb": verb.group(1) if verb else None})
    return sorted(out, key=lambda r: r["t_post"])


def heal_asks(message):
    """rsd console line -> {(key, index)} it asks for."""
    try:
        body = json.loads(message[message.index("{"):message.rindex("}") + 1])
    except ValueError:
        return set()
    return {(h[0], i) for h in body.get("h") or [] if len(h) >= 2 for i in expand_ranges(h[1])}


def join_console(send, con, chunk_window_s):
    """Fill the console columns of one send row (pure)."""
    cid, t0 = send["id"], send["t_post"]
    row = {}
    rx = next((t for t, msg in con["remote_rx"]
               if t is not None and t >= t0 - 60 and RE_ID.search(msg)
               and int(RE_ID.search(msg).group(1)) == cid), None)
    row["t_console"] = rx
    start = rx if rx is not None else t0
    ans = next(((t, ok, rest) for t, _h, ok, i, rest in con["answers"]
                if i == cid and t is not None and t >= start - 1), None)
    if ans:
        row["t_answer"] = ans[0]
        err = re.search(r"\be=(\w+)", ans[2])
        row["answer"] = "OK" if ans[1] else f"REJECTED e={err.group(1) if err else '?'}"
    if send["verb"] == "rsd":
        asks = heal_asks(send["message"])
        row["chunks_asked"] = len(asks)
        after = row.get("t_answer") or start
        seen, times = set(), []
        for t, payload in con["cells"]:
            if t is None or t < after or t > after + chunk_window_s:
                continue
            m = RE_CHUNK.match(payload)
            if m and (m.group(1), int(m.group(2))) in asks:
                seen.add((m.group(1), int(m.group(2))))
                times.append(t)
            hl = RE_HL.match(payload)
            if hl and f"id={cid}" in hl.group(1).split() and "t_hl_console" not in row:
                row["t_hl_console"] = t
        row["chunks_on_console"] = len(seen)
        if times:
            row["t_chunks_first"], row["t_chunks_last"] = min(times), max(times)
    return row


def backend_acks(backend, device, spotter, since, until, log):
    """{command_id: (t_ack, detail, media_ids)} from the command log and heal events."""
    out = {}
    code, body = backend.call("GET", f"/admin/devices/{device}/commands?limit=500")
    if code == 200:
        for c in body.get("commands") or []:
            ack = c.get("ack")
            if ack and ack.get("at"):
                out[int(c["command_id"])] = (parse_utc(ack["at"]),
                                             "ok" if ack.get("ok") else f"e={ack.get('e')}", [])
    else:
        log(f"[WARN] {device}: GET commands -> HTTP {code}")
    start = iso(since - 3600)
    end = iso(min(until + 48 * 3600, time.time()))
    code, body = backend.call("GET", f"/systems/{spotter}/heal-events?device_id={device}"
                                     f"&start={start}&end={end}&sort=asc&limit=2000")
    if code != 200:
        log(f"[WARN] {spotter}: GET heal-events -> HTTP {code}")
        return out
    media = {}
    for ev in body.get("rows") or []:
        if ev.get("kind") == "heal_request" and ev.get("command_id") is not None:
            media[int(ev["command_id"])] = [h.get("media_id") for h in ev.get("heals") or []
                                            if h.get("media_id") is not None]
    for ev in body.get("rows") or []:
        cid = ev.get("command_id")
        if ev.get("kind") == "heal_status" and cid is not None and int(cid) not in out:
            out[int(cid)] = (parse_utc(ev.get("timestamp_utc")),
                             f"HL a={ev.get('action')} r={ev.get('reason')} n={ev.get('n')}", [])
    for cid, mids in media.items():
        t, detail, _ = out.get(cid, (None, None, []))
        out[cid] = (t, detail, mids)
    return out


def conductor_completions(path):
    """{media_id: unix time first seen complete} from a conductor events.jsonl."""
    out = {}
    if not path:
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "complete" and ev.get("media_id") is not None:
                out.setdefault(ev["media_id"], parse_utc(ev.get("utc")))
    return out


def finish_row(row, completions):
    """Media completion + the per-hop seconds (pure)."""
    mids = row.get("media_ids") or []
    if mids and completions:
        done = [completions.get(m) for m in mids]
        row["media_complete"] = all(t is not None for t in done)
        if row["media_complete"]:
            row["t_media_complete"] = max(done)

    def gap(a, b):
        return round(row[b] - row[a], 1) if row.get(a) is not None and row.get(b) is not None \
            else None
    row["post_to_console_s"] = gap("t_post", "t_console")
    row["console_to_answer_s"] = gap("t_console", "t_answer")
    row["post_to_answer_s"] = gap("t_post", "t_answer")
    row["post_to_ack_s"] = gap("t_post", "t_ack")
    row["post_to_media_complete_s"] = gap("t_post", "t_media_complete")
    return row


def summarize(rows):
    sent = [r for r in rows if r.get("http_status") == 202]
    out = {"sends": len(rows), "accepted_202": len(sent),
           "reached_console": sum(1 for r in sent if r.get("t_console") is not None),
           "answered": sum(1 for r in sent if r.get("t_answer") is not None),
           "backend_ack": sum(1 for r in sent if r.get("t_ack") is not None),
           "heals": sum(1 for r in sent if r.get("verb") == "rsd"),
           "heal_media_complete": sum(1 for r in sent if r.get("media_complete") is True)}
    for hop in HOPS:
        vals = [r[hop] for r in sent if r.get(hop) is not None]
        out[hop] = {"n": len(vals), "p50": pct(vals, 0.5), "p95": pct(vals, 0.95)}
    return out


def build_rows(sends, consoles, acks, completions, rigs, chunk_window_s):
    rows = []
    for s in sends:
        row = {"spotter_id": s["spotter_id"], "device_id": rigs.get(s["spotter_id"]),
               "command_id": s["id"], "verb": s["verb"], "http_status": s["http_status"],
               "t_post": s["t_post"]}
        if s["spotter_id"] in consoles:
            row.update(join_console(s, consoles[s["spotter_id"]], chunk_window_s))
        t_ack, detail, mids = acks.get((s["spotter_id"], s["id"]), (None, None, []))
        row.update(t_ack=t_ack, ack=detail, media_ids=mids)
        rows.append(finish_row(row, completions))
    return rows


def write_outputs(out_dir, rows, summary, manifest):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "latency.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (iso(v) if k.startswith("t_") else
                            ";".join(map(str, v)) if isinstance(v, list) else v)
                        for k, v in r.items()})
    for name, data in (("summary.json", summary), ("run_manifest.json", manifest)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--send-log", default=SEND_LOG)
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    ap.add_argument("--rig", action="append", default=[], help="SPOT-ID=DEVICE_ID (repeatable)")
    ap.add_argument("--since", required=True, help="UTC, e.g. 2026-10-01T00:00:00Z")
    ap.add_argument("--until", help="UTC (default now)")
    ap.add_argument("--conductor-events", help="conductor events.jsonl (media completion times)")
    ap.add_argument("--api", help=f"backend base URL for t_ack (e.g. {DEFAULT_API}); off if unset")
    ap.add_argument("--env-file", default=os.path.expanduser("~/.config/nereus/heal_driver.env"))
    ap.add_argument("--chunk-window-min", type=float, default=90.0,
                    help="look for an rsd's heal chunks this long after its answer (default 90)")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    since = parse_utc(args.since)
    until = parse_utc(args.until) if args.until else time.time()
    if since is None or until is None:
        ap.error("--since/--until must be ISO UTC")
    rigs = dict(r.split("=", 1) for r in args.rig)
    out_dir = args.out or os.path.join(REPO_ROOT, "runs", time.strftime(
        "remote_latency_%Y%m%dT%H%M%SZ", time.gmtime()))
    print(f"[LAT] send log {args.send_log}, consoles {args.log_root}, window "
          f"{iso(since)} .. {iso(until)}, rigs {rigs or 'all'}")

    sends = read_sends(args.send_log, since, until, set(rigs))
    print(f"[LAT] {len(sends)} send(s) in the window")
    consoles, sources = {}, []
    for spot in sorted({s["spotter_id"] for s in sends}):
        lines = []
        for path in console_files(args.log_root, spot, since, until + 2 * 86400):
            sources.append(path)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines.extend(fh)
        consoles[spot] = parse_console(lines)
        c = consoles[spot]
        print(f"[LAT] {spot}: {len(lines)} console lines, {len(c['remote_rx'])} remote rx, "
              f"{len(c['answers'])} answers, {len(c['cells'])} cellular payloads")

    acks = {}
    if args.api:
        backend = Backend(args.api, read_token(args.env_file))
        for spot, device in rigs.items():
            for cid, v in backend_acks(backend, device, spot, since, until, print).items():
                acks[(spot, cid)] = v
        print(f"[LAT] backend: {len(acks)} ack/heal record(s)")
    completions = conductor_completions(args.conductor_events)

    rows = build_rows(sends, consoles, acks, completions, rigs, args.chunk_window_min * 60)
    summary = summarize(rows)
    manifest = {"tool": "tools/remote_latency_report.py", "utc": iso(time.time()),
                "args": {k: v for k, v in vars(args).items() if k != "env_file"},
                "console_files": sources, "backend": bool(args.api)}
    write_outputs(out_dir, rows, summary, manifest)
    print(json.dumps(summary, indent=1))
    print(f"[LAT] wrote {out_dir}/latency.csv ({len(rows)} rows), summary.json, run_manifest.json")
    if not sends:
        print("[LAT][FAIL] no sends in the window: nothing to report")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
