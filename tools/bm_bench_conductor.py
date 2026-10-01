#!/usr/bin/env python3
# filename: bm_bench_conductor.py
# description: Sprint26 S5 — bench conductor on nereus000: console trg -> backend media row -> next trg, with the heal step folded in; the 24 h loop gate.
"""
Bench conductor (Sprint26 S5 gate "24 h conductor loop, 0 lost clips";
KICKOFF R1/R5, D1: nereus000 drives, nobody in the loop).

Runs next to spotter_serial_monitor.py (same Pi, same log root), one thread per
rig (Spotter -> backend device). The units run stay_on, trigger-only
(mode.interval_s 0), transmit, on a HELD bus. Each cycle:

  1. heal step, EVERY cycle (S5 F10, 2026-09-29): the last heal command's media
     are checked (`healed` when all complete, else `heal_status`), then GET
     heal-candidates and POST heal-commands (the backend allocates the id and packs
     the CURRENT gaps; media still receiving chunks are not candidates). The unit keeps one
     pending heal per key and a newer rsd replaces it (command_state_v9
     record_heals), so a fresh command each cycle never duplicates work. The rsd
     is published BEFORE the trigger, so its chunks ride this cycle's action
     ("sent N heal chunk(s) before START"). The first 24 h run held one command
     for up to 3 cycles: ~1 heal per 90 min, slower than bmcam003's losses.
  2. trigger: `bm pub bmcam/cmd {"id":<conductor id>,"c":"trg","v":2} 1 1` through
     the monitor's cmd.txt; the unit's console answer "OK id=<id>" confirms it
     (re-published with the SAME id up to PUBLISH_TRIES times; the unit dedupes).
     Conductor ids: 2e9 + (unix seconds - ID_EPOCH): unique, console-only replies
     (DESIGN §6.2 "bench conductor" range, no high-water).
  3. wait for the media row at the backend: the first row of the device not yet
     claimed with captured_at_utc >= trigger - MATCH_SLACK_S (Sofar exposes rows
     11-30 min late; MAX_ROW_WAIT_S bounds the wait, then `no_row`).
  4. the next trigger no sooner than --min-interval after this one (cellular budget).

  5. --indoor-flush (BENCH ONLY, default off; Nick 2026-09-30, S5 F1): once per cycle,
     in the idle window between bursts, `note sync` on that Spotter's console when the
     Notecard looks backed up (see flush_decision). Indoors the weak cellular link is
     suspected to drain the Notecard slowly so MS_Q_CELLULAR_ONLY fills and drops chunks.
     A sync itself holds the queue full for up to ~40 s ("Attempting to Sync"), so it is
     never sent mid-burst or close to the next trigger. Not a production feature.

Console accounting (always on): every console line is classified (Notecard pct,
`Queue MS_Q_CELLULAR_ONLY is full.`, `Attempting to Sync.`, `Sync request sent
successfully`); each cycle's counts go in the ledger and a `console_cycle` event, so
the queue-full rate per Spotter and the flush effect come out of summary.json.

Every claimed media is re-read each cycle until complete. After --hours the
conductor stops triggering, keeps healing for --drain-min, then writes
summary.json and exits 0 (1 if any clip is lost). Lost = a trigger with no row,
or a row not complete at the end.

Nothing here talks to Sofar (console path only). The admin token is read from
--env-file (ADMIN_TOKEN=..., mode 600) and never logged. bm-heal-driver must be
STOPPED while this runs (two heal senders must never be live at once, KICKOFF §6).

Inputs
  --log-root     monitor log root (default /home/pi/spotter_logs)
  --rig          SPOT-ID=DEVICE_ID=HOST (repeatable), e.g. SPOT-33507C=BMCAM_003=bmcam003
  --run-dir      where events.jsonl / state.json / summary.json go
                 (default <log-root>/conductor/<UTC start>)
  --hours / --min-interval / --drain-min / --api / --env-file
  --indoor-flush [--flush-pct 10 --flush-after-min 8 --flush-lead-min 5]
  --report DIR   print the summary of a run directory and exit (no network)
Outputs (run dir): events.jsonl (one line per event), state.json (ledger),
  summary.json (per rig: triggers, rows, complete, healed, lost, latencies,
  queue-full per cycle, flushes and the first-try rate after a flush vs without).

Example (nereus000):
  python3 bm_bench_conductor.py --rig SPOT-33507C=BMCAM_003=bmcam003 \\
      --rig SPOT-31593C=BMCAM_004=bmcam004 --hours 24 --min-interval 30 [--indoor-flush]
systemd (Restart=no: a run is one 24 h experiment; resume is not supported):
  ExecStart=/usr/bin/python3 -u /home/pi/bm_bench_conductor.py --rig ... --hours 24

Known limitations: matching is by capture time, so a unit that also captures on
its own (interval_s > 0, a per_boot wake) would be mis-attributed; a restart
starts a new run (state is not resumed); console answers are recognised by the
text "OK id=<id>" only.
"""

import argparse
import collections
import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_API = "https://nereus-vision-staging.onrender.com"
ID_BASE = 2_000_000_000         # DESIGN §6.2: bench conductor range, console answers only
ID_EPOCH = 1_790_000_000        # 2026-09-21: keeps ids well below 2**32 for decades
ACK_WAIT_S = 30.0
PUBLISH_TRIES = 3
CMD_TXT_WAIT_S = 20.0           # the monitor consumes cmd.txt within ~1 s
MATCH_SLACK_S = 60.0            # captured_at is the Spotter clock; the trigger is ours
MAX_ROW_WAIT_S = 45 * 60
POLL_S = 60.0
DONE_REASONS = ("complete", "nothing_missing")

# --- indoor flush (bench only) ------------------------------------------------------
# Spotter console lines (runs/s5_console_20260928/pulled/*.txt, s2_bench console_commands.log):
#   "[MS] [DEBUG] Notecard is 6.000000 pct full."   printed per MS submission (not when idle)
#   "[MS] [ERROR] Queue MS_Q_CELLULAR_ONLY is full." one per rejected message (the paired
#       "Error adding message to queue." / "[BM_TX] Unable to submit ..." lines are NOT counted)
#   "[MS] [DEBUG] Attempting to Sync."               the Spotter's own sync; ~40 s of queue-full
#   "Sync request sent successfully to notecard"     the answer to our `note sync`
# Measured 2026-09-28: SPOT-33507C synced by itself mid-burst at 4-6 % (16:42, 16:43),
# SPOT-31593C at 20 % (18:56); one 185-chunk burst adds ~11 %; a forced sync drains to 2-3 %.
FLUSH_LINE = "note sync"
FLUSH_ACK = "Sync request sent successfully"
FLUSH_ACK_WAIT_S = 15.0
FLUSH_QUIET_S = 60.0            # no MS line for this long = the Spotter is not mid-burst


def classify_console_line(text):
    """One console line -> ("pct", float) | ("queue_full",) | ("sync_attempt",) |
    ("sync_requested",) | None. Pure; the timestamp prefixes are ignored."""
    if "Notecard is " in text and " pct full" in text:
        try:
            return ("pct", float(text.split("Notecard is ", 1)[1].split(" pct full", 1)[0]))
        except ValueError:
            return None
    if "Queue MS_Q_CELLULAR_ONLY is full" in text:
        return ("queue_full",)
    if "Attempting to Sync" in text:
        return ("sync_attempt",)
    if FLUSH_ACK in text:
        return ("sync_requested",)
    return None


def flush_decision(*, since_trigger_s, to_next_trigger_s, pct, queue_full_cycle, ms_age_s,
                   already, pct_threshold, after_s, lead_s, quiet_s=FLUSH_QUIET_S):
    """(flush?, reason). Pure. One flush per cycle, between bursts only:
    - not before `after_s` past this cycle's trigger (the burst, heal chunks first, is ~4-5 min),
    - not within `lead_s` of the next trigger (a sync holds the queue full for up to ~40 s),
    - not while the console still shows MS submissions (last MS line < quiet_s ago),
    - and only when the Notecard looks backed up: the last pct >= pct_threshold, or this
      cycle already had `is full` rejections."""
    if already:
        return False, "already_flushed_this_cycle"
    if since_trigger_s < after_s:
        return False, "burst_window"
    if to_next_trigger_s < lead_s:
        return False, "too_close_to_next_trigger"
    if ms_age_s is not None and ms_age_s < quiet_s:
        return False, "console_busy"
    if queue_full_cycle > 0:
        return True, "queue_full"
    if pct is not None and pct >= pct_threshold:
        return True, "pct"
    return False, "not_needed"


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text):
    """ISO-8601 (with or without fraction / offset) -> unix seconds, None if unparseable."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def conductor_id(now=None):
    """2e9 + seconds since ID_EPOCH: unique per second, inside the conductor range."""
    return ID_BASE + int((time.time() if now is None else now) - ID_EPOCH)


def match_row(rows, trigger_unix, claimed):
    """The earliest unclaimed row captured at/after the trigger (minus slack), or None."""
    best = None
    for row in rows:
        t = parse_utc(row.get("captured_at_utc"))
        if t is None or row.get("media_id") in claimed or t < trigger_unix - MATCH_SLACK_S:
            continue
        if best is None or t < parse_utc(best["captured_at_utc"]):
            best = row
    return best


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 1)


def summarize(ledger):
    """ledger: {device: [trigger records]} -> per-device summary (pure)."""
    out = {}
    for device, recs in ledger.items():
        rows = [r for r in recs if r.get("media_id") is not None]
        complete = [r for r in rows if r.get("complete_utc")]
        first_try = [r for r in complete if not r.get("healed_by")]
        to_row = [r["row_s"] for r in rows if r.get("row_s") is not None]
        to_done = [r["complete_s"] for r in complete if r.get("complete_s") is not None]
        lost = [r["trigger_id"] for r in recs if not r.get("complete_utc")]
        out[device] = {
            "triggers": len(recs),
            "acked": sum(1 for r in recs if r.get("acked")),
            "rows": len(rows),
            "no_row": sum(1 for r in recs if r.get("media_id") is None),
            "complete": len(complete),
            "complete_first_try": len(first_try),
            "healed": len(complete) - len(first_try),
            "lost": len(lost),
            "lost_trigger_ids": lost,
            "trigger_to_row_s_p50": pct(to_row, 0.5), "trigger_to_row_s_p95": pct(to_row, 0.95),
            "trigger_to_complete_s_p50": pct(to_done, 0.5),
            "trigger_to_complete_s_p95": pct(to_done, 0.95),
            **console_summary(recs),
        }
    return out


def console_summary(recs):
    """Queue-full per cycle and the flush comparison: cycles whose burst followed a flush
    vs the rest (first-try complete, queue-full during the cycle). Pure."""
    counted = [r for r in recs if r.get("console")]

    def group(rs):
        qf = [r["console"]["queue_full"] for r in rs]
        done = [r for r in rs if r.get("complete_utc") and not r.get("healed_by")]
        return {"cycles": len(rs), "complete_first_try": len(done),
                "queue_full_total": sum(qf),
                "queue_full_mean": round(sum(qf) / len(qf), 1) if qf else None}
    return {
        "queue_full_total": sum(r["console"]["queue_full"] for r in counted),
        "spotter_sync_attempts": sum(r["console"]["sync_attempts"] for r in counted),
        "flushes": sum(1 for r in recs if r.get("flush")),
        "after_flush": group([r for r in counted if r.get("flushed_before")]),
        "no_flush_before": group([r for r in counted if not r.get("flushed_before")]),
    }


def read_token(env_file):
    with open(env_file, "r", encoding="utf-8") as fh:
        for line in fh:
            key, _, value = line.strip().partition("=")
            if key == "ADMIN_TOKEN" and value:
                return value.strip().strip("'\"")
    raise SystemExit(f"ADMIN_TOKEN not found in {env_file}")


class Backend:
    def __init__(self, api, token):
        self.api = api.rstrip("/")
        self.token = token

    def call(self, method, path, body=None):
        """(status, json-or-text). Never raises on HTTP errors."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(text)
            except ValueError:
                return exc.code, text
        except Exception as exc:
            return 0, f"{type(exc).__name__}: {exc}"


class Console:
    """Tail of one Spotter's console log (UTC day rollover aware) + its cmd.txt."""

    def __init__(self, log_root, spotter):
        self.dir = os.path.join(log_root, spotter)
        self.lines = collections.deque(maxlen=5000)   # (monotonic, text)
        self.lock = threading.Lock()
        # cumulative counts (a burst is ~8000 console lines, more than `lines` keeps)
        self.counts = {"queue_full": 0, "sync_attempt": 0, "sync_requested": 0}
        self.pcts = collections.deque(maxlen=4000)    # (monotonic, Notecard pct)
        self.ms_mono = None                           # last MS pct / queue-full line

    def account(self, mono, text):
        """Classify one line into the counters (called by follow, and by tests)."""
        kind = classify_console_line(text)
        if kind is None:
            return
        with self.lock:
            if kind[0] == "pct":
                self.pcts.append((mono, kind[1]))
                self.ms_mono = mono
            else:
                self.counts[kind[0]] += 1
                if kind[0] == "queue_full":
                    self.ms_mono = mono

    def snapshot(self):
        """Counts now, the last pct and the ages (s) of the last pct / MS line."""
        now = time.monotonic()
        with self.lock:
            last = self.pcts[-1] if self.pcts else None
            return {**self.counts, "mono": now,
                    "pct": last[1] if last else None,
                    "pct_age_s": round(now - last[0], 1) if last else None,
                    "ms_age_s": round(now - self.ms_mono, 1) if self.ms_mono else None}

    def pcts_since(self, mono):
        """Notecard pct values seen at/after `mono`, oldest first."""
        with self.lock:
            return [v for t, v in self.pcts if t >= mono]

    def follow(self, stop):
        day, fh = None, None
        while not stop.is_set():
            today = time.strftime("%Y%m%d", time.gmtime())
            if today != day:
                try:
                    new = open(os.path.join(self.dir, f"console_{today}.log"), "r",
                               encoding="utf-8", errors="replace")
                except OSError:
                    time.sleep(1)
                    continue
                if fh is None:
                    new.seek(0, os.SEEK_END)
                else:
                    fh.close()
                day, fh = today, new
            line = fh.readline()
            if not line:
                time.sleep(0.3)
                continue
            mono, text = time.monotonic(), line.rstrip("\n")
            with self.lock:
                self.lines.append((mono, text))
            self.account(mono, text)

    def seen_since(self, mono, needle):
        with self.lock:
            return any(t >= mono and needle in text for t, text in self.lines)

    def publish(self, line):
        """Write one console line to cmd.txt once the monitor has consumed the last one."""
        path = os.path.join(self.dir, "cmd.txt")
        deadline = time.monotonic() + CMD_TXT_WAIT_S
        while time.monotonic() < deadline:
            try:
                if os.path.getsize(path) == 0:
                    break
            except OSError:
                break
            time.sleep(0.5)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(line + "\n")


class Conductor:
    def __init__(self, run_dir, backend, consoles, rigs, *, hours, min_interval_s, drain_s,
                 flush=None, clock=time.time, sleep=time.sleep, heal=True):
        self.run_dir = run_dir
        self.backend = backend
        self.consoles = consoles            # {spotter: Console}
        self.rigs = rigs                    # {spotter: (device, host)}
        self.hours = hours
        self.min_interval_s = min_interval_s
        self.drain_s = drain_s
        self.heal = heal                    # False (--no-heal): another heal sender is live
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.ledger = {dev: [] for dev, _ in rigs.values()}
        self.heals = {}                     # device -> outstanding heal command
        self.claimed = {dev: set() for dev, _ in rigs.values()}
        # --indoor-flush: None = off (default), else {"pct", "after_s", "lead_s"}
        self.flush = flush
        self.last_flush = {dev: None for dev, _ in rigs.values()}   # flush before the next trg
        os.makedirs(run_dir, exist_ok=True)

    # --- records ---------------------------------------------------------------
    def event(self, spotter, kind, **fields):
        rec = {"utc": utc_now(), "spotter": spotter, "device": self.rigs[spotter][0],
               "event": kind, **fields}
        line = json.dumps(rec, default=str)
        print(line, flush=True)
        with self.lock:
            with open(os.path.join(self.run_dir, "events.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def save(self):
        with self.lock:
            tmp = os.path.join(self.run_dir, "state.json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"ledger": self.ledger, "heals": self.heals}, fh, indent=1)
            os.replace(tmp, os.path.join(self.run_dir, "state.json"))

    # --- console commands ---------------------------------------------------------
    def send(self, spotter, cmd_id, json_text, what):
        """Publish until the unit answers "OK id=<id>" (same id each time). -> acked?"""
        console = self.consoles[spotter]
        for attempt in range(1, PUBLISH_TRIES + 1):
            mark = time.monotonic()
            console.publish(f"bm pub bmcam/cmd {json_text} 1 1")
            self.event(spotter, "publish", what=what, id=cmd_id, attempt=attempt)
            deadline = time.monotonic() + ACK_WAIT_S
            while time.monotonic() < deadline:
                if console.seen_since(mark, f"OK id={cmd_id} "):
                    self.event(spotter, "acked", what=what, id=cmd_id, attempt=attempt)
                    return True
                self.sleep(0.5)
        self.event(spotter, "no_ack", what=what, id=cmd_id)
        return False

    # --- heal step (bm_heal_driver.py rules, per cycle instead of per wake) ----------
    def media_status(self, media_ids):
        out = {}
        for mid in media_ids:
            code, body = self.backend.call("GET", f"/admin/ingest/media/{mid}/missing")
            if code == 200:
                out[mid] = f"missing {body.get('ranges')}"
            elif code == 409 and isinstance(body, dict):
                out[mid] = (body.get("detail") or {}).get("reason", "409")
            else:
                out[mid] = f"http {code}"
        return out

    def heal_step(self, spotter):
        device = self.rigs[spotter][0]
        cmd = self.heals.get(device)
        if cmd:
            status = self.media_status(cmd["media_ids"])
            done = all(r in DONE_REASONS for r in status.values())
            self.event(spotter, "healed" if done else "heal_status", id=cmd["command_id"],
                       media=status, cycles=cmd["cycles"])
        # S5 F10: a fresh command every cycle for the CURRENT gaps. The backend leaves
        # out media still receiving chunks (still_arriving), so bytes in flight are not
        # asked for twice; the unit keeps the newest heal per key.
        cmd = self.new_heal(spotter, device)
        self.heals[device] = cmd
        if cmd is None:
            return
        cmd["cycles"] += 1
        for mid in cmd["media_ids"]:
            for rec in self.ledger[device]:
                if rec.get("media_id") == mid:
                    rec.setdefault("healed_by", []).append(cmd["command_id"])
        json_text = cmd["console_line"][len("bm pub bmcam/cmd "):-len(" 1 1")]
        self.send(spotter, cmd["command_id"], json_text, "rsd")

    def new_heal(self, spotter, device):
        code, body = self.backend.call(
            "GET", f"/admin/ingest/devices/{device}/heal-candidates?hours=24")
        if code != 200:
            self.event(spotter, "backend_error", call="heal-candidates", status=code)
            return None
        cands = body.get("candidates") or []
        if not cands:
            return None
        self.event(spotter, "heal_candidates", n=len(cands),
                   items=[{"media_id": c["media_id"], "missing": c["ranges"]} for c in cands])
        code, body = self.backend.call("POST", f"/admin/ingest/devices/{device}/heal-commands", {})
        if code != 200:
            self.event(spotter, "backend_error", call="heal-commands", status=code)
            return None
        self.event(spotter, "heal_command", id=body["command_id"], heals=body["heals"],
                   chunks=body["chunks"])
        return {"command_id": body["command_id"], "console_line": body["console_line"],
                "media_ids": body["media_ids"], "cycles": 0}

    # --- backend media ------------------------------------------------------------
    def rows(self, device):
        code, body = self.backend.call("GET", f"/devices/{device}/media?page_size=50")
        return body if code == 200 and isinstance(body, list) else []

    def refresh(self, spotter, rows):
        """Mark claimed media complete when the backend says so."""
        device = self.rigs[spotter][0]
        by_id = {r.get("media_id"): r for r in rows}
        for rec in self.ledger[device]:
            row = by_id.get(rec.get("media_id"))
            if row is None or rec.get("complete_utc"):
                continue
            rec["received"], rec["expected"] = row.get("received_chunks"), row.get("expected_chunks")
            if row.get("is_complete"):
                rec["complete_utc"] = utc_now()
                rec["complete_s"] = round(self.clock() - rec["trigger_unix"], 1)
                self.event(spotter, "complete", trigger_id=rec["trigger_id"],
                           media_id=rec["media_id"], chunks=rec["expected"],
                           after_s=rec["complete_s"], healed_by=rec.get("healed_by"))

    # --- indoor flush (bench only) + console accounting --------------------------------
    def maybe_flush(self, spotter, rec, t0, snap0):
        """--indoor-flush: at most one `note sync` per cycle, between bursts (flush_decision)."""
        device = self.rigs[spotter][0]
        if self.flush is None or rec.get("flush"):
            return
        console = self.consoles[spotter]
        snap = console.snapshot()
        queue_full = snap["queue_full"] - snap0["queue_full"]
        now = self.clock()
        go, reason = flush_decision(
            since_trigger_s=now - t0, to_next_trigger_s=t0 + self.min_interval_s - now,
            pct=snap["pct"], queue_full_cycle=queue_full, ms_age_s=snap["ms_age_s"],
            already=False, pct_threshold=self.flush["pct"], after_s=self.flush["after_s"],
            lead_s=self.flush["lead_s"])
        if not go:
            return
        mark = time.monotonic()
        console.publish(FLUSH_LINE)
        deadline = time.monotonic() + FLUSH_ACK_WAIT_S
        requested = False
        while time.monotonic() < deadline:
            if console.seen_since(mark, FLUSH_ACK):
                requested = True
                break
            self.sleep(0.5)
        flush = {"reason": reason, "pct_before": snap["pct"], "pct_age_s": snap["pct_age_s"],
                 "queue_full_cycle": queue_full, "requested": requested,
                 "after_trigger_s": round(now - t0, 1),
                 "to_next_trigger_s": round(t0 + self.min_interval_s - now, 1)}
        rec["flush"] = flush
        self.last_flush[device] = {**flush, "mono": mark, "unix": now,
                                   "counts": {k: snap[k] for k in ("queue_full", "sync_attempt")}}
        self.event(spotter, "flush", trigger_id=rec["trigger_id"], **flush)

    def flush_effect(self, spotter, rec):
        """At the trigger after a flush: what the console showed between the flush and now."""
        device = self.rigs[spotter][0]
        last = self.last_flush.get(device)
        if last is None:
            return
        self.last_flush[device] = None
        snap = self.consoles[spotter].snapshot()
        pcts = self.consoles[spotter].pcts_since(last["mono"])
        effect = {"flush_reason": last["reason"], "pct_before": last["pct_before"],
                  "pct_first_after": pcts[0] if pcts else None,
                  "pct_last_before_trigger": pcts[-1] if pcts else None,
                  "sync_attempts_between": snap["sync_attempt"] - last["counts"]["sync_attempt"],
                  "queue_full_between": snap["queue_full"] - last["counts"]["queue_full"],
                  "flush_to_trigger_s": round(self.clock() - last["unix"], 1)}
        rec["flushed_before"] = effect
        self.event(spotter, "flush_effect", trigger_id=rec["trigger_id"], **effect)

    def console_cycle(self, spotter, rec, snap0):
        """End of a cycle: queue-full / sync counts and the Notecard pct since the trigger."""
        console = self.consoles[spotter]
        snap = console.snapshot()
        pcts = console.pcts_since(snap0["mono"])
        stats = {"queue_full": snap["queue_full"] - snap0["queue_full"],
                 "sync_attempts": snap["sync_attempt"] - snap0["sync_attempt"],
                 "pct_at_trigger": snap0["pct"], "pct_first": pcts[0] if pcts else None,
                 "pct_max": max(pcts) if pcts else None, "pct_last": pcts[-1] if pcts else None}
        rec["console"] = stats
        self.event(spotter, "console_cycle", trigger_id=rec["trigger_id"], **stats)

    # --- one rig -------------------------------------------------------------------
    def cycle(self, spotter):
        device = self.rigs[spotter][0]
        if self.heal:
            self.heal_step(spotter)
        t0 = self.clock()
        tid = conductor_id(t0)
        rec = {"trigger_id": tid, "trigger_utc": utc_now(), "trigger_unix": t0}
        self.ledger[device].append(rec)
        self.flush_effect(spotter, rec)
        snap0 = self.consoles[spotter].snapshot()
        rec["acked"] = self.send(spotter, tid, json.dumps(
            {"id": tid, "c": "trg", "v": 2}, separators=(",", ":")), "trg")
        while self.clock() - t0 < MAX_ROW_WAIT_S:
            rows = self.rows(device)
            self.refresh(spotter, rows)
            row = match_row(rows, t0, self.claimed[device])
            if row is not None:
                self.claimed[device].add(row["media_id"])
                rec.update(media_id=row["media_id"], captured_utc=row.get("captured_at_utc"),
                           row_s=round(self.clock() - t0, 1))
                self.event(spotter, "row", trigger_id=tid, media_id=row["media_id"],
                           received=row.get("received_chunks"),
                           expected=row.get("expected_chunks"), after_s=rec["row_s"])
                self.refresh(spotter, rows)
                break
            self.maybe_flush(spotter, rec, t0, snap0)
            self.sleep(POLL_S)
        else:
            self.event(spotter, "no_row", trigger_id=tid)
        self.save()
        while self.clock() - t0 < self.min_interval_s:
            self.refresh(spotter, self.rows(device))
            self.maybe_flush(spotter, rec, t0, snap0)
            self.sleep(POLL_S)
        self.console_cycle(spotter, rec, snap0)
        self.save()

    def run_rig(self, spotter, end_unix, drain_end_unix):
        device = self.rigs[spotter][0]
        self.event(spotter, "rig_start", host=self.rigs[spotter][1])
        while self.clock() < end_unix:
            try:
                self.cycle(spotter)
            except Exception as exc:     # a bad cycle must not end a 24 h run
                self.event(spotter, "cycle_error", error=f"{type(exc).__name__}: {exc}")
                self.sleep(POLL_S)
        self.event(spotter, "drain_start")
        while self.clock() < drain_end_unix and any(
                not r.get("complete_utc") and r.get("media_id") for r in self.ledger[device]):
            try:
                if self.heal:
                    self.heal_step(spotter)
                self.refresh(spotter, self.rows(device))
            except Exception as exc:
                self.event(spotter, "cycle_error", error=f"{type(exc).__name__}: {exc}")
            self.save()
            self.sleep(10 * POLL_S)
        self.save()
        self.event(spotter, "rig_done")

    def run(self):
        start = self.clock()
        end, drain_end = start + self.hours * 3600, start + self.hours * 3600 + self.drain_s
        threads = [threading.Thread(target=self.run_rig, args=(s, end, drain_end),
                                    name=f"rig-{s}") for s in self.rigs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        summary = summarize(self.ledger)
        with open(os.path.join(self.run_dir, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1)
        print(json.dumps(summary, indent=1), flush=True)
        return summary


def report(run_dir):
    with open(os.path.join(run_dir, "state.json"), "r", encoding="utf-8") as fh:
        ledger = json.load(fh)["ledger"]
    print(json.dumps(summarize(ledger), indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    ap.add_argument("--rig", action="append", help="SPOT-ID=DEVICE_ID=HOST (repeatable)")
    ap.add_argument("--run-dir")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--min-interval", type=float, default=30.0, help="minutes between triggers")
    ap.add_argument("--drain-min", type=float, default=120.0)
    ap.add_argument("--no-heal", action="store_true",
                    help="triggers only: another heal sender is live (Sofar lane, backend "
                         "auto-send, PLAN_S6 §9.14); never two heal senders at once")
    ap.add_argument("--env-file", default=os.path.expanduser("~/.config/nereus/heal_driver.env"))
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--report", metavar="RUN_DIR")
    ap.add_argument("--indoor-flush", action="store_true",
                    help="BENCH ONLY: `note sync` between bursts when the Notecard looks backed up")
    ap.add_argument("--flush-pct", type=float, default=10.0,
                    help="flush when the last 'Notecard is N pct full' is >= this (default 10)")
    ap.add_argument("--flush-after-min", type=float, default=8.0,
                    help="no flush sooner than this after the cycle's trigger (burst; default 8)")
    ap.add_argument("--flush-lead-min", type=float, default=5.0,
                    help="no flush within this of the next trigger (default 5)")
    args = ap.parse_args()
    if args.report:
        report(args.report)
        return 0
    if not args.rig:
        ap.error("--rig is required")
    rigs = {}
    for r in args.rig:
        spotter, device, host = r.split("=", 2)
        rigs[spotter] = (device, host)
    run_dir = args.run_dir or os.path.join(args.log_root, "conductor",
                                           time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    flush = None
    if args.indoor_flush:
        flush = {"pct": args.flush_pct, "after_s": args.flush_after_min * 60,
                 "lead_s": args.flush_lead_min * 60}
    consoles = {s: Console(args.log_root, s) for s in rigs}
    stop = threading.Event()
    for s, c in consoles.items():
        threading.Thread(target=c.follow, args=(stop,), name=f"follow-{s}", daemon=True).start()
    conductor = Conductor(run_dir, Backend(args.api, read_token(args.env_file)), consoles, rigs,
                          hours=args.hours, min_interval_s=args.min_interval * 60,
                          heal=not args.no_heal,
                          drain_s=args.drain_min * 60, flush=flush)
    for s in rigs:
        conductor.event(s, "conductor_start", run_dir=run_dir, hours=args.hours,
                        heal=not args.no_heal,
                        min_interval_min=args.min_interval, api=args.api, indoor_flush=flush)
    time.sleep(2)                        # let the followers reach the end of the logs
    summary = conductor.run()
    stop.set()
    return 1 if any(v["lost"] for v in summary.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
