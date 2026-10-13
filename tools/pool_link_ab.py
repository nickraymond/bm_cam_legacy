#!/usr/bin/env python3
# filename: pool_link_ab.py
# description: Pool test 2026-10-13 — Wi-Fi vs ssh-CPU A/B for the pool_pull rate (Mac -> one Pi), MB/s per method, n runs.
"""
pool_link_ab.py — why is the pool pull ~0.8-1.0 MB/s per unit? Separate Wi-Fi from ssh/crypto CPU.

Question (EM 2026-10-12 8:05 PM): the live T-ENDURE pull ran ~0.8-1.0 MB/s per unit in parallel
over home Wi-Fi, so ~3.6 GB/unit takes ~65-70 min against Nick's 1 h. Which knob helps?

Method (one unit at a time; run it AFTER a pull, never during one: it refuses if an `rsync
--server` is running on the unit). The same random test file (--mb, default 50 MB, in the Pi's
page cache, so no SD reads) is fetched n times (default 3) per method:
  http          plain HTTP (python3 -m http.server on the Pi, curl on the Mac): NO crypto ->
                the Wi-Fi + TCP ceiling
  ssh_default   ssh 'cat file' with ssh's default cipher (the Mac negotiates
                chacha20-poly1305@openssh.com; printed in facts.txt)
  ssh_aes128gcm ssh -c aes128-gcm@openssh.com (the Pi Zero 2W has no ARMv8 AES instructions;
                expected SLOWER: facts.txt shows the CPU Features line)
  ssh_2streams  two ssh 'cat file' at once (aggregate MB/s): helps only if ONE ssh stream is
                CPU-bound (sshd at ~100 % of a core)
  rsync         rsync -a over ssh exactly as pool_pull runs it (rsync protocol + checksums)
Facts captured: wlan0 power_save (Nick's rule: must be off), link signal / tx bitrate, CPU
Features, the negotiated cipher, and sshd %CPU sampled mid-transfer during ssh_default.

Decision rule (set before the data):
  - http within 15 % of ssh_default -> LINK-bound: ciphers / streams won't help; fix placement,
    power save, or pull fewer bytes at the pool. pool_pull defaults stay as they are.
  - http >= 1.5x ssh_default AND sshd >= 90 % CPU -> CPU-bound: the faster of ssh_aes128gcm /
    ssh_2streams (>= 15 % better median, n=3) becomes the pool_pull default (--cipher / --streams).
  - rsync well below ssh_default -> rsync overhead: try pool_pull --whole-file.
  Then confirm the winner with BOTH units in parallel (one pool_pull run), since a shared AP's
  airtime splits between the units.

Outputs (in --out, default ./runs/pool_link_ab_<UTC>_<host>/):
  results.csv   method, run, bytes, seconds, MB_s
  facts.txt     link + CPU + cipher facts, sshd CPU sample
  summary.txt   median MB/s per method + the decision-rule verdict
Cleanup: stops its HTTP server and removes ONLY its own files (ab.bin, http.log, http.pid) and then the
  empty scratch dir (also on Ctrl-C / failure). --remote-dir must be /tmp/pool_link_ab* (refused
  otherwise): the units hold the only DNG copies until the Friday pickup.

Example (Mac, repo root, after the pull finished):
  python3 tools/pool_link_ab.py pi@bmcam003            # 50 MB x 3 runs x 5 methods ~ 12 min @ 1 MB/s
  python3 tools/pool_link_ab.py pi@192.168.1.60 --mb 200 --methods http,ssh_default
Limits: the HTTP method briefly serves ONLY its own test dir on --http-port (default 8099) on the
  LAN; one unit at a time; Wi-Fi conditions vary minute to minute (n=3 medians, same session).
"""
import argparse
import csv
import os
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

METHODS = ("http", "ssh_default", "ssh_aes128gcm", "ssh_2streams", "rsync")
# The units hold the ONLY copy of the DNGs until the Friday pickup (Nick 2026-10-12), so the scratch
# dir is pinned to /tmp/pool_link_ab* (one level, no '..'), and cleanup removes only the files this
# tool creates, then rmdir (QE #158 nit 1). A typo like /home/pi/pool_runs or /tmp is refused.
RE_REMOTE_DIR = re.compile(r"/tmp/pool_link_ab[A-Za-z0-9_.-]*\Z")
OWN_FILES = ("ab.bin", "http.log", "http.pid")


def safe_remote_dir(path):
    """-> the normalised path if it is /tmp/pool_link_ab*, else None."""
    norm = os.path.normpath(path)
    return norm if RE_REMOTE_DIR.match(norm) and ".." not in path.split("/") else None
SSH_BASE = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10",
            "-o", "ServerAliveCountMax=3", "-o", "Compression=no"]


def log(msg):
    print(f"{datetime.now().strftime('%I:%M:%S %p')} {msg}", flush=True)


def ssh(target, cmd, extra=(), timeout=120, **kw):
    return subprocess.run(["ssh"] + SSH_BASE + list(extra) + [target, cmd], timeout=timeout, **kw)


def timed_fetch(argv, timeout):
    """Run argv, discard stdout, -> (bytes, seconds, rc)."""
    t0 = time.monotonic()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    while True:
        chunk = p.stdout.read(1 << 20)
        if not chunk:
            break
        n += len(chunk)
    p.wait(timeout=timeout)
    return n, time.monotonic() - t0, p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description="Wi-Fi vs ssh-CPU A/B for pool_pull (Mac -> one Pi).",
                                 epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="pi@HOST or pi@IP (one unit)")
    ap.add_argument("--mb", type=int, default=50, help="test file size in MB (default 50)")
    ap.add_argument("--n", type=int, default=3, help="runs per method (default 3)")
    ap.add_argument("--methods", default=",".join(METHODS), help=f"comma list from {','.join(METHODS)}")
    ap.add_argument("--http-port", type=int, default=8099)
    ap.add_argument("--remote-dir", default="/tmp/pool_link_ab",
                    help="Pi-side scratch dir, must be /tmp/pool_link_ab* (only its own files are removed after)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    methods = [m for m in a.methods.split(",") if m]
    bad = [m for m in methods if m not in METHODS]
    if bad or not methods or a.mb < 1 or a.n < 1:
        print(f"[ab] bad --methods {bad} / --mb / --n", file=sys.stderr)
        return 2
    if not safe_remote_dir(a.remote_dir):
        print(f"[ab] REFUSED --remote-dir {a.remote_dir!r}: must be /tmp/pool_link_ab* (the units hold the only "
              f"DNG copies until Friday; nothing outside that scratch dir is ever touched)", file=sys.stderr)
        return 2
    a.remote_dir = safe_remote_dir(a.remote_dir)
    host = a.target.split("@")[-1]
    out = a.out or os.path.join("runs", f"pool_link_ab_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{host}")
    os.makedirs(out, exist_ok=True)
    rdir, rfile = a.remote_dir, a.remote_dir.rstrip("/") + "/ab.bin"
    log(f"[ab] {a.target}: {a.mb} MB x {a.n} runs x {methods} -> {out}")

    # never during a pull: refuse if the unit is serving an rsync
    busy = ssh(a.target, "pgrep -af '[r]sync --server' || true", capture_output=True, text=True)
    if busy.returncode != 0:
        log(f"[ab] FAIL: ssh {a.target} rc={busy.returncode}: {busy.stderr.strip()[:200]}")
        return 1
    if busy.stdout.strip():
        log(f"[ab] REFUSED: an rsync is running on {a.target} (a pull?): {busy.stdout.strip()[:200]}")
        return 3

    facts = []
    f = ssh(a.target, "echo power_save: $(iw dev wlan0 get power_save 2>&1); iw dev wlan0 link 2>&1 | "
                      "grep -E 'SSID|freq|signal|tx bitrate|rx bitrate'; echo cpus: $(nproc); "
                      "grep -m1 -i '^Features' /proc/cpuinfo 2>&1; uptime", capture_output=True, text=True)
    facts.append(f.stdout.strip())
    v = subprocess.run(["ssh", "-v"] + SSH_BASE + [a.target, "true"], capture_output=True, text=True, timeout=60)
    facts += [l.split("debug1: ", 1)[-1] for l in v.stderr.splitlines() if "cipher:" in l]
    if "power_save: Power save: on" in facts[0]:
        log("[ab] WARN wlan0 power save is ON (Nick's rule: always off) - that alone can cap throughput")

    mk = ssh(a.target, f"mkdir -p {shlex.quote(rdir)} && head -c {a.mb * 1048576} /dev/urandom > {shlex.quote(rfile)} "
                       f"&& cat {shlex.quote(rfile)} > /dev/null && ls -l {shlex.quote(rfile)}",
             capture_output=True, text=True, timeout=600)
    if mk.returncode != 0:
        log(f"[ab] FAIL creating the test file: {mk.stderr.strip()[:200]}")
        return 1
    log(f"[ab] test file ready: {mk.stdout.strip()}")
    rows, http_started = [], False
    try:
        for m in methods:
            if m == "http":
                # background ONLY the server (not a `cd && ...` list): its own fds go to http.log so ssh
                # returns at once, and $! is the server's pid for the kill below
                ssh(a.target, f"cd {shlex.quote(rdir)} || exit 1; nohup python3 -m http.server {a.http_port} "
                              f"--bind 0.0.0.0 > http.log 2>&1 < /dev/null & echo $! > http.pid", capture_output=True)
                http_started = True
                time.sleep(2)
            for run in range(1, a.n + 1):
                if m == "http":
                    n, sec, rc = timed_fetch(["curl", "-s", "--max-time", "1800",
                                              f"http://{host}:{a.http_port}/ab.bin"], 1900)
                elif m == "ssh_default" or m == "ssh_aes128gcm":
                    extra = ["-c", "aes128-gcm@openssh.com"] if m == "ssh_aes128gcm" else []
                    sampler = None
                    if m == "ssh_default" and run == 1:      # sshd CPU mid-transfer
                        def sample():
                            time.sleep(3)
                            r = ssh(a.target, "top -bn1 | grep -E '^%?Cpu|sshd' | head -6",
                                    capture_output=True, text=True)
                            facts.append("sshd sample (ssh_default run 1, ~3 s in):\n" + r.stdout.strip())
                        sampler = threading.Thread(target=sample)
                        sampler.start()
                    n, sec, rc = timed_fetch(["ssh"] + SSH_BASE + extra + [a.target, f"cat {shlex.quote(rfile)}"], 1900)
                    if sampler:
                        sampler.join()
                elif m == "ssh_2streams":
                    res = [None, None]

                    def one(i):
                        res[i] = timed_fetch(["ssh"] + SSH_BASE + [a.target, f"cat {shlex.quote(rfile)}"], 1900)
                    t0 = time.monotonic()
                    ts = [threading.Thread(target=one, args=(i,)) for i in (0, 1)]
                    [t.start() for t in ts]
                    [t.join() for t in ts]
                    n, sec, rc = res[0][0] + res[1][0], time.monotonic() - t0, max(res[0][2], res[1][2])
                elif m == "rsync":
                    with tempfile.TemporaryDirectory() as d:
                        t0 = time.monotonic()
                        r = subprocess.run(["rsync", "-a", "--partial", "-e", "ssh " + " ".join(SSH_BASE),
                                            f"{a.target}:{rfile}", d + "/"], capture_output=True, text=True)
                        sec = time.monotonic() - t0
                        p = os.path.join(d, "ab.bin")
                        n, rc = (os.path.getsize(p) if os.path.exists(p) else 0), r.returncode
                ok = rc == 0 and n >= a.mb * 1048576
                mbs = n / sec / 1e6 if sec > 0 else 0.0
                rows.append({"method": m, "run": run, "bytes": n, "seconds": round(sec, 2),
                             "MB_s": round(mbs, 3), "ok": ok})
                log(f"[ab] {m:14s} run {run}/{a.n}: {n / 1e6:7.1f} MB in {sec:6.1f} s = {mbs:6.2f} MB/s"
                    f"{'' if ok else '  (FAILED / short: rc=' + str(rc) + ')'}")
            if m == "http":
                ssh(a.target, f"kill $(cat {shlex.quote(rdir)}/http.pid) 2>/dev/null", capture_output=True)
                http_started = False
    finally:
        if http_started:
            ssh(a.target, f"kill $(cat {shlex.quote(rdir)}/http.pid) 2>/dev/null", capture_output=True)
        own = " ".join(shlex.quote(f"{rdir}/{f}") for f in OWN_FILES)
        ssh(a.target, f"rm -f {own}; rmdir {shlex.quote(rdir)} 2>/dev/null || echo 'scratch dir not empty: left in place'",
            capture_output=True)
        log(f"[ab] cleaned up {rdir} on {a.target}")

    with open(os.path.join(out, "results.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["method", "run", "bytes", "seconds", "MB_s", "ok"])
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(out, "facts.txt"), "w") as fh:
        fh.write("\n".join(facts) + "\n")
    med = {m: statistics.median([r["MB_s"] for r in rows if r["method"] == m and r["ok"]] or [0.0])
           for m in methods}
    lines = [f"{a.target}  {a.mb} MB x {a.n}  {datetime.now().isoformat(timespec='seconds')}"]
    lines += [f"  {m:14s} median {med[m]:6.2f} MB/s" for m in methods]
    verdict = "incomplete: need http + ssh_default"
    if med.get("http") and med.get("ssh_default"):
        ratio = med["http"] / med["ssh_default"]
        if ratio < 1.15:
            verdict = (f"LINK-bound (http/ssh = {ratio:.2f} < 1.15): ciphers/streams won't help; keep pool_pull "
                       f"defaults; fix placement / power save / bytes")
        elif ratio >= 1.5:
            alt = {k: med[k] for k in ("ssh_aes128gcm", "ssh_2streams") if med.get(k)}
            best = max(alt, key=alt.get) if alt else None
            if best and alt[best] >= 1.15 * med["ssh_default"]:
                verdict = (f"CPU-bound (http/ssh = {ratio:.2f}): {best} is {alt[best] / med['ssh_default']:.2f}x "
                           f"ssh_default -> make it the pool_pull default, then confirm with both units "
                           f"(check the sshd sample in facts.txt)")
            else:
                verdict = f"CPU-bound (http/ssh = {ratio:.2f}) but no alternative is >= 15 % better"
        else:
            verdict = f"mixed (http/ssh = {ratio:.2f}): look at the sshd CPU sample before changing defaults"
    if med.get("rsync") and med.get("ssh_default") and med["rsync"] < 0.85 * med["ssh_default"]:
        verdict += f"; rsync {med['rsync']:.2f} < 0.85x ssh: try pool_pull --whole-file"
    lines.append("VERDICT " + verdict)
    with open(os.path.join(out, "summary.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    log(f"[ab] wrote {out}/results.csv, facts.txt, summary.txt")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
