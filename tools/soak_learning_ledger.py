#!/usr/bin/env python3
# filename: soak_learning_ledger.py
# description: c_soak_learning (E4) — per-image ledger, per-wake table and cause classes from saved rows (offline, read-only).
"""
Build the soak-learning ledger (spec SPEC_c_soak_learning.md rev 2, must-do 2 + 3) from
rows already saved by tools/soak_learning_fetch.py plus the TE's pulled Spotter console logs.
No network access; reads files only; writes CSVs/JSON into the run folder.

Purpose
  One row per image (unit, key, capture time, chunks expected, first-send received, every rsd
  ask, every heal delivery, completion time or "missing at read-out", age, cause class,
  evidence pointer), one row per scheduled wake slot (feeds Q5), one row per rsd ask, and a
  count check (backend rows vs Sofar keys vs unit wakes).

Inputs
  <run-dir>/raw/sofar/SPOT-*.json      Sofar api/sensor-data rows (Spotter-UTC timestamps)
  <run-dir>/raw/backend/*.json         media lists, heal events (rsd asks + <HL>)
  --console-dir                        TE pull: SPOT-*/console_YYYYMMDD.log (nereus000 spotter-monitor)
  --cycle-log-dir (optional)           TE pull: <host>/rc_cycle_*.log (count check vs the unit's own logs)
  Run/event constants below = EXPECTATIONS.md (locked; sha256 in MANIFEST.md). Change them only
  through a new EXPECTATIONS hash.

Outputs (<run-dir>/)
  ledger.csv, wakes.csv, asks.csv, bus_windows.csv, countcheck.json, ledger_summary.json, logs/ledger.log

Assumptions (stated, from EXPECTATIONS.md §4)
  - First send = distinct chunk indices of a key with Sofar timestamp < capture + 50 min.
  - Chunks expected M = the unit's W9 total (<Ikey.n/M>), else START length, else backend.
  - Completion = Sofar timestamp of the row that first completes indices 0..M-1.
  - A stall signature = a run of >= 8 consecutive missing indices (~10 chunks lost per
    ~10.8 s Notecard stall at 1.0 s/msg).

Example
  python3 tools/soak_learning_ledger.py \
      --console-dir ~/NereusData/bm_cam_legacy/2026-10-17_console_logs \
      --readout 2026-10-17T22:13:00Z

Known limitations
  - Per-image fields come from Sofar, the backend and the Spotter console. The Pi rc_cycle logs
    (--cycle-log-dir, optional) feed only the count check.
  - Dead-zone timing uses the camera's last console submit as its halt time (the Pi's own
    halt line is not on the console).
"""

import argparse
import csv
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_RUN = Path.home() / "NereusData/bm_cam_legacy/2026-10-16_soak_learning"
UTC = timezone.utc
ET = timezone(timedelta(hours=-4))
FIRST_SEND_S = 50 * 60
WAKE_GAP_S = 15 * 60
FETCH_START = datetime(2026, 10, 14, 4, tzinfo=UTC)   # Sofar rows fetched from here (soak_learning_fetch.py --start)
STALL_RUN = 8


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


# --- EXPECTATIONS.md constants (lock v1) -------------------------------------------------
UNITS = {
    "BMCAM_003": {
        "spot": "SPOT-31593C", "host": "bmcam003",
        "run_start": T("2026-10-14T04:58:00Z"), "run_end": T("2026-10-16T19:15:43Z"),
        "t_off": T("2026-10-15T16:47:38Z"), "before_start": T("2026-10-14T21:40:24Z"),
        "stubs": [T("2026-10-14T21:38:20Z"), T("2026-10-15T16:47:38Z")],
    },
    "BMCAM_004": {
        "spot": "SPOT-33507C", "host": "bmcam004",
        "run_start": T("2026-10-14T04:58:00Z"), "run_end": T("2026-10-16T19:23:54Z"),
        "t_off": T("2026-10-15T16:37:13Z"), "before_start": T("2026-10-14T21:44:54Z"),
        "stubs": [T("2026-10-14T21:43:22Z"), T("2026-10-15T16:37:13Z")],
    },
}
# No production wakes: Wed hold + pool mode until each unit's production restore (E-b).
HOLD = {"BMCAM_003": (T("2026-10-14T17:09:15Z"), T("2026-10-14T21:40:24Z")),
        "BMCAM_004": (T("2026-10-14T17:09:15Z"), T("2026-10-14T21:44:54Z"))}
HEAL_SINCE = {"SPOT-31593C": T("2026-10-08T16:00:00Z"), "SPOT-33507C": T("2026-10-08T20:00:00Z")}
CAP_PER_DAY = 24

# --- Sofar message parsing ---------------------------------------------------------------
RE_CHUNK = re.compile(r"^<I([0-9a-z]{6})\.(\d+)(?:/(\d+))?>")
RE_KV = re.compile(r"(\w+)=([^\s,>]+)")
RE_START_FN = re.compile(r"filename: (\S+?)_image")
RE_START_LEN = re.compile(r"length: (\d+)")
RE_END_SENT = re.compile(r"sent_buffers: (\d+)")
RE_END_FN = re.compile(r"filename: (\S+?)_image")


def decode(hexval):
    try:
        return bytes.fromhex(str(hexval).strip()).decode("utf-8", "replace")
    except ValueError:
        return None


def load_sofar(run_dir, spot):
    rows = []
    for f in sorted(glob.glob(str(run_dir / f"raw/sofar/{spot}_*.json"))):
        for r in json.load(open(f))["body"]:
            rows.append((r["timestamp"], r.get("bristlemouth_node_id"), r.get("value"), Path(f).name))
    seen, out = set(), []
    for ts, node, val, fname in sorted(rows):
        if (ts, node, val) in seen:          # slice edges can repeat a row
            continue
        seen.add((ts, node, val))
        out.append({"ts": T(ts), "node": node, "text": decode(val) or "", "file": fname})
    return out


def parse_sofar(msgs, host):
    """-> per-key dict, WS list, HL list for one camera host (node chosen by hn=)."""
    nodes = Counter(m["node"] for m in msgs if f"hn={host}" in m["text"])
    node = nodes.most_common(1)[0][0] if nodes else None
    keys, ws, hl, other = {}, [], [], Counter()

    def K(k):
        return keys.setdefault(k, {"key": k, "chunks": defaultdict(list), "M": Counter(),
                                   "start": None, "end": None, "fn_time": None, "start_len": None,
                                   "sent_buffers": None, "fmt": None, "file": None})
    for m in msgs:
        if m["node"] != node:
            continue
        s = m["text"].strip()
        c = RE_CHUNK.match(s)
        if c:
            k = K(c.group(1))
            k["chunks"][int(c.group(2))].append(m["ts"])
            if c.group(3):
                k["M"][int(c.group(3))] += 1
            k["file"] = k["file"] or m["file"]
        elif s.startswith("<WS"):
            ws.append({"ts": m["ts"], **dict(RE_KV.findall(s))})
        elif s.startswith("<HL"):
            hl.append({"ts": m["ts"], **dict(RE_KV.findall(s)), "file": m["file"]})
        elif s.startswith("<START"):
            kv = dict(RE_KV.findall(s))
            if "key" in kv:
                k = K(kv["key"])
                k["start"] = m["ts"]
                k["fmt"] = kv.get("fmt")
                fn = RE_START_FN.search(s)
                k["fn_time"] = T(fn.group(1)) if fn else None
                ln = RE_START_LEN.search(s)
                k["start_len"] = int(ln.group(1)) if ln else None
            else:
                other["start_nokey"] += 1
        elif s.startswith("<END"):
            fn = RE_END_FN.search(s)
            sb = RE_END_SENT.search(s)
            if fn:
                t = T(fn.group(1))
                for k in keys.values():
                    if k["fn_time"] == t:
                        k["end"] = m["ts"]
                        k["sent_buffers"] = int(sb.group(1)) if sb else None
        else:
            other[s[:12]] += 1
    return node, keys, ws, hl, other


B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def key_time(key):
    sec = 0
    for ch in key:
        sec = sec * 36 + B36.index(ch)
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sec)


# --- Console parsing ---------------------------------------------------------------------
RE_MON = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) ")
RE_SPOT_TS = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z \[")
RE_EPOCH = re.compile(r" (\d{10})\.\d+ [0-9a-f]{16},")
RE_BUS = re.compile(r"Bridge bus power: ([01])")
RE_SUBMIT = re.compile(r"\[BM_TX\] \[INFO\] Submitted spotter/transmit-data")
RE_QFULL = re.compile(r"Queue MS_Q_CELLULAR_ONLY is full")
RE_RX = re.compile(r'Remote message received\(\d+\)! "bm pub bmcam/cmd (\{.*\})')
RE_ACK = re.compile(r"\[(bmcam\d{3})\] (OK|ERR\w*) id=(\d+)")
RE_HDR = re.compile(r"\[HDR\] \[INFO\] HDR Message \d+ added to queue")


RE_HEXLINE = re.compile(r"^(?:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)?\s+((?:[0-9a-f]{2}\s+)*[0-9a-f]{2})\s*$")   # monitor stamp + hex bytes
DUMP_BYTES = 160          # enough for a chunk header or a START's key= field


def load_console(console_dir, spot):
    """Spotter console events. `txmsg` = a [BM_TX] dump: printed only for a message the Spotter
    ACCEPTED into its cell queue (dumps == 'Submitted' lines; queue-full rejects are not dumped)."""
    ev = []
    for f in sorted(glob.glob(str(Path(console_dir) / spot / "console_*.log"))):
        name = f"{spot}/{Path(f).name}"
        dump = None
        with open(f, errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if dump is not None:
                    hx = RE_HEXLINE.match(line)
                    if hx and len(dump["b"]) < DUMP_BYTES:
                        dump["b"] += bytes.fromhex(hx.group(1).replace(" ", ""))
                        continue
                    if not RE_HEXLINE.match(line):
                        txt = dump.pop("b").decode("utf-8", "replace")
                        dump["text"] = txt
                        ev.append(dump)
                        dump = None
                    else:
                        continue
                if "[BM_TX] [DEBUG] Message:" in line:
                    sp = RE_SPOT_TS.search(line)
                    if sp:
                        dump = {"ts": T(sp.group(1) + "Z"), "kind": "txmsg", "where": f"{name}:{i}", "b": b""}
                    continue
                kind = None
                if RE_BUS.search(line):
                    kind = "bus" + RE_BUS.search(line).group(1)
                elif RE_SUBMIT.search(line):
                    kind = "submit"
                elif RE_QFULL.search(line):
                    kind = "qfull"
                elif RE_HDR.search(line):
                    kind = "hdr"
                elif "Remote message received" in line:
                    kind = "rx"
                elif RE_ACK.search(line):
                    kind = "ack"
                if not kind:
                    continue
                m = RE_MON.match(line)
                if not m:
                    continue
                # Spotter clock when the line carries it, then the mote's epoch, else the monitor's stamp.
                sp, ep = RE_SPOT_TS.search(line), RE_EPOCH.search(line)
                if sp:
                    ts = T(sp.group(1) + "Z")
                elif ep:
                    ts = datetime.fromtimestamp(int(ep.group(1)), UTC)
                else:
                    ts = T(m.group(1))
                e = {"ts": ts, "kind": kind, "where": f"{name}:{i}"}
                if kind == "rx":
                    r = RE_RX.search(line)
                    if not r:
                        continue
                    try:
                        cmd = json.loads(r.group(1))
                    except ValueError:
                        continue
                    e.update(id=int(cmd.get("id", -1)), c=cmd.get("c"))
                elif kind == "ack":
                    a = RE_ACK.search(line)
                    e.update(host=a.group(1), ok=a.group(2), id=int(a.group(3)))
                ev.append(e)
    ev.sort(key=lambda e: e["ts"])
    return ev


def bus_windows(ev):
    out, cur = [], None
    for e in ev:
        if e["kind"] == "bus1":
            if cur:
                out.append(cur)
            cur = {"on": e["ts"], "off": None, "submits": 0, "first_submit": None, "last_submit": None,
                   "qfull": 0, "hdr": 0, "rx": [], "ack": [], "where": e["where"]}
        elif e["kind"] == "bus0":
            if cur:
                cur["off"] = e["ts"]
                out.append(cur)
                cur = None
        elif cur:
            if e["kind"] == "submit":
                cur["submits"] += 1
                cur["first_submit"] = cur["first_submit"] or e["ts"]
                cur["last_submit"] = e["ts"]
            elif e["kind"] in ("qfull", "hdr"):
                cur[e["kind"]] += 1
            elif e["kind"] in ("rx", "ack"):
                cur[e["kind"]].append(e)
    if cur:
        out.append(cur)
    for i, w in enumerate(out):      # console gaps can drop a bus-off line
        nxt = out[i + 1]["on"] if i + 1 < len(out) else w["on"] + timedelta(minutes=15)
        w["off_eff"] = w["off"] or min(nxt, w["on"] + timedelta(minutes=15))
    return out


# --- Pi cycle logs (optional; the TE's pulled rc_cycle_*.log) -----------------------------
RE_PI_KEY = re.compile(r"^\[KEY\] media key ([0-9a-z]{6}) from Spotter UTC (\S+)")
RE_PI_SENT = re.compile(r"^\[KEY\] sent record: .*\((\d+) msgs")


def load_cycle_logs(cycle_dir, host):
    """-> list of {key, spotter_utc, msgs, file, cycle_end}. File names carry the Pi clock at log open
    (can be stale), so the wake is identified by the [KEY] line's Spotter UTC, never the file name."""
    out = []
    if not cycle_dir:
        return out
    for f in sorted(glob.glob(str(Path(cycle_dir) / host / "rc_cycle_*.log"))):
        rec = {"file": f"{host}/{Path(f).name}", "key": None, "spotter_utc": None, "msgs": None, "cycle_end": False}
        for line in open(f, errors="replace"):
            m = RE_PI_KEY.match(line)
            if m and not rec["key"]:
                rec["key"], rec["spotter_utc"] = m.group(1), T(m.group(2))
            m = RE_PI_SENT.match(line)
            if m:
                rec["msgs"] = int(m.group(1))
            if line.startswith("[RC] cycle end:"):
                rec["cycle_end"] = True
        out.append(rec)
    return out


# --- backend -----------------------------------------------------------------------------
def load_backend(run_dir, dev):
    media = json.load(open(run_dir / f"raw/backend/media_{dev}.json"))["body"]
    he = json.load(open(run_dir / f"raw/backend/heal_events_{dev}.json"))["body"]
    req, st = {}, []
    for s in he:
        for r in s["body"]["rows"]:
            if r["kind"] == "heal_request":
                req[int(r["command_id"])] = r
            elif r["kind"] == "heal_status":
                st.append(r)
    return media, req, st


def count_ranges(ranges):
    n = 0
    for part in str(ranges).split(","):
        if "-" in part:
            a, b = part.split("-")
            n += int(b) - int(a) + 1
        elif part.strip():
            n += 1
    return n


def runs_of_missing(missing):
    runs, cur = [], []
    for i in sorted(missing):
        if cur and i == cur[-1] + 1:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i]
    if cur:
        runs.append(cur)
    return runs


def cluster(ts_list, gap_s=WAKE_GAP_S):
    groups = []
    for t in sorted(ts_list):
        if groups and (t - groups[-1][-1]).total_seconds() <= gap_s:
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


# --- per-unit build ----------------------------------------------------------------------
def build_unit(dev, cfg, run_dir, console_dir, readout, log, cycle_dir=None):
    spot, host = cfg["spot"], cfg["host"]
    msgs = load_sofar(run_dir, spot)
    node, keys, ws, hl, other = parse_sofar(msgs, host)
    log(f"[{dev}] sofar rows={len(msgs)} node={node} keys={len(keys)} ws={len(ws)} hl={len(hl)} other={dict(other)}")
    media, req, st = load_backend(run_dir, dev)
    ev = load_console(console_dir, spot)
    bw = bus_windows(ev)
    acks = {e["id"]: e for e in ev if e["kind"] == "ack" and e.get("host") == host}
    unit_tx = defaultdict(lambda: {"idx": defaultdict(list), "M": Counter(), "start": None, "where": None})
    for e in ev:
        if e["kind"] != "txmsg":
            continue
        c = RE_CHUNK.match(e["text"])
        if c:
            u = unit_tx[c.group(1)]
            u["idx"][int(c.group(2))].append(e["ts"])
            if c.group(3):
                u["M"][int(c.group(3))] += 1
            u["where"] = u["where"] or e["where"]
        elif e["text"].startswith("<START"):
            kv = dict(RE_KV.findall(e["text"]))
            if "key" in kv:
                unit_tx[kv["key"]]["start"] = e["ts"]
                unit_tx[kv["key"]]["where"] = unit_tx[kv["key"]]["where"] or e["where"]
                fn = RE_START_FN.search(e["text"])
                unit_tx[kv["key"]]["fn_time"] = T(fn.group(1)) if fn else None
                unit_tx[kv["key"]]["fmt"] = kv.get("fmt")
    # keys the unit sent that Sofar never showed: add an empty Sofar record so they get a ledger row
    for kk, u in unit_tx.items():
        ut = u.get("fn_time") or key_time(kk)
        if kk not in keys and (u["M"] or u["start"]) and ut >= FETCH_START:
            keys[kk] = {"key": kk, "chunks": defaultdict(list), "M": Counter(u["M"]), "start": None, "end": None,
                        "fn_time": u.get("fn_time"), "start_len": None, "sent_buffers": None, "fmt": u.get("fmt"),
                        "file": None, "unit_only": True}
    rx = defaultdict(list)
    for e in ev:
        if e["kind"] == "rx":
            rx[e["id"]].append(e)
    log(f"[{dev}] console events={len(ev)} bus_windows={len(bw)} acks={len(acks)} rx_ids={len(rx)}")

    # backend media <-> key: heal requests carry (media_key, media_id); else capture second.
    mid_by_key = {}
    for r in req.values():
        for h in r.get("heals") or []:
            mid_by_key[h["media_key"]] = h["media_id"]
    by_mid = {m["media_id"]: m for m in media}
    by_cap = defaultdict(list)
    for m in media:
        if m.get("captured_at_utc"):
            by_cap[T(m["captured_at_utc"]).replace(microsecond=0)].append(m)

    def backend_row(k):
        if k["key"] in mid_by_key and mid_by_key[k["key"]] in by_mid:
            return by_mid[mid_by_key[k["key"]]], "heal_request.media_id"
        t = k["fn_time"] or key_time(k["key"])
        for d in (0, -1, 1, -2, 2, -3, 3):
            c = by_cap.get(t + timedelta(seconds=d))
            if c and len(c) == 1:
                return c[0], f"captured_at±{abs(d)}s"
        return None, "none"

    # asks (backend heal_commands) per key
    asks_by_key = defaultdict(list)
    ask_rows = []
    for cid, r in sorted(req.items()):
        sent = T(r["sent_at"]) if r.get("sent_at") else None
        rx_e = rx.get(cid, [])
        ack_e = acks.get(cid)
        # dead zone: the Spotter got it while the bus was on and after the camera's last submit
        dz, dz_why = "", ""
        for e in rx_e:
            w = next((w for w in bw if w["on"] <= e["ts"] < w["off_eff"]), None)
            if w is None:
                dz_why = dz_why or "rx while bus off (held for next wake)"
            elif w["last_submit"] and e["ts"] > w["last_submit"]:
                dz, dz_why = "yes", f"rx {iso(e['ts'])} after last submit {iso(w['last_submit'])}, bus off {iso(w['off_eff'])}"
            else:
                dz_why = dz_why or "rx during camera activity"
        row = {"unit": dev, "command_id": cid, "sent_at": iso(sent), "send_outcome": r.get("send_outcome"),
               "keys": ";".join(f"{h['media_key']}:{count_ranges(h['ranges'])}" for h in r.get("heals") or []),
               "chunks": r.get("chunks"), "spotter_rx": iso(rx_e[0]["ts"]) if rx_e else "",
               "rx_where": rx_e[0]["where"] if rx_e else "",
               "unit_ack": iso(ack_e["ts"]) if ack_e else "", "ack_where": ack_e["where"] if ack_e else "",
               "dead_zone": dz, "dead_zone_why": dz_why, "last_hl_action": r.get("last_hl_action")}
        ask_rows.append(row)
        for h in r.get("heals") or []:
            asks_by_key[h["media_key"]].append({"id": cid, "sent": sent, "n": count_ranges(h["ranges"]),
                                                "ranges": h["ranges"], "row": row})
    answered = {int(h["id"]) for h in hl if str(h.get("id", "")).isdigit()} | set(acks)
    for row in ask_rows:
        row["answered"] = row["command_id"] in answered
    hl_by_key = defaultdict(list)
    for h in hl:
        hl_by_key[h.get("key")].append(h)

    sent_times = sorted(T(r["sent_at"]) for r in req.values() if r.get("sent_at"))

    def asks_in_24h(t):
        return sum(1 for s in sent_times if t - timedelta(hours=24) < s <= t)

    # scheduled slots + wakes
    slots = []
    t = (cfg["run_start"] + timedelta(minutes=59)).replace(minute=0, second=0, microsecond=0)   # first :00 slot
    while t < cfg["run_end"]:
        h0, h1 = HOLD[dev]
        if not (h0 <= t < h1):
            slots.append(t)
        t += timedelta(hours=1)

    # ---- per-image rows
    rows = []
    for k in keys.values():
        cap = k["fn_time"] or key_time(k["key"])
        M = (k["M"].most_common(1)[0][0] if k["M"] else None) or k["start_len"]
        brow, how = backend_row(k)
        if not M and brow:
            M = brow.get("expected_chunks")
        idx_first = {n for n, ts in k["chunks"].items() if min(ts) < cap + timedelta(seconds=FIRST_SEND_S)}
        later = sorted((min(ts), n) for n, ts in k["chunks"].items() if n not in idx_first)
        heal_deliv = []
        for g in cluster([x[0] for x in later]):
            ns = [n for (tt, n) in later if g[0] <= tt <= g[-1]]
            heal_deliv.append((g[0], len(ns)))
        # completion
        have, done_at = set(), None
        for tt, n in sorted((min(ts), n) for n, ts in k["chunks"].items()):
            if M and n < M:
                have.add(n)
                if len(have) == M:
                    done_at = tt
                    break
        missing_first = sorted(set(range(M or 0)) - idx_first)
        bw_cap = next((w for w in bw if w["on"] - timedelta(minutes=2) <= cap <= w["off_eff"]), None)
        # accepted/not-accepted split only where the console saw this wake's burst (else unknown)
        u = unit_tx.get(k["key"]) if (bw_cap and bw_cap["submits"] > 0) else None
        acc_first = {n for n, ts in (u["idx"].items() if u else []) if min(ts) < cap + timedelta(seconds=FIRST_SEND_S)}
        if u and (idx_first - acc_first):
            u = None          # Sofar has a chunk the console never saw accepted: console gap mid-burst -> unknown
            acc_first = set()
        stall_runs = [r for r in runs_of_missing(missing_first) if len(r) >= STALL_RUN]
        # nearest scheduled slot / kind
        nearest = min(slots, key=lambda s: abs((cap - s).total_seconds())) if slots else None
        in_run = cfg["run_start"] <= cap < cfg["run_end"]
        if cap >= cfg["run_end"]:
            kind = "post_pull"
        elif cap < cfg["run_start"]:
            kind = "pre_run"
        elif any(abs((cap - s).total_seconds()) <= 300 for s in cfg["stubs"]):
            kind = "stub"
        elif HOLD[dev][0] <= cap < HOLD[dev][1]:
            kind = "pool_preview" if (k["fmt"] and k["fmt"] != "nrjxl") else "forced"
        elif -120 <= (cap - nearest).total_seconds() <= 600:
            kind = "production"
        else:
            kind = "unscheduled"
        asks = sorted(asks_by_key.get(k["key"], []), key=lambda a: a["id"])
        hls = sorted(hl_by_key.get(k["key"], []), key=lambda h: h["ts"])
        deadline = cap + timedelta(hours=24)
        le24 = bool(done_at and done_at <= deadline)
        complete_now = done_at is not None
        age_h = ((done_at or readout) - cap).total_seconds() / 3600
        prod_wakes_after = sum(1 for s in slots if cap + timedelta(minutes=30) < s < min(deadline, cfg["run_end"]))
        r = {
            "unit": dev, "spotter": spot, "media_key": k["key"],
            "media_id": brow["media_id"] if brow else "", "backend_match": how,
            "capture_utc": iso(cap), "capture_et": cap.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
            "kind": kind, "in_run": in_run,
            "in_bar": in_run and kind in ("production", "stub", "forced", "unscheduled"),
            "window": "all-day (no window)", "format": k["fmt"] or (brow or {}).get("format", ""),
            "chunks_expected": M or "", "end_sent_buffers": k["sent_buffers"] or "",
            "first_send_received": len(idx_first & set(range(M or 0))),
            "first_send_lost": len(missing_first),
            "first_send_stall_runs": len(stall_runs),
            "first_send_missing": _ranges(missing_first),
            "spotter_accepted_first": len(acc_first & set(range(M or 0))) if u else "",
            "spotter_not_accepted_first": (M - len(acc_first & set(range(M or 0)))) if (u and M) else "",
            "accepted_not_at_sofar_first": len((acc_first - idx_first) & set(range(M or 0))) if u else "",
            "unit_only": bool(k.get("unit_only")),
            "wake_bus_on": iso(bw_cap["on"]) if bw_cap else "", "wake_submits": bw_cap["submits"] if bw_cap else "",
            "wake_qfull": bw_cap["qfull"] if bw_cap else "", "wake_hdr_adds": bw_cap["hdr"] if bw_cap else "",
            "asks": " ".join(f"{a['id']}@{iso(a['sent']) or 'unsent:' + str(a['row']['send_outcome'])}:{a['n']}"
                             f"{'' if a['id'] in answered else '(unanswered)'}" for a in asks),
            "heal_deliveries": " ".join(f"{iso(tt)}:{n}" for tt, n in heal_deliv),
            "hl": " ".join(f"{h.get('a')}/{h.get('r')}/id{h.get('id')}/n{h.get('n')}@{iso(h['ts'])}" for h in hls),
            "completion_utc": iso(done_at), "complete_le_24h": le24,
            "missing_at_readout": not complete_now,
            "age_at_readout_h": round(age_h, 2) if not complete_now else "",
            "hours_to_complete": round(age_h, 2) if complete_now else "",
            "backend_is_complete": (brow or {}).get("is_complete", ""),
            "backend_pct": (brow or {}).get("percent_received", ""),
            "backend_expected": (brow or {}).get("expected_chunks", ""),
            "backend_received": (brow or {}).get("received_chunks", ""),
            "backend_ts": (brow or {}).get("timestamp_utc", ""),
            "prod_wakes_before_deadline": prod_wakes_after,
            "evidence": (f"sofar:{k['file']}#key={k['key']}" if k["file"] else "sofar:none")
                        + (f" backend:media/{brow['media_id']}" if brow else "")
                        + (f" console:{u['where']}" if u and u["where"] else ""),
        }
        r["_asks"], r["_hls"], r["_heal"] = asks, hls, heal_deliv
        rows.append(r)
    # backend rows with no Sofar key (count check)
    known = {r["media_id"] for r in rows if r["media_id"] != ""}
    orphans = [m for m in media if m["media_id"] not in known and m.get("captured_at_utc")
               and cfg["run_start"] <= T(m["captured_at_utc"]) < cfg["run_end"]]

    # ---- wake slots (Q5 + missed-wake rule)
    wakes = []
    for s in slots:
        win = (s - timedelta(minutes=5), s + timedelta(minutes=20))
        ws_hit = [w for w in ws if win[0] <= w["ts"] < win[1]]
        img = [r for r in rows if r["kind"] == "production" and win[0] <= T(r["capture_utc"]) < win[1]]
        b = next((w for w in bw if abs((w["on"] - s).total_seconds()) <= 120), None)
        seen = bool(ws_hit or img or (b and b["submits"] > 0))
        wakes.append({
            "unit": dev, "slot_utc": iso(s), "wake_seen": seen,
            "ws": iso(ws_hit[0]["ts"]) if ws_hit else "", "media_key": img[0]["media_key"] if img else "",
            "M": img[0]["chunks_expected"] if img else "", "first_send_lost": img[0]["first_send_lost"] if img else "",
            "first_send_received": img[0]["first_send_received"] if img else "",
            "stall_runs": img[0]["first_send_stall_runs"] if img else "",
            "bus_on": iso(b["on"]) if b else "", "bus_off": iso(b["off"]) if b else "",
            "submits": b["submits"] if b else "", "qfull": b["qfull"] if b else "",
            "hdr_adds": b["hdr"] if b else "", "bus_where": b["where"] if b else "",
        })
    prod_wakes = sorted(T(r["capture_utc"]) for r in rows if r["kind"] == "production")
    for r in rows:
        r.update(classify(r, cfg, prod_wakes, answered, asks_in_24h))
        for x in ("_asks", "_hls", "_heal"):
            r.pop(x)
    pi = load_cycle_logs(cycle_dir, host)
    pi_in = [x for x in pi if x["key"] and cfg["run_start"] <= x["spotter_utc"] < cfg["run_end"]]
    led_in = {r["media_key"] for r in rows if r["in_run"]}
    count_pi = {} if not cycle_dir else {
        "pi_cycle_logs": len(pi), "pi_logs_without_key": [x["file"] for x in pi if not x["key"]],
        "pi_image_wakes_in_run": len(pi_in),
        "pi_keys_without_ledger_row": [x["key"] + "@" + iso(x["spotter_utc"]) for x in pi_in if x["key"] not in led_in],
        "ledger_rows_without_pi_key": sorted(led_in - {x["key"] for x in pi_in}),
        "pi_wakes_without_cycle_end": [x["key"] + "@" + iso(x["spotter_utc"]) for x in pi_in if not x["cycle_end"]],
        "pi_msgs_vs_ledger_M": [x["key"] + f" pi={x['msgs']} M={r['chunks_expected']}" for x in pi_in for r in rows
                                if r["media_key"] == x["key"] and x["msgs"] and str(x["msgs"]) != str(r["chunks_expected"])],
    }
    count = {
        "unit": dev,
        **count_pi,
        "scheduled_slots": len(slots), "wakes_seen": sum(w["wake_seen"] for w in wakes),
        "missed_slots": [w["slot_utc"] for w in wakes if not w["wake_seen"]],
        "sofar_keys_in_run": sum(1 for r in rows if r["in_run"]),
        "sofar_keys_by_kind": dict(Counter(r["kind"] for r in rows)),
        "backend_rows_in_run": sum(1 for m in media if m.get("captured_at_utc")
                                   and cfg["run_start"] <= T(m["captured_at_utc"]) < cfg["run_end"]),
        "sofar_keys_in_run_matched_to_backend": sum(1 for r in rows if r["in_run"] and r["media_id"] != ""),
        "backend_rows_without_sofar_key": [{"media_id": m["media_id"], "captured_at_utc": m["captured_at_utc"],
                                            "format": m["format"], "pct": m.get("percent_received")} for m in orphans],
        "sofar_keys_without_backend": [r["media_key"] + "@" + r["capture_utc"] for r in rows
                                       if r["in_run"] and r["media_id"] == ""],
        "ws_in_run": sum(1 for w in ws if cfg["run_start"] <= w["ts"] < cfg["run_end"]),
        "console_bus_windows_in_run_with_submits": sum(1 for w in bw if cfg["run_start"] <= w["on"] < cfg["run_end"] and w["submits"] > 0),
        "backend_vs_sofar_disagreements": [
            {"key": r["media_key"], "sofar_M": r["chunks_expected"], "backend_expected": r["backend_expected"],
             "sofar_complete": not r["missing_at_readout"], "backend_complete": r["backend_is_complete"]}
            for r in rows if r["in_run"] and r["media_id"] != "" and (
                (r["backend_expected"] not in ("", None) and r["chunks_expected"] != r["backend_expected"])
                or (r["backend_is_complete"] is True) != (not r["missing_at_readout"]))],
    }
    buses = [{"unit": dev, "spotter": spot, "bus_on": iso(w["on"]), "bus_off": iso(w["off"]) if w["off"] else "",
              "off_inferred": w["off"] is None, "submits": w["submits"], "qfull": w["qfull"], "where": w["where"]}
             for w in bw]
    return rows, wakes, ask_rows, count, buses


def _ranges(xs):
    return ",".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in runs_of_missing(xs))


def classify(r, cfg, prod_wakes, answered, asks_in_24h):
    """EXPECTATIONS.md §4 decision order (lock v1). Returns cause_primary / cause_secondary / cause_note."""
    if not r["in_bar"]:
        return {"cause_primary": "", "cause_secondary": "", "cause_note": "not in the bar (" + r["kind"] + ")"}
    if r["complete_le_24h"]:
        return {"cause_primary": "", "cause_secondary": "", "cause_note": "complete <= 24 h"}
    asks, hls, heal = r["_asks"], r["_hls"], r["_heal"]
    cap = T(r["capture_utc"])
    deadline = cap + timedelta(hours=24)
    end = min(deadline, cfg["run_end"])
    wakes = [w for w in prod_wakes if cap + timedelta(minutes=30) < w < end]
    slots = [cap.replace(minute=0, second=0) + timedelta(hours=h) for h in range(1, 25)]
    missed = [s for s in slots if s < end and not any(abs((w - s).total_seconds()) <= 600 for w in prod_wakes)]
    holes = r["first_send_lost"]
    knew = r["media_id"] != ""
    usable = [a for a in asks if a["sent"] and any(w > a["sent"] for w in wakes)]   # a production wake followed
    cand = []
    # 1. C7: own wake was stub/forced/unscheduled and no ask reached it; >= 2 missed slots; pull cut it short
    if r["kind"] in ("stub", "forced", "unscheduled") and not any(a["id"] in answered for a in usable):
        cand.append(("C7", f"{r['kind']} wake; no answered ask before the deadline"))
    if len(missed) >= 2:
        cand.append(("C7", f"{len(missed)} scheduled wakes missed in its first 24 h (pool hold / outage): "
                           + ",".join(iso(x)[5:13] for x in missed[:6])))
    if deadline > cfg["run_end"] and len(wakes) < 3:
        cand.append(("C7", f"deadline after the pull ({iso(cfg['run_end'])}); {len(wakes)} production wakes left"))
    # 2. C5
    if cap < HEAL_SINCE[cfg["spot"]]:
        cand.append(("C5", "captured before heal_since"))
    # 3. C6
    bad = [h for h in hls if h.get("a") in ("dropped", "refused")]
    if bad:
        cand.append(("C6", "; ".join(f"<HL a={h.get('a')} r={h.get('r')} id={h.get('id')}>" for h in bad)))
    # 4. C3: an ask followed by a production wake, never answered (no <HL> with its id, no unit ack)
    lost = [a for a in usable if a["id"] not in answered]
    if lost:
        a0 = next((a for a in lost if a["row"]["dead_zone"] == "yes"), lost[0])
        why = a0["row"]["dead_zone_why"] or ("Spotter rx not seen in the console" if not a0["row"]["spotter_rx"] else "")
        cand.append(("C3", f"ask {a0['id']} sent {iso(a0['sent'])} never answered; {why}"))
    # 5. C2: ask side (backend knew it): never asked in time, or asks carried fewer holes than missing
    if knew:
        in_time = [a for a in usable]
        asked = sum(a["n"] for a in asks if a["sent"] and a["sent"] < end)
        if not in_time:
            peak = max((asks_in_24h(cap + timedelta(hours=h)) for h in range(25)), default=0)
            first = min((a["sent"] for a in asks if a["sent"]), default=None)
            cand.append(("C2", f"no ask before its last usable wake ({len(wakes)} production wakes after capture); "
                               f"first ask {iso(first) or 'never'}; Spotter asks/24 h peaked at {peak}/{CAP_PER_DAY}"))
        elif asked < holes:
            cand.append(("C2", f"asks before the deadline carried {asked} of {holes} holes"))
    # 6. C4: heals answered a=sent but the delivery came up short
    short = []
    for h in hls:
        if h.get("a") == "sent" and str(h.get("n", "")).isdigit():
            got = sum(n for (t, n) in heal if abs((t - h["ts"]).total_seconds()) <= 900)
            if got < int(h["n"]):
                short.append(f"id{h.get('id')} sent {h['n']} got {got}")
    if short:
        cand.append(("C4", "heal delivery short: " + "; ".join(short)))
    # secondary-only notes
    notes = []
    if r["first_send_stall_runs"]:
        notes.append(("C4", f"{r['first_send_stall_runs']} stall-signature run(s) in the first send (origin of holes)"))
    if r.get("accepted_not_at_sofar_first") not in ("", 0, None) and int(r["accepted_not_at_sofar_first"]) > 0:
        notes.append(("C8", f"{r['accepted_not_at_sofar_first']} chunks the Spotter accepted never reached Sofar"))
    if not knew:
        notes.append(("C8", "backend never saw this key (no Sofar rows), so no ask was possible"))
    if not cand:
        missing = [n for n, gone in (("Sofar rows", r["evidence"].startswith("sofar:none")),
                                      ("console burst", r.get("spotter_not_accepted_first") in ("", None))) if gone]
        if missing:   # spec §4 / A3: never guess a class when the evidence a rule needs is absent
            cand.append(("insufficient evidence", "no rule matched and missing: " + ", ".join(missing)))
        else:
            cand.append(("C8", "no rule matched: written description needed"))
    primary = cand[0][0]
    sec = sorted({c for c, _ in cand + notes} - {primary})
    note = " | ".join(f"{c}: {n}" for c, n in cand + notes)
    return {"cause_primary": primary, "cause_secondary": ",".join(sec), "cause_note": note}


def load_config(path):
    """Override the lock-v1 constants from a JSON file, so another soak reuses this builder.
    Keys: units {DEV: {spot, host, run_start, run_end, stubs[]}}, hold {DEV: [start, end] | null},
    heal_since {SPOT: iso}, fetch_start iso. Times are UTC ISO strings."""
    global FETCH_START
    cfg = json.loads(Path(path).read_text())
    UNITS.clear()
    for dev, u in cfg["units"].items():
        UNITS[dev] = {"spot": u["spot"], "host": u["host"], "run_start": T(u["run_start"]), "run_end": T(u["run_end"]),
                      "stubs": [T(x) for x in u.get("stubs", [])]}
    HOLD.clear()
    for dev in UNITS:
        h = (cfg.get("hold") or {}).get(dev)
        HOLD[dev] = (T(h[0]), T(h[1])) if h else (T("1970-01-01T00:00:00Z"), T("1970-01-01T00:00:00Z"))
    HEAL_SINCE.update({k: T(v) for k, v in (cfg.get("heal_since") or {}).items()})
    if cfg.get("fetch_start"):
        FETCH_START = T(cfg["fetch_start"])


def write_csv(path, rows):
    if not rows:
        path.write_text("")
        return
    cols = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the soak-learning ledger from saved rows (offline)")
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    ap.add_argument("--console-dir", type=Path, required=True)
    ap.add_argument("--readout", required=True, help="read-out instant (UTC ISO) for age_at_readout")
    ap.add_argument("--cycle-log-dir", type=Path, default=None,
                    help="TE pull of Pi rc_cycle logs: <dir>/<host>/rc_cycle_*.log (count check vs unit logs)")
    ap.add_argument("--config", type=Path, default=None,
                    help="JSON run config for another soak (default: the 10/14-10/16 lock-v1 constants)")
    a = ap.parse_args(argv)
    if a.config:
        load_config(a.config)
    run_dir = a.run_dir.expanduser()
    logp = run_dir / "logs/ledger.log"
    logp.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        line = f"{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
        print(line, flush=True)
        with logp.open("a") as fh:
            fh.write(line + "\n")
    exp = run_dir / "EXPECTATIONS.md"
    man = (run_dir / "MANIFEST.md").read_text()
    import hashlib
    h = hashlib.sha256(exp.read_bytes()).hexdigest()
    if h not in man:
        raise SystemExit(f"FAIL (A5): EXPECTATIONS.md sha256 {h} is not recorded in MANIFEST.md")
    log(f"[ledger] EXPECTATIONS sha256 {h} found in MANIFEST; readout={a.readout}")
    readout = T(a.readout)
    all_rows, all_wakes, all_asks, counts, all_bus = [], [], [], [], []
    for dev, cfg in UNITS.items():
        rows, wakes, asks, count, buses = build_unit(dev, cfg, run_dir, a.console_dir, readout, log, a.cycle_log_dir)
        all_bus += buses
        all_rows += sorted(rows, key=lambda r: r["capture_utc"])
        all_wakes += wakes
        all_asks += asks
        counts.append(count)
    write_csv(run_dir / "ledger.csv", all_rows)
    write_csv(run_dir / "wakes.csv", all_wakes)
    write_csv(run_dir / "asks.csv", all_asks)
    write_csv(run_dir / "bus_windows.csv", all_bus)
    (run_dir / "countcheck.json").write_text(json.dumps(counts, indent=1, default=str))
    bar = [r for r in all_rows if r["in_bar"]]
    summ = {
        "readout": a.readout,
        "per_unit": {dev: {
            "images_in_bar": sum(1 for r in bar if r["unit"] == dev),
            "complete_le_24h": sum(1 for r in bar if r["unit"] == dev and r["complete_le_24h"]),
            "not_complete_le_24h": sum(1 for r in bar if r["unit"] == dev and not r["complete_le_24h"]),
            "missing_at_readout": sum(1 for r in bar if r["unit"] == dev and r["missing_at_readout"]),
            "primary_causes": dict(Counter(r["cause_primary"] for r in bar if r["unit"] == dev and r["cause_primary"])),
        } for dev in UNITS},
    }
    (run_dir / "ledger_summary.json").write_text(json.dumps(summ, indent=1))
    log(f"[ledger] wrote ledger.csv ({len(all_rows)} rows), wakes.csv ({len(all_wakes)}), asks.csv ({len(all_asks)})")
    log(json.dumps(summ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
