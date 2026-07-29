#!/usr/bin/env python3
# filename: overnight_ab_runner.py
# description: Sprint10 overnight A/B — drive the roi sweep on both units and watch for failure.
"""
Sprint10 overnight A/B orchestrator (2026-07-29, Nick-directed).

WHAT IT DOES
Both camera units run themselves: the Spotter cycles bus power (20 min on /
10 min off), @reboot cron runs one capture cycle, the cycle halts the box,
the Spotter powers it back up. This script does NOT drive the cycles. It:

  1. watches each Spotter's USB console for the start of a cycle's
     pre-capture listen window,
  2. injects the next `roi` value of the sweep to BOTH units in that
     window (`bm pub bmcam/cmd`), same value to both,
  3. RE-SENDS until acked — the operational doctrine proven in the 07-27/28
     soak: a command is not "sent", it is *re-sent until acked*. Dedupe on
     the device makes re-sends free (D4),
  4. records every cycle, command, ack and error to a JSONL timeline,
  5. STOPS THE WHOLE RUN on a real failure and captures diagnosis, so the
     morning has a clean stop point rather than hours of garbage.

WHY roi ONLY (Nick 2026-07-29): the point of the night is proving the units
run unattended and accept field commands, not sweeping the command space.
All five roi presets are 16:9 and downsample to the same 1000x562 output, so
message counts stay comparable while the visible framing changes every
cycle — which makes "did the command land?" obvious on the website.

SWEEP ORDER is widest -> smallest by crop width, then repeat:
    1 (4608 full frame) -> 2 (3072) -> 3 (2304) -> 0 (1600) -> 4 (1000)

INPUTS
  --log-root     spotter monitor root (default ~/spotter_logs)
  --out          timeline JSONL (default runs/.../overnight_timeline.jsonl)
  --until-utc    hard stop, HH:MM UTC (default 14:00 = 07:00 PDT)
  --dry-run      print the plan and the first injection, send nothing

OUTPUTS
  <out>                     one JSON line per event
  <out>.summary.json        written on exit (clean or aborted)

FAILURE POLICY (the thing that matters at 3 a.m.)
  A unit missing ONE listen window is normal — it is logged, the same roi
  value is retried next cycle, and the run continues.
  The run ABORTS only on signals that mean the night is already wasted:
    - a unit produces N_DEAD_CYCLES consecutive cycles with no ack,
    - a unit stops appearing on its console for STALL_MIN minutes.
  On abort it writes the reason, the last console lines, and stops sending.
  It never power-cycles or reconfigures hardware on its own.

LIMITATIONS
  Console-only visibility of the device side; delivery is confirmed from the
  backend in the morning, not here. It does not read the Pis over SSH — the
  Pis are halted most of the time by design.
"""

import argparse
import json
import os
import re
import sys
import time

# Widest -> smallest by crop width (see ROI_TABLE in command_tables.py).
ROI_SWEEP = [1, 2, 3, 0, 4]
ROI_LABEL = {
    1: "widest (full frame 4608x2592)",
    2: "wide 3072x1728",
    3: "mid 2304x1296",
    0: "default 1600x900",
    4: "max detail 1000x562",
}

# Sprint11 (2026-07-29): same two units, roles renamed. Sprint10 called
# bmcam003 "arm B" and bmcam000 "arm A"; Sprint11 calls them Unit A (the
# candidate, C1-C4 on) and Unit B (the production-ish control). The letters
# are inverted between sprints, so `role` is the field to read -- never the
# letter alone.
UNITS = {
    "SPOT-33507C": {"unit": "bmcam003", "arm": "A", "role": "candidate",
                    "txd": 1.0},
    "SPOT-31593C": {"unit": "bmcam000", "arm": "B", "role": "control",
                    "txd": 5.0},
}

# Console signatures. The daemon prints to the PI's log, not the Spotter
# console, so we detect cycle activity from BM traffic on the console.
RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s")
# A node joining the bus = that unit just powered up and is booting.
RE_NEIGHBOR = re.compile(r"Neighbor ([0-9a-f]{16}) added")
# Uplink payloads are dumped as HEX on the console, never as text — verified
# 2026-07-29 (`grep -c '"ok":1'` over a full night of console = 0). So both
# the wake-status marker and the command ack have to be recovered by
# accumulating these hex lines and decoding them.
RE_TXDUMP = re.compile(r"\[BM_TX\].*Message:")
RE_HEXLINE = re.compile(r"^\s*(?:[0-9a-f]{2}\s+)+[0-9a-f]{2}\s*$")
RE_ACK = re.compile(r'\{"id":(\d+),"ok":(\d)')

# Timing of a cycle after its unit powers up.
#
# SPRINT10 (obsolete): boot ~35 s + cron settle 30 s + time-sync ~10 s ->
# the 90 s pre-capture listen window opened ~75-100 s in. Seven re-sends at
# 15 s covered it.
#
# SPRINT11: there IS no pre-capture listen window (C1/D2), and the settle
# dropped 30 s -> 0.5 s (D4). The daemon subscribes at ~40 s (boot ~35 s +
# settle + time-sync), and from then until the halt EVERY pacing slot pumps
# inbound commands. So the receptive window is no longer a 90 s slot -- it
# is essentially the whole cycle:
#     Unit A (1.0 s): subscribed ~40 s -> transmit ends ~256 s -> 150 s
#                     listen tail -> halt ~410 s after power-up
#     Unit B (5.0 s): subscribed ~40 s -> transmit ends ~1035 s -> halt
# Cover Unit A's whole window; Unit B's is strictly longer.
SEND_EARLIEST_S = 45      # never send sooner than this after power-up
SEND_INTERVAL_S = 20      # re-send spacing (dedupe makes re-sends free, D4)
SEND_MAX_TRIES = 18       # 45 + 18 x 20 s = 405 s, i.e. Unit A's full window

# Sprint11 C3 decouples "stop re-sending" from "give up on the ack": with
# deferred acks the ack cannot arrive until AFTER the image completes
# (~256 s on Unit A), and on Unit B a mid-burst ack can land as late as
# ~1035 s. Keeping the pending entry alive purely for ack-matching costs
# nothing and stops us recording a false cmd_no_ack for a command that was
# in fact applied -- which would corrupt metric 3.
ACK_WAIT_S = 1100      # keep matching acks this long after arming

N_DEAD_CYCLES = 6      # consecutive cycles with no ack on one unit -> abort
STALL_MIN = 45         # no console lines at all from a unit -> abort


def now():
    return time.time()


def utcstamp(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or now()))


class Timeline:
    """Append-only JSONL event log; flushed per line so a kill -9 keeps it."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.n = 0

    def add(self, kind, **fields):
        rec = {"utc": utcstamp(), "kind": kind, **fields}
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        self.n += 1
        print(f"[{rec['utc']}] {kind}: "
              + " ".join(f"{k}={v}" for k, v in fields.items()), flush=True)
        return rec


class ConsoleTail:
    """Follow one Spotter console log across daily rollover."""

    def __init__(self, log_root, spot):
        self.dir = os.path.join(log_root, spot)
        self.spot = spot
        self.fh = None
        self.path = None
        self.last_line_t = now()
        self._open_latest()

    def _latest(self):
        try:
            files = sorted(f for f in os.listdir(self.dir)
                           if f.startswith("console_"))
        except OSError:
            return None
        return os.path.join(self.dir, files[-1]) if files else None

    def _open_latest(self):
        p = self._latest()
        if p and p != self.path:
            if self.fh:
                self.fh.close()
            self.fh = open(p, "r", errors="replace")
            self.fh.seek(0, os.SEEK_END)   # only new lines
            self.path = p

    def lines(self):
        self._open_latest()
        if not self.fh:
            return
        while True:
            line = self.fh.readline()
            if not line:
                return
            self.last_line_t = now()
            yield line.rstrip()


def send_cmd(log_root, spot, payload):
    """Write one console command via the monitor's cmd.txt FIFO."""
    path = os.path.join(log_root, spot, "cmd.txt")
    with open(path, "w") as fh:
        fh.write(f"bm pub bmcam/cmd {payload} 1 1\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-root", default=os.path.expanduser("~/spotter_logs"))
    ap.add_argument("--out", default="runs/sprint10_overnight_20260729/"
                                     "overnight_timeline.jsonl")
    ap.add_argument("--until-utc", default="14:00")
    ap.add_argument("--id-base", type=int, default=3000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    hh, mm = (int(x) for x in args.until_utc.split(":"))
    tl = Timeline(args.out)
    tl.add("run_start", units=list(UNITS), sweep=ROI_SWEEP,
           until_utc=args.until_utc, dry_run=args.dry_run)

    if args.dry_run:
        for i, v in enumerate(ROI_SWEEP * 2):
            print(f"  cycle {i+1}: roi={v}  ({ROI_LABEL[v]})")
        print("# dry-run: nothing sent.")
        return 0

    tails = {s: ConsoleTail(args.log_root, s) for s in UNITS}
    state = {s: {"sweep_i": 0, "cycle": 0, "pending": None, "armed_t": 0.0,
                 "dead": 0, "sent_t": 0.0, "hex": None} for s in UNITS}
    next_id = args.id_base
    abort = None

    def decode_dump(chunks):
        try:
            return bytes.fromhex("".join(chunks)).decode("utf-8", "replace")
        except ValueError:
            return ""

    while abort is None:
        t = time.gmtime()
        if (t.tm_hour, t.tm_min) >= (hh, mm):
            tl.add("deadline_reached", until_utc=args.until_utc)
            break

        for spot, tail in tails.items():
            st = state[spot]
            for line in tail.lines():
                body = line.split("Z ", 1)[-1]

                # --- hex payload accumulation (acks + wake status) ---------
                if RE_TXDUMP.search(body):
                    st["hex"] = []
                    continue
                if st["hex"] is not None:
                    if RE_HEXLINE.match(body):
                        st["hex"].append(body.strip().replace(" ", ""))
                        continue
                    text = decode_dump(st["hex"])
                    st["hex"] = None
                    m = RE_ACK.search(text)
                    if m and st["pending"] and int(m.group(1)) == st["pending"]["id"]:
                        tl.add("ack", spot=spot, unit=UNITS[spot]["unit"],
                               id=int(m.group(1)), ok=int(m.group(2)),
                               roi=st["pending"]["roi"],
                               tries=st["pending"]["tries"])
                        st["dead"] = 0
                        st["pending"] = None
                    elif text.startswith("<WS") and st["pending"]:
                        # The a=cap wake status goes out right after the
                        # schedule gate, i.e. after time-sync and therefore
                        # after the daemon has subscribed. Under Sprint11 C1
                        # it no longer marks "the listen window is opening"
                        # (there is no such window) — it marks "this node is
                        # now receptive", which is the same cue for our
                        # purposes and still the most precise one we get.
                        st["armed_t"] = min(st["armed_t"], now() - SEND_EARLIEST_S)
                        tl.add("wake_status", spot=spot,
                               unit=UNITS[spot]["unit"],
                               note="node subscribed and receptive")

                # --- unit powered up -> arm the next sweep value -----------
                if RE_NEIGHBOR.search(body) and st["pending"] is None:
                    st["cycle"] += 1
                    value = ROI_SWEEP[st["sweep_i"] % len(ROI_SWEEP)]
                    st["sweep_i"] += 1
                    st["pending"] = {"id": next_id, "roi": value, "tries": 0}
                    st["armed_t"] = now()
                    st["sent_t"] = 0.0
                    next_id += 1
                    tl.add("cycle_detected", spot=spot,
                           unit=UNITS[spot]["unit"], cycle=st["cycle"],
                           roi=value, label=ROI_LABEL[value])

            # --- (re)send inside the listen window ------------------------
            p = st["pending"]
            if p:
                waited = now() - st["armed_t"]
                due = now() - st["sent_t"] > SEND_INTERVAL_S
                if waited >= SEND_EARLIEST_S and due and p["tries"] < SEND_MAX_TRIES:
                    payload = json.dumps(
                        {"id": p["id"], "c": "roi", "v": p["roi"]},
                        separators=(",", ":"))
                    send_cmd(args.log_root, spot, payload)
                    p["tries"] += 1
                    st["sent_t"] = now()
                    tl.add("cmd_sent", spot=spot, unit=UNITS[spot]["unit"],
                           id=p["id"], roi=p["roi"], attempt=p["tries"])
                elif p["tries"] >= SEND_MAX_TRIES and waited >= ACK_WAIT_S:
                    # Re-sending stopped a while ago; this is the point at
                    # which even a DEFERRED ack (C3) can no longer be in
                    # flight, so the silence is real.
                    tl.add("cmd_no_ack", spot=spot, unit=UNITS[spot]["unit"],
                           id=p["id"], roi=p["roi"], waited_s=round(waited),
                           note="no ack this cycle; sweep continues")
                    st["pending"] = None
                    st["dead"] += 1
                    if st["dead"] >= N_DEAD_CYCLES:
                        abort = (f"{spot}: {st['dead']} consecutive cycles "
                                 f"with no ack")

            # Console silence = the unit (or the USB link) is gone.
            quiet_min = (now() - tail.last_line_t) / 60.0
            if quiet_min > STALL_MIN:
                abort = f"{spot}: console silent for {quiet_min:.0f} min"

        time.sleep(2)

    if abort:
        tl.add("ABORT", reason=abort)
        print(f"\n[ABORT] {abort}", file=sys.stderr)

    summary = {
        "finished_utc": utcstamp(),
        "aborted": abort,
        "events": tl.n,
        "per_unit": {s: {"cycles": state[s]["cycle"],
                         "sweeps_completed": state[s]["sweep_i"],
                         "arm": UNITS[s]["arm"], "txd": UNITS[s]["txd"]}
                     for s in UNITS},
    }
    with open(args.out + ".summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))
    return 1 if abort else 0


if __name__ == "__main__":
    raise SystemExit(main())
