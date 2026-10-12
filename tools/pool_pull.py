#!/usr/bin/env python3
# filename: pool_pull.py
# description: Pool test 2026-10-13 — pull both units' pool run folders to the Mac in parallel over Wi-Fi (rsync/ssh), strict priority, sha256-verified.
"""
pool_pull.py — after the pool test, copy each camera's run folder to the laptop, most
important files first, both units at the same time, every file sha256-checked against the Pi.

Purpose
  The pool sequence writes /home/pi/pool_runs/<YYYYMMDD_HHMMSS>_<host>/ on each Pi
  (calibration/, loop/, preview/, run_manifest.json, pool.log). Wi-Fi time at the pool is
  short, so per unit the pull goes in STRICT priority order, one class after another:
      1 manifest+logs   top-level files (run_manifest.json, pool.log, ...) and every *.log
      2 loop products   loop/ + preview/: *_compressed.* (nrjxl + pjpg), *_preview.jpg and the
                        small sidecars (*.json/.txt/.csv) ONLY (a few MB)
      3 calibration     calibration/ (DNG + JPEG, ~3.8 GB)
      4 loop natives    loop/ + preview/ native stills: *_still.jpg / *_still.dng (~0.6 GB)
      5 videos          *.mp4 / *.h264 anywhere
      6 other           anything else in the run folder (so nothing is silently left)
  Units run in parallel (one thread each); classes within a unit run in order.

How
  - ONE ssh per unit lists the run folder (python3 on the Pi: size + path) -> classes.
  - per class: rsync -a --partial --timeout --files-from (resumable: re-run the same command
    after a Wi-Fi drop and it continues), up to --retries attempts;
    then ONE remote sha256 call (python3 hashlib on the Pi) for that class's files, local
    sha256, compare;
    a mismatched file is deleted and re-pulled once.
  - ssh is bounded: BatchMode, ConnectTimeout, ServerAliveInterval/CountMax,
    StrictHostKeyChecking=accept-new (a NEW host key is accepted, a CHANGED one refuses).
  - live progress every --progress-s: per unit class, MB done/total, MB/s, files done/total.

Inputs
  --host  bmcam003.local  (repeatable; default bmcam003.local + bmcam004.local).
          "name=address" pins the unit name, e.g. --host bmcam003=192.168.4.23; a bare IP
          takes the unit name from the Pi's hostname.
  --run latest (default: newest <remote-root>/*_<hostname>/, else newest folder), a folder name,
          or per unit: --run bmcam003=<folder> --run bmcam004=<folder> (names come from --host
          name= or the .local name). A second run of the same unit + date needs its own --date
          (e.g. --date 20261013_run2): the dest folder is pinned to one run.
  --remote-root /home/pi/pool_runs     --user pi     --dest-root ~/NereusPool
Outputs
  <dest-root>/<date>/<unit>/...            mirror of the run folder (date = run folder date)
  <dest-root>/<date>/pull_report.json      per unit + per file: class, size, sha256 Pi vs Mac, status
  <dest-root>/<date>/pull.log              everything printed (append)
  <dest-root>/<date>/<unit>/.pool_pull/    rsync file lists + rsync logs + the run name pin
Exit     0 every requested file present + sha256 match; 1 anything missing / mismatched /
         a unit unreachable; 2 usage, or the dest already holds a DIFFERENT run for that unit.

Examples (on the Mac, repo root)
  python3 tools/pool_pull.py --dry-run                         # sizes per class + projected time
  python3 tools/pool_pull.py                                   # pull both, all classes
  python3 tools/pool_pull.py --classes 1,2                     # manifest/logs + nrjxl/pjpg only
  python3 tools/pool_pull.py --host bmcam004=192.168.4.31 --run 20261013_181500_bmcam004
  python3 tools/pool_pull.py --host bmcam003=172.20.10.4 --host bmcam004=172.20.10.5 \\
      --run bmcam003=20261013_170512_bmcam003 --run bmcam004=20261013_170530_bmcam004

Known limits
  - Needs key ssh to pi@<host> (BatchMode: a password prompt fails fast, it does not hang).
  - macOS ships rsync 2.6.9: works (no --info=progress2; progress is measured on the local
    folder instead). Filenames with spaces are not supported (the Pi writes none).
  - Pull AFTER the sequence stops: a file still being written fails the sha check (re-run).
  - The remote sha256sum of ~3 GB calibration takes ~1-2 min on a Pi Zero 2W (SD + CPU bound).
  - --dry-run's rate is a 16 MB /dev/zero probe over ssh per unit, both at once (shared Wi-Fi):
    real rsync of JPEG/DNG data on the Pi Zero is usually a bit slower.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

CLASSES = {1: "manifest+logs", 2: "loop products", 3: "calibration", 4: "loop natives", 5: "videos",
           6: "other"}
DEFAULT_HOSTS = ["bmcam003.local", "bmcam004.local"]
STATE_DIR = ".pool_pull"

# Remote helpers run with the Pi's python3 (stdlib): portable, so the tests can run them on the Mac.
# LIST: argv root, run ('latest' = newest *_<hostname>/ folder, else newest folder) ->
#       "HOST <hostname>", "RUN <dir>", then "F <size> <relpath>" per file; "NORUN" + exit 3.
REMOTE_LIST = r"""
import os, socket, sys
root, run = sys.argv[1].rstrip("/"), sys.argv[2]
host = socket.gethostname()
if run == "latest":
    dirs = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))) if os.path.isdir(root) else []
    mine = [d for d in dirs if d.endswith("_" + host)]
    run = (mine or dirs or [""])[-1]
d = os.path.join(root, run.strip("/"))
if not run or not os.path.isdir(d):
    print("NORUN", root); sys.exit(3)
print("HOST", host); print("RUN", d + "/")
for base, subdirs, files in os.walk(d):
    subdirs.sort()
    for fn in sorted(files):
        p = os.path.join(base, fn)
        if os.path.isfile(p) and not os.path.islink(p):
            print("F", os.path.getsize(p), os.path.relpath(p, d))
"""
# SHA: argv run dir; stdin = relpaths, one per line -> "<sha256>  <relpath>" (sha256sum format).
REMOTE_SHA = r"""
import hashlib, os, sys
os.chdir(sys.argv[1]); rc = 0
for rel in sys.stdin.read().splitlines():
    if not rel: continue
    try:
        h = hashlib.sha256()
        with open(rel, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
        print(h.hexdigest() + "  " + rel)
    except OSError as e:
        print("ERR", rel, e, file=sys.stderr); rc = 1
sys.exit(rc)
"""


def classify(rel):
    """Strict pull priority (Nick: JPEG XL + JPEG first; QE #151 nit 2026-10-12):
    1 top-level files + every *.log; 2 loop/preview PRODUCTS = *_compressed.* (nrjxl, pjpg),
    *_preview.jpg and the small sidecars (*.json/.txt/.csv); 3 calibration/; 4 loop/preview
    NATIVE stills (*_still.jpg/.dng, the preview's native capture); 5 videos; 6 anything else."""
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    if low.endswith((".mp4", ".h264")):
        return 5
    if "/" not in rel or low.endswith(".log"):
        return 1
    top = rel.split("/", 1)[0]
    if top == "calibration":
        return 3
    if top in ("loop", "preview"):
        if "_compressed" in name or name.endswith("_preview.jpg") or name.endswith((".json", ".txt", ".csv")):
            return 2
        if name.endswith((".jpg", ".jpeg", ".dng")):
            return 4
    return 6


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def hms(s):
    s = int(s)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Log:
    def __init__(self):
        self.lock = threading.Lock()
        self.f = None

    def open(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a")

    def __call__(self, msg):
        line = f"{datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')} {msg}"
        with self.lock:
            print(line, flush=True)
            if self.f:
                self.f.write(line + "\n")
                self.f.flush()


class Unit:
    """One camera: listing, per-class pull, verification, progress counters."""

    def __init__(self, spec, a, log):
        name, _, addr = spec.partition("=")
        if not addr:
            addr, name = spec, ""
        self.addr = addr
        self.name = name or (addr[:-6] if addr.endswith(".local") else "")
        self.a, self.log = a, log
        self.target = f"{a.user}@{addr}"
        self.ssh_opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={a.connect_timeout}",
                         "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
                         "-o", "StrictHostKeyChecking=accept-new", "-o", "Compression=no"]
        self.run_dir = None
        self.files = {}          # rel -> {"size", "class"}
        self.results = {}        # rel -> per-file report
        self.error = None
        self.phase = None        # current class number
        self.dest = None
        self.done_bytes_prev = None
        self.rate = 0.0
        self.rate_probe = None

    # --- remote

    def ssh(self, cmd, timeout, stdin=None):
        return subprocess.run(["ssh"] + self.ssh_opts + [self.target, cmd], input=stdin,
                              capture_output=True, text=True, timeout=timeout)

    def run_spec(self):
        """--run values: 'latest' | NAME (all units) | unit=NAME (this unit). unit= wins."""
        bare = "latest"
        for v in self.a.run or ["latest"]:
            unit, sep, name = v.partition("=")
            if sep and unit == self.name:
                return name
            if not sep:
                bare = v
        return bare

    def list_remote(self):
        self.run_req = self.run_spec()
        cmd = "python3 -c " + shlex.quote(REMOTE_LIST) + " " + shlex.quote(self.a.remote_root) + " " + \
            shlex.quote(self.run_req)
        try:
            r = self.ssh(cmd, timeout=self.a.connect_timeout + 60)
        except subprocess.TimeoutExpired:
            raise RuntimeError("listing timed out")
        if "NORUN" in r.stdout:
            raise RuntimeError(f"no run folder under {self.a.remote_root} (run={self.run_req})")
        if r.returncode != 0:
            raise RuntimeError(f"ssh {self.target} failed rc={r.returncode}: {r.stderr.strip()[:300]}")
        for line in r.stdout.splitlines():
            if line.startswith("HOST "):
                host = line[5:].strip()
                if not self.name:
                    self.name = host
                elif host != self.name:
                    self.log(f"[{self.name}] WARN Pi hostname is {host!r}, not {self.name!r} (check --host)")
            elif line.startswith("RUN "):
                self.run_dir = line[4:].strip().rstrip("/") + "/"
            elif line.startswith("F "):
                _, size, rel = line.split(" ", 2)
                self.files[rel] = {"size": int(size), "class": classify(rel)}
        if not self.run_dir:
            raise RuntimeError("listing returned no RUN line")
        if not self.files:
            raise RuntimeError(f"run folder {self.run_dir} is EMPTY")

    def run_name(self):
        return os.path.basename(self.run_dir.rstrip("/"))

    def run_date(self):
        m = re.match(r"(\d{8})_", self.run_name())
        return m.group(1) if m else datetime.now().strftime("%Y%m%d")

    def class_files(self, cls):
        return sorted(r for r, f in self.files.items() if f["class"] == cls)

    def class_bytes(self, cls):
        return sum(self.files[r]["size"] for r in self.class_files(cls))

    def probe_rate(self, mb):
        n = int(mb * 1024 * 1024)
        t0 = time.time()
        p = subprocess.run(["ssh"] + self.ssh_opts + [self.target, f"head -c {n} /dev/zero"],
                           capture_output=True, timeout=self.a.connect_timeout + 120)
        dt = time.time() - t0
        if p.returncode != 0 or len(p.stdout) != n:
            raise RuntimeError(f"rate probe failed rc={p.returncode} got {len(p.stdout)} B")
        self.rate_probe = n / dt
        return self.rate_probe

    # --- local

    def local_size(self, rel):
        try:
            return os.path.getsize(os.path.join(self.dest, rel))
        except OSError:
            return -1

    def local_bytes_in_flight(self):
        """Sum of every file under dest (completed + rsync temp files), skipping our state dir."""
        total = 0
        for root, dirs, files in os.walk(self.dest):
            if STATE_DIR in dirs:
                dirs.remove(STATE_DIR)
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
        return total

    # --- pull

    def rsync(self, rels, tag):
        state = os.path.join(self.dest, STATE_DIR)
        lst = os.path.join(state, f"class{tag}.list")
        with open(lst, "w") as f:
            f.write("\n".join(rels) + "\n")
        ssh_cmd = "ssh " + " ".join(self.ssh_opts)
        cmd = ["rsync", "-a", "--partial", f"--timeout={self.a.io_timeout}", f"--files-from={lst}",
               "-e", ssh_cmd, f"{self.target}:{self.run_dir}", self.dest.rstrip("/") + "/"]
        rc = None
        for attempt in range(1, self.a.retries + 1):
            with open(os.path.join(state, f"rsync_class{tag}.log"), "a") as lf:
                lf.write(f"\n# {datetime.now().isoformat()} attempt {attempt}: {' '.join(cmd)}\n")
                lf.flush()
                rc = subprocess.call(cmd, stdout=lf, stderr=subprocess.STDOUT)
            if rc == 0:
                return 0
            self.log(f"[{self.name}] class {tag}: rsync attempt {attempt}/{self.a.retries} exit {rc} "
                     f"(see {STATE_DIR}/rsync_class{tag}.log); "
                     f"{'retrying (resumes)' if attempt < self.a.retries else 'giving up'}")
            time.sleep(3)
        return rc

    def remote_sha(self, rels):
        cmd = "python3 -c " + shlex.quote(REMOTE_SHA) + " " + shlex.quote(self.run_dir)
        r = self.ssh(cmd, timeout=self.a.sha_timeout, stdin="\n".join(rels) + "\n")
        out = {}
        for line in r.stdout.splitlines():
            m = re.match(r"([0-9a-f]{64})\s+\*?(.+)$", line)
            if m:
                out[m.group(2)] = m.group(1)
        if r.returncode != 0:
            self.log(f"[{self.name}] WARN remote sha256sum rc={r.returncode}: {r.stderr.strip()[:200]}")
        return out

    def verify(self, rels, rsha):
        bad = []
        for rel in rels:
            f = self.files[rel]
            lsize = self.local_size(rel)
            res = {"class": f["class"], "size_pi": f["size"], "size_mac": lsize,
                   "sha256_pi": rsha.get(rel), "sha256_mac": None}
            if lsize < 0:
                res["status"] = "missing"
            else:
                res["sha256_mac"] = sha256_file(os.path.join(self.dest, rel))
                if not res["sha256_pi"]:
                    res["status"] = "no_pi_sha"
                elif res["sha256_mac"] == res["sha256_pi"]:
                    res["status"] = "ok"
                else:
                    res["status"] = "sha_mismatch"
            self.results[rel] = res
            if res["status"] != "ok":
                bad.append(rel)
        return bad

    def pull_class(self, cls):
        rels = self.class_files(cls)
        if not rels:
            return
        self.phase = cls
        t0 = time.time()
        self.log(f"[{self.name}] class {cls} {CLASSES[cls]}: {len(rels)} files, "
                 f"{human(self.class_bytes(cls))} -> start")
        rc = self.rsync(rels, cls)
        rsha = self.remote_sha(rels)
        bad = self.verify(rels, rsha)
        mism = [r for r in bad if self.results[r]["status"] == "sha_mismatch"]
        if mism:
            self.log(f"[{self.name}] class {cls}: {len(mism)} sha256 mismatch(es); deleting + re-pulling once")
            for rel in mism:
                os.remove(os.path.join(self.dest, rel))
            self.rsync(mism, f"{cls}_repull")
            bad = [r for r in bad if r not in mism] + self.verify(mism, rsha)
        dt = max(time.time() - t0, 0.001)
        ok = len(rels) - len(bad)
        self.log(f"[{self.name}] class {cls} {CLASSES[cls]}: {'DONE' if not bad else 'INCOMPLETE'} "
                 f"{ok}/{len(rels)} ok, {human(self.class_bytes(cls))} in {hms(dt)} "
                 f"({self.class_bytes(cls) / dt / 1e6:.2f} MB/s){'' if rc == 0 else f', rsync exit {rc}'}")
        for rel in bad[:10]:
            self.log(f"[{self.name}]   {self.results[rel]['status']}: {rel}")
        if len(bad) > 10:
            self.log(f"[{self.name}]   ... and {len(bad) - 10} more (pull_report.json)")

    def run(self, classes):
        try:
            for cls in classes:
                self.pull_class(cls)
        except Exception as exc:          # loud, per unit; the other unit keeps going
            self.error = f"{type(exc).__name__}: {exc}"
            self.log(f"[{self.name}] FAILED: {self.error}")
        finally:
            self.phase = None

    def progress_line(self, dt):
        if self.phase is None or not self.dest:
            return None
        now = self.local_bytes_in_flight()
        if self.done_bytes_prev is not None and dt > 0:
            self.rate = max(0.0, (now - self.done_bytes_prev) / dt)
        self.done_bytes_prev = now
        rels = self.class_files(self.phase)
        done = sum(1 for r in rels if self.local_size(r) == self.files[r]["size"])
        cb = self.class_bytes(self.phase)
        cdone = sum(max(0, min(self.local_size(r), self.files[r]["size"])) for r in rels)
        total = sum(f["size"] for f in self.files.values() if f["class"] in self.a.class_list)
        return (f"[{self.name}] class {self.phase} {CLASSES[self.phase]}: {human(cdone)}/{human(cb)} "
                f"files {done}/{len(rels)} | {self.rate / 1e6:.2f} MB/s | unit {human(now)}/{human(total)}")


def progress_loop(units, every, stop, log):
    last = time.time()
    while not stop.wait(every):
        now = time.time()
        for u in units:
            line = u.progress_line(now - last)
            if line:
                log(line)
        last = now


def dry_run(units, a, log):
    log("[dry-run] measuring ssh throughput: "
        f"{a.probe_mb:.0f} MB from each unit at once (shared Wi-Fi)")
    threads = []
    for u in units:
        def probe(u=u):
            try:
                u.probe_rate(a.probe_mb)
            except Exception as exc:
                u.error = f"rate probe: {exc}"
        t = threading.Thread(target=probe)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    worst = 0.0
    for u in units:
        rate = a.rate_mbps * 1e6 if a.rate_mbps else u.rate_probe
        src = "--rate-mbps" if a.rate_mbps else "measured"
        log(f"[dry-run] {u.name}  {u.target}:{u.run_dir}  "
            f"rate {rate / 1e6:.2f} MB/s ({src})" if rate else f"[dry-run] {u.name}: no rate ({u.error})")
        cum = 0.0
        for cls in CLASSES:
            n, b = len(u.class_files(cls)), u.class_bytes(cls)
            if not n:
                continue
            skip = "" if cls in a.class_list else "   (SKIPPED by --classes)"
            t = b / rate if rate else float("nan")
            if cls in a.class_list:
                cum += t
            log(f"[dry-run]   {cls} {CLASSES[cls]:14s} {n:5d} files {human(b):>10s}  "
                f"~{hms(t) if rate else '?':>7s}  cumulative {hms(cum) if rate else '?'}{skip}")
        local = os.path.join(a.dest_root, u.run_date(), u.name)
        log(f"[dry-run]   -> {local}/  ({u.run_name()})")
        worst = max(worst, cum)
    log(f"[dry-run] projected wall time (units in parallel): ~{hms(worst)} + remote sha256 (~1-2 min per GB-scale class)")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pull both units' pool run folders to the Mac in parallel, strict priority, sha256-verified.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", action="append", help="unit address or name=address (repeatable; "
                    f"default {' '.join(DEFAULT_HOSTS)})")
    ap.add_argument("--user", default="pi")
    ap.add_argument("--remote-root", default="/home/pi/pool_runs")
    ap.add_argument("--run", action="append", help="'latest' (default), a run folder name, or unit=NAME "
                    "per unit (repeatable), e.g. --run bmcam003=20261013_181500_bmcam003")
    ap.add_argument("--dest-root", default=os.path.expanduser("~/NereusPool"))
    ap.add_argument("--date", help="override the <date> folder (default: the run folder's date)")
    ap.add_argument("--classes", default="1,2,3,4,5,6", help="classes to pull, in this order (default 1,2,3,4,5,6)")
    ap.add_argument("--dry-run", action="store_true", help="list sizes per class + projected time; copy nothing")
    ap.add_argument("--probe-mb", type=float, default=16.0, help="dry-run rate probe size per unit")
    ap.add_argument("--rate-mbps", type=float, help="dry-run: use this MB/s instead of probing")
    ap.add_argument("--retries", type=int, default=3, help="rsync attempts per class (each resumes)")
    ap.add_argument("--connect-timeout", type=int, default=10, help="ssh ConnectTimeout seconds")
    ap.add_argument("--io-timeout", type=int, default=60, help="rsync --timeout (stalled I/O) seconds")
    ap.add_argument("--sha-timeout", type=int, default=1800, help="remote sha256sum call timeout seconds")
    ap.add_argument("--progress-s", type=float, default=5.0, help="progress line interval")
    a = ap.parse_args(argv)
    try:
        a.class_list = [int(c) for c in a.classes.split(",") if c]
        assert a.class_list and all(c in CLASSES for c in a.class_list)
    except (ValueError, AssertionError):
        print(f"[pool_pull] --classes {a.classes!r}: comma list from {sorted(CLASSES)}", file=sys.stderr)
        return 2

    log = Log()
    units = [Unit(h, a, log) for h in (a.host or DEFAULT_HOSTS)]
    log(f"[pool_pull] mac={socket.gethostname()} units={','.join(u.target for u in units)} "
        f"run={','.join(a.run or ['latest'])} classes={a.class_list} {'DRY-RUN' if a.dry_run else ''}")

    # listing (parallel; one ssh each)
    def lst(u):
        try:
            u.list_remote()
        except Exception as exc:
            u.error = str(exc)
    ts = [threading.Thread(target=lst, args=(u,)) for u in units]
    [t.start() for t in ts]
    [t.join() for t in ts]
    for u in units:
        if u.error:
            log(f"[{u.name or u.addr}] UNREACHABLE / NO RUN: {u.error}")
        else:
            log(f"[{u.name}] run {u.run_dir} : {len(u.files)} files, {human(sum(f['size'] for f in u.files.values()))}")
    live = [u for u in units if not u.error]
    if not live:
        log("[pool_pull] no unit listed: nothing to do (exit 1)")
        return 1

    date = a.date or live[0].run_date()
    day_dir = os.path.join(a.dest_root, date)
    if a.dry_run:
        dry_run(live, a, log)
        return 0 if len(live) == len(units) else 1

    log.open(os.path.join(day_dir, "pull.log"))
    for u in live:
        u.dest = os.path.join(day_dir, u.name)
        state = os.path.join(u.dest, STATE_DIR)
        os.makedirs(state, exist_ok=True)
        pin = os.path.join(state, "run.txt")
        if os.path.exists(pin):
            prev = open(pin).read().strip()
            if prev != u.run_name():
                log(f"[{u.name}] REFUSED: {u.dest} already holds run {prev}, this is {u.run_name()}; "
                    f"use --date or --dest-root to keep runs apart (exit 2)")
                return 2
        with open(pin, "w") as f:
            f.write(u.run_name() + "\n")
        log(f"[{u.name}] -> {u.dest}/")

    stop = threading.Event()
    prog = threading.Thread(target=progress_loop, args=(live, a.progress_s, stop, log), daemon=True)
    prog.start()
    t0 = time.time()
    ts = [threading.Thread(target=u.run, args=(a.class_list,)) for u in live]
    [t.start() for t in ts]
    [t.join() for t in ts]
    stop.set()

    # report (merged with an earlier report for units not pulled this time)
    rpath = os.path.join(day_dir, "pull_report.json")
    report = {}
    if os.path.exists(rpath):
        try:
            report = json.load(open(rpath))
        except ValueError:
            report = {}
    report.setdefault("units", {})
    report.update({"updated": datetime.now().isoformat(timespec="seconds"), "mac": socket.gethostname(),
                   "classes": {str(k): v for k, v in CLASSES.items()}})
    failed = False
    for u in units:
        if u.error and not u.files:
            report["units"][u.name or u.addr] = {"target": u.target, "error": u.error}
            failed = True
            continue
        counts = {}
        for rel, f in u.files.items():
            st = u.results.get(rel, {}).get("status") or ("skipped" if f["class"] not in a.class_list else "missing")
            counts[st] = counts.get(st, 0) + 1
        skipped = {rel: {"class": f["class"], "size_pi": f["size"], "status": "skipped"}
                   for rel, f in u.files.items() if f["class"] not in a.class_list}
        files = dict(skipped)
        for rel, f in u.files.items():
            if f["class"] in a.class_list:
                files[rel] = u.results.get(rel, {"class": f["class"], "size_pi": f["size"], "status": "missing"})
        unit_ok = not u.error and all(v["status"] == "ok" for v in files.values() if v["status"] != "skipped")
        failed = failed or not unit_ok
        report["units"][u.name] = {
            "target": u.target, "run_dir": u.run_dir, "run": u.run_name(), "dest": u.dest,
            "complete": unit_ok, "error": u.error, "counts": counts,
            "bytes_pi": sum(f["size"] for f in u.files.values()),
            "files": dict(sorted(files.items()))}
        log(f"[{u.name}] SUMMARY {'COMPLETE' if unit_ok else 'INCOMPLETE'}: "
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if counts.get("skipped"):
            log(f"[{u.name}] SKIPPED by --classes: {counts['skipped']} files (re-run with --classes to fetch)")
    with open(rpath + ".tmp", "w") as f:
        json.dump(report, f, indent=1)
    os.replace(rpath + ".tmp", rpath)
    log(f"[pool_pull] {'OK' if not failed else 'INCOMPLETE'} in {hms(time.time() - t0)}; report {rpath}"
        f"{'' if not failed else '  -> re-run the same command to resume'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
