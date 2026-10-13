#!/usr/bin/env python3
# filename: pool_cmd.py
# description: Pool test 2026-10-13 — send one pool verb to one or both units via the Spotter console (cmd.txt) on nereus000 and wait for the OK/ERR.
"""
pool_cmd.py — the pool-test console driver (runs ON nereus000, next to spotter-monitor).

Purpose
  One command sends a pool verb to bmcam003, bmcam004 or both through the existing console
  path (spotter-monitor's <log-root>/<SPOT>/cmd.txt -> Spotter console -> `bm pub` ->
  BM bus -> the unit's command daemon), then waits (bounded) for the unit's answer
      [bmcamNNN] OK id=<N> pool <verb> ...      or      [bmcamNNN] ERR id=<N> pool <verb> <reason>
  in that Spotter's console log and prints it. Says clearly when nothing came back.

Wire (agreed with the Pi-side pool session 2026-10-12; console range ids 1..99999):
  console line   bm pub bmcam/cmd <json> 1 1          (same as hil/tools/hil_cmd.sh)
  json           {"id":N,"c":"pool","v":"<verb>", ...}  <= 248 B (command_wire.MAX_JSON_BYTES)
  verbs          status | cal [--duration-s S --interval-s S] | loop [--iterations N --period-s S]
                 | mark <note> | stop | preview [--lines 3-7,12]
                 | sweep  (focus sweep, lens 0.6..1.0, #160: answers OK when DONE after ~20-40 s, so
                           the default wait is 90 s; refused unless the unit is idle, i.e. not
                           during cal or the whole 3 h loop)
  mark note      spaces -> "_", charset [A-Za-z0-9_:./+,-], <= 40 chars ("card 2m" -> "card_2m")
  preview        without --lines: new capture, new img_id; with --lines: resend of the latest
                 preview's lines (inclusive, 1-based), <= 186 chars (max_lines_chars(): the 256 B
                 console line binds; JSON 248 B and the Pi's 200 are looser). PV lines are counted, not printed
                 (tools/pool_preview.py rebuilds the image).
  ids            persisted in --id-file (default <log-root>/pool_cmd_id), +1 per command, start
                 90001, flock'd; "both" uses ONE id for both units. A repeated id is a "dup" on
                 the unit (re-prints its old ack, does not re-run), so ids never go back.

Bus hold (pass-through, NOT reimplemented):
  pool_cmd.py bus-hold on  <unit|both>   -> hil/tools/hil_bus_always_on.sh <SPOT>
  pool_cmd.py bus-hold off <unit|both> [interval_ms window_ms] -> hil/tools/hil_restore_schedule.sh
  These need the repo's hil/ tree + hil/hil.env (HIL_MONITOR = nereus000's IP on the pool
  Wi-Fi) and ssh to the CAMERA, so run bus-hold from the Mac repo checkout, not on nereus000
  (nereus000 has no camera ssh key). Preconditions are printed before anything runs.

Inputs   target: bmcam003 | bmcam004 | both | SPOT-ID (only the --map Spotters; anything
         else is refused before a byte is written - the field-unit guard)
Outputs  stdout: what was sent, the unit answers; <log-root>/pool_cmd.log (append, UTC)
Exit     0 every target answered OK; 1 an ERR; 3 a cmd.txt not consumed (spotter-monitor down?);
         4 a target gave no answer within --wait; 2 usage / refused. Worst wins: 3 > 4 > 1.

Example (on nereus000)
  python3 /home/pi/pool_tools/pool_cmd.py both status
  python3 pool_cmd.py both cal --duration-s 600 --interval-s 10
  python3 pool_cmd.py bmcam003 mark "card 2m"
  python3 pool_cmd.py bmcam004 preview --lines 3-7,12
  python3 pool_cmd.py both stop --wait 30
  python3 pool_cmd.py both sweep                     # waits up to 90 s for each unit's OK
  python3 pool_cmd.py both loop --dry-run            # print the line + JSON, send nothing

Known limits
  - Sees only what the Spotter console prints: a unit that is halted / mid-boot answers
    nothing (exit 4); re-check the console log later, the command may still be queued.
  - One command line per cmd.txt: if a previous line is still unconsumed it waits up to
    10 s, then refuses (exit 3) rather than overwrite it.
  - The log offset is taken just before the write, so an answer printed by the unit before
    this tool started is not seen.
"""
import argparse
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import time

DEFAULT_MAP = "bmcam003=SPOT-33507C,bmcam004=SPOT-31593C"   # hil/hil.env.example HIL_RIG_A/B
ID_START, ID_MAX = 90001, 99999                                # console id range (command_wire.RANGES)
MAX_JSON_BYTES = 248
MAX_CONSOLE_LINE = 256       # hil_console.sh refuses > 256 B incl. newline (Spotter RX)
VERBS = ("status", "cal", "loop", "mark", "stop", "preview", "sweep")
# The unit answers `sweep` only when it is DONE (5 full-res stills at lens 0.6..1.0, ~20-40 s on the
# Pi, #160), so its default answer wait is longer. Every other verb answers at once.
DEFAULT_WAIT_S = 20.0
VERB_WAIT_S = {"sweep": 90.0}
RE_NOTE = re.compile(r"[A-Za-z0-9_:./+,-]{1,40}\Z")
RE_LINES = re.compile(r"\d+(-\d+)?(,\d+(-\d+)?)*\Z")
CONSOLE_PREFIX, CONSOLE_SUFFIX = "bm pub bmcam/cmd ", " 1 1"   # same line as hil/tools/hil_cmd.sh
PI_LINES_MAX = 200            # the Pi's rc_preview.RE_LINES = [0-9,-]{1,200} (#152 @ a23f4d6)


def max_lines_chars():
    """Longest preview `lines` value that passes EVERY limit with the worst-case 5-digit id:
    JSON <= 248 B, console line (+ newline) <= 256 B, the Pi's RE_LINES <= 200. = 186 (the
    256 B console line binds). tools/pool_preview.py computes the same number (tested)."""
    tpl = json.dumps({"id": ID_MAX, "c": "pool", "v": "preview", "lines": ""}, separators=(",", ":"))
    by_json = MAX_JSON_BYTES - len(tpl)
    by_console = MAX_CONSOLE_LINE - len(CONSOLE_PREFIX) - len(CONSOLE_SUFFIX) - 1 - len(tpl)
    return min(PI_LINES_MAX, by_json, by_console)


MAX_LINES_CHARS = max_lines_chars()
RE_PV = re.compile(r"(?<![A-Za-z0-9])PV[HE]?\s")


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Logger:
    def __init__(self, path):
        self.path = path

    def __call__(self, msg, quiet=False):
        line = f"{utc()} {msg}"
        if not quiet:
            print(msg, flush=True)
        try:
            with open(self.path, "a") as f:
                f.write(line + "\n")
        except OSError as exc:
            print(f"[pool_cmd] WARN cannot write {self.path}: {exc}", file=sys.stderr)


def die(msg):
    """Usage / refusal: loud, exit 2, nothing sent."""
    print(msg, file=sys.stderr, flush=True)
    sys.exit(2)


def parse_map(text):
    m = {}
    for pair in text.split(","):
        unit, _, spot = pair.partition("=")
        if unit and spot:
            m[unit.strip()] = spot.strip()
    return m


def resolve_targets(target, unit_map):
    """-> [(unit, SPOT)]; refuses anything outside the map (field-unit guard)."""
    if target == "both":
        return sorted(unit_map.items())
    if target in unit_map:
        return [(target, unit_map[target])]
    for u, s in unit_map.items():
        if s == target:
            return [(u, s)]
    die(f"[pool_cmd] REFUSED: {target!r} is not one of {sorted(unit_map)} / "
                     f"{sorted(unit_map.values())} (--map); nothing sent")


def next_id(path):
    """Persisted, monotonically increasing command id (flock'd read-increment-write)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        txt = f.read().strip()
        last = int(txt) if txt.isdigit() else ID_START - 1
        nid = max(last + 1, ID_START)
        if nid > ID_MAX:
            die(f"[pool_cmd] id {nid} would leave the console range 1..{ID_MAX}; "
                             f"reset {path} to a value unused by today's units (dedupe is by id)")
        f.seek(0)
        f.truncate()
        f.write(f"{nid}\n")
        f.flush()
        os.fsync(f.fileno())
    return nid


def clean_note(text):
    note = re.sub(r"\s+", "_", text.strip())
    if not RE_NOTE.match(note):
        die(f"[pool_cmd] mark note {note!r} must be [A-Za-z0-9_:./+,-]{{1,40}} "
                         f"(spaces become _)")
    return note


def build_json(cid, verb, a):
    body = {"id": cid, "c": "pool", "v": verb}
    if verb == "mark":
        if not a.note:
            die('[pool_cmd] mark needs a note, e.g. mark "card 2m"')
        body["note"] = clean_note(" ".join(a.note))
    if verb == "preview" and a.lines:
        if not RE_LINES.match(a.lines):
            die(f"[pool_cmd] --lines {a.lines!r}: use e.g. 3-7,12 (1-based, inclusive)")
        if len(a.lines) > MAX_LINES_CHARS:
            die(f"[pool_cmd] --lines is {len(a.lines)} chars; max {MAX_LINES_CHARS} (JSON 248 B, console "
                f"line 256 B, Pi RE_LINES 200): split it into several preview --lines commands")
        body["lines"] = a.lines
    if verb == "cal":
        if a.duration_s is not None:
            body["duration_s"] = a.duration_s
        if a.interval_s is not None:
            body["interval_s"] = a.interval_s
    if verb == "loop":
        if a.iterations is not None:
            body["iterations"] = a.iterations
        if a.period_s is not None:
            body["period_s"] = a.period_s
    for kv in a.arg or []:
        k, _, v = kv.partition("=")
        if not k or not _:
            die(f"[pool_cmd] --arg {kv!r}: use key=value")
        body[k] = int(v) if re.fullmatch(r"-?\d+", v) else v
    js = json.dumps(body, separators=(",", ":"))
    if "'" in js:
        die("[pool_cmd] single quote in the JSON (breaks the console line)")
    if len(js.encode()) > MAX_JSON_BYTES:
        die(f"[pool_cmd] JSON {len(js)} B > {MAX_JSON_BYTES} B limit: {js}")
    line = f"{CONSOLE_PREFIX}{js}{CONSOLE_SUFFIX}"
    if len(line) + 1 > MAX_CONSOLE_LINE:
        die(f"[pool_cmd] console line {len(line) + 1} B > {MAX_CONSOLE_LINE} B")
    return js, line


def console_log(log_root, spot):
    return os.path.join(log_root, spot, f"console_{time.strftime('%Y%m%d', time.gmtime())}.log")


def write_cmd(log_root, spot, line, log, consume_s=10.0):
    """Write the line to cmd.txt (after any pending one is consumed); -> True once consumed."""
    path = os.path.join(log_root, spot, "cmd.txt")
    if not os.path.isdir(os.path.dirname(path)):
        log(f"[{spot}] NO LOG DIR {os.path.dirname(path)} (spotter-monitor never saw this Spotter)")
        return False
    t0 = time.time()
    while os.path.exists(path) and os.path.getsize(path) > 0:
        if time.time() - t0 > consume_s:
            log(f"[{spot}] REFUSED: cmd.txt still holds an unsent line after {consume_s:.0f}s "
                f"(spotter-monitor down? `systemctl status spotter-monitor`); not overwriting")
            return False
        time.sleep(0.25)
    with open(path, "w") as f:
        f.write(line + "\n")
    t0 = time.time()
    while time.time() - t0 < consume_s:
        if os.path.getsize(path) == 0:
            return True
        time.sleep(0.25)
    log(f"[{spot}] NOT-CONSUMED: monitor did not pick up cmd.txt within {consume_s:.0f}s "
        f"(spotter-monitor down or the Spotter USB console is gone); the line is still in cmd.txt")
    return False


class LogFollower:
    """Read console lines appended after construction (handles the UTC-midnight rollover)."""

    def __init__(self, log_root, spot):
        self.log_root, self.spot = log_root, spot
        self.path = console_log(log_root, spot)
        self.pos = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        self.partial = ""

    def read_new(self):
        out = []
        cur = console_log(self.log_root, self.spot)
        paths = [self.path] if cur == self.path else [self.path, cur]
        for i, p in enumerate(paths):
            if not os.path.exists(p):
                continue
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.pos)
                data = self.partial + f.read()
                self.pos = f.tell()
            self.partial = ""
            lines = data.split("\n")
            self.partial = lines.pop()
            out += lines
            if i == 0 and len(paths) == 2:
                self.path, self.pos, self.partial = cur, 0, ""
        return out


def wait_answers(followers, cid, wait_s, linger_s, log, show_all):
    """followers: {unit: LogFollower}. -> {unit: (status, line)} status OK/ERR/None."""
    re_ack = re.compile(rf"\[(bmcam\d+)\]\s+(OK|ERR)\s+id={cid}(?!\d)")
    got = {u: (None, None) for u in followers}
    pv_count = {u: 0 for u in followers}
    t0 = time.time()
    done_at = None
    while True:
        for unit, fol in followers.items():
            for line in fol.read_new():
                if RE_PV.search(line):
                    pv_count[unit] += 1
                    continue
                if ", power |" in line:
                    continue
                m = re_ack.search(line)
                if m and got[unit][0] is None:
                    got[unit] = (m.group(2), line)
                    log(f"  [{unit}] {m.group(2)}  {line[line.find('[' + m.group(1)):][:220]}")
                elif re.search(r"\[bmcam\d+\]", line) or show_all:
                    log(f"  [{unit}] .. {line[:220]}")
        if done_at is None and all(s is not None for s, _ in got.values()):
            done_at = time.time()
        now = time.time()
        if done_at is not None and now - done_at >= linger_s:
            break
        if now - t0 >= wait_s:
            break
        time.sleep(0.3)
    for unit, n in pv_count.items():
        if n:
            log(f"  [{unit}] {n} preview line(s) (PVH/PV/PVE) seen - pool_preview.py rebuilds them")
    return got


def run_bus_hold(a, targets, log):
    hil = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hil", "tools")
    script = "hil_bus_always_on.sh" if a.state == "on" else "hil_restore_schedule.sh"
    path = os.path.join(hil, script)
    log(f"[bus-hold {a.state}] -> {path}")
    log("  PRECONDITIONS (the script refuses otherwise):")
    log("   - the unit is HALTED (ssh to it fails); a bridge commit power-cycles the bus")
    log("   - Nick's OK for bridge config changes; hil/hil.env has HIL_MONITOR = nereus000's IP")
    log("   - run from the Mac repo checkout: it ssh's to the monitor AND to the camera")
    if a.state == "on":
        log("   - on: catches the boot, backs up + disarms the @reboot crontab, holds the bus ON.")
        log("     RESTORE later: pool_cmd.py bus-hold off <unit> (and re-arm the crontab per its gate.log line)")
    else:
        log("   - off: unit's crontab must already be ARMED; restores controller 1 + interval/window,")
        log("     catches the stub-window boot and halts it")
    if not os.path.exists(path):
        log(f"[bus-hold] ERROR {path} not found: run this from a repo checkout with hil/ (the Mac), "
            f"not the nereus000 tool copy")
        return 2
    if a.dry_run:
        for _, spot in targets:
            log(f"  DRY-RUN would run: {path} {spot} {' '.join(a.extra)}")
        return 0
    rc = 0
    for unit, spot in targets:
        cmd = [path, spot] + (a.extra if a.state == "off" else [])
        log(f"[bus-hold {a.state}] {unit} ({spot}): {' '.join(cmd)}")
        r = subprocess.call(cmd)
        log(f"[bus-hold {a.state}] {unit} exit {r}")
        rc = rc or r
    return rc


def build_parser():
    ap = argparse.ArgumentParser(
        description="Send one pool verb to bmcam003/bmcam004/both via the Spotter console and wait for OK/ERR.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="bmcam003 | bmcam004 | both | SPOT-ID")
    ap.add_argument("verb", help=f"{' | '.join(VERBS)} | bus-hold")
    ap.add_argument("rest", nargs="*", help="mark: the note; bus-hold: on|off [interval_ms window_ms]")
    ap.add_argument("--lines", help="preview: resend these lines, e.g. 3-7,12")
    ap.add_argument("--duration-s", type=int, help="cal: total seconds")
    ap.add_argument("--interval-s", type=int, help="cal: seconds between captures")
    ap.add_argument("--iterations", type=int, help="loop: number of iterations")
    ap.add_argument("--period-s", type=int, help="loop: seconds per iteration")
    ap.add_argument("--arg", action="append", help="extra JSON field key=value (int if numeric); repeatable")
    ap.add_argument("--wait", type=float, default=None,
                    help=f"seconds to wait for OK/ERR (default {DEFAULT_WAIT_S:.0f}; sweep {VERB_WAIT_S['sweep']:.0f})")
    ap.add_argument("--linger", type=float, default=3.0, help="seconds to keep printing after the last answer")
    ap.add_argument("--log-root", default="/home/pi/spotter_logs", help="spotter-monitor log root")
    ap.add_argument("--id-file", default=None, help="persisted id counter (default <log-root>/pool_cmd_id)")
    ap.add_argument("--map", default=DEFAULT_MAP, help=f"unit=SPOT pairs (default {DEFAULT_MAP})")
    ap.add_argument("--show-all", action="store_true", help="print every console line during the wait")
    ap.add_argument("--dry-run", action="store_true", help="print the console line(s); send nothing, no id used")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.wait is None:
        a.wait = VERB_WAIT_S.get(a.verb, DEFAULT_WAIT_S)
    unit_map = parse_map(a.map)
    targets = resolve_targets(a.target, unit_map)
    log = Logger(os.path.join(a.log_root, "pool_cmd.log") if os.path.isdir(a.log_root)
                 else os.path.expanduser("~/pool_cmd.log"))

    if a.verb == "bus-hold":
        if not a.rest or a.rest[0] not in ("on", "off"):
            print("[pool_cmd] usage: pool_cmd.py <unit|both> bus-hold on|off [interval_ms window_ms]")
            return 2
        a.state, a.extra = a.rest[0], a.rest[1:]
        return run_bus_hold(a, targets, log)
    if a.verb not in VERBS:
        print(f"[pool_cmd] unknown verb {a.verb!r}; one of {', '.join(VERBS)}, bus-hold")
        return 2
    if a.lines and a.verb != "preview":
        print("[pool_cmd] --lines only goes with preview")
        return 2
    a.note = a.rest if a.verb == "mark" else None
    if a.rest and a.verb != "mark":
        print(f"[pool_cmd] unexpected arguments {a.rest} for {a.verb}")
        return 2

    if a.dry_run:
        js, line = build_json(ID_START, a.verb, a)
        for unit, spot in targets:
            print(f"DRY-RUN {unit} ({spot}) -> {os.path.join(a.log_root, spot, 'cmd.txt')}: {line}  "
                  f"({len(js)} B JSON; real id comes from the counter)")
        return 0

    build_json(ID_MAX, a.verb, a)          # validate first: a refused command burns no id
    id_file = a.id_file or os.path.join(a.log_root, "pool_cmd_id")
    cid = next_id(id_file)
    js, line = build_json(cid, a.verb, a)
    log(f"[pool_cmd] host={socket.gethostname()} id={cid} verb={a.verb} targets="
        f"{','.join(u + '/' + s for u, s in targets)} wait={a.wait:.0f}s")
    followers, unsent = {}, []
    for unit, spot in targets:
        fol = LogFollower(a.log_root, spot)
        log(f"[{unit}] SEND via {spot}/cmd.txt: {line}")
        if write_cmd(a.log_root, spot, line, log):
            followers[unit] = fol
        else:
            unsent.append(unit)
    if not followers:
        log(f"[pool_cmd] id={cid} nothing was sent (exit 3)")
        return 3
    got = wait_answers(followers, cid, a.wait, a.linger, log, a.show_all)
    spot_of = dict(targets)
    for unit, (status, _) in sorted(got.items()):
        if status == "OK":
            log(f"[pool_cmd] id={cid} {unit}: OK")
        elif status == "ERR":
            log(f"[pool_cmd] id={cid} {unit}: ERR (see the line above)")
        else:
            log(f"[pool_cmd] id={cid} {unit}: NO ANSWER within {a.wait:.0f}s (unit halted/booting, or the "
                f"answer comes later: grep 'id={cid}' {console_log(a.log_root, spot_of[unit])})")
    for unit in unsent:
        log(f"[pool_cmd] id={cid} {unit}: NOT SENT (cmd.txt, see above)")
    statuses = [s for s, _ in got.values()]
    if unsent:
        return 3
    if None in statuses:
        return 4
    return 1 if "ERR" in statuses else 0

if __name__ == "__main__":
    sys.exit(main())
