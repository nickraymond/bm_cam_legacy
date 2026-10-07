#!/usr/bin/env python3
"""hil_r2_switch.py — R2-DELAY adaptive arm switch, run once per bmcam003 wake at ~HH:03Z (Pi up, send decided).

Reads the CURRENT wake's `[R2DELAY] …` line from the newest cycle log (read-only), decides whether the wake counts
(A always; B only if the hold was applied, skipped=False — EM rule 2026-10-07), advances the ABBA pointer, then writes
the NEXT wake's arm value (0 / 230) to <app>/r2_start_delay_s and reads it back. State: --state JSON (seq, pointer,
history, consecutive_b_skips, stopped). Stops (writes 0, stopped=reason) when the sequence is done or 2 B wakes in a
row were not applied. Decision rules 1–3 are applied by the scorer/TE, not here (it only refuses to continue after a
"stopped" state). Every ssh is bounded (process-group timeout).
Inputs:  --state runs/r2_delay_20261006/schedule.json, --host bmcam003, --wake HH (UTC hour of the current wake)
Outputs: prints one line; updates --state; exit 0 ok, 3 stopped, 4 Pi unreachable / no [R2DELAY] line
Example: python3 hil/tools/hil_r2_switch.py --state runs/r2_delay_20261006/schedule.json --wake 7
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys

VAL = {"A": "0", "B": "230"}


def ssh(host, cmd, t=25):
    p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "ServerAliveInterval=2",
                          "-o", "ServerAliveCountMax=2", f"pi@{host}", cmd], stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=t)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        return 124, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state", required=True)
    ap.add_argument("--host", default="bmcam003")
    ap.add_argument("--wake", type=int, required=True)
    a = ap.parse_args()
    st = json.load(open(a.state)) if os.path.exists(a.state) else {
        "seq": list("ABBAABBAABBA"), "pointer": 0, "history": [], "consecutive_b_skips": 0, "stopped": None}
    if st.get("stopped"):
        print(f"[r2switch] stopped earlier ({st['stopped']}); nothing written"); return 3
    rc, out = ssh(a.host, "cd /home/pi/BM_Devel_Pi && cat r2_start_delay_s; "
                          "grep -h '\\[R2DELAY\\]' $(ls -t cron_logs/rc_cycle_*.log | head -1) | tail -1")
    lines = out.strip().splitlines()
    if rc != 0 or len(lines) < 2 or "[R2DELAY]" not in lines[-1]:
        print(f"[r2switch] wake {a.wake:02d}Z: Pi unreachable or no [R2DELAY] line (rc {rc}): {out.strip()[:200]}")
        return 4
    cur_val, line = lines[0].strip(), lines[-1]
    arm = "B" if float(cur_val or 0) > 0 else "A"
    f = dict(re.findall(r"(\w+)=([^\s]+)", line))
    skipped = f.get("skipped") == "True"
    counted = not (arm == "B" and skipped)
    rec = {"wake_utc_hour": a.wake, "arm": arm, "applied": counted, "line": line}
    for k in ("uptime", "wait", "burst_est", "heal_msgs", "budget_left"):
        if k in f:
            rec[k] = f[k]
    if counted:
        if arm != st["seq"][st["pointer"]]:
            rec["note"] = f"arm {arm} ran where the sequence expected {st['seq'][st['pointer']]}"
        st["pointer"] += 1
        st["consecutive_b_skips"] = 0
    else:
        st["consecutive_b_skips"] += 1
    st["history"].append(rec)
    if st["consecutive_b_skips"] >= 2:
        st["stopped"] = "2 B wakes in a row not applied (budget rule is the blocker)"
    elif st["pointer"] >= len(st["seq"]):
        st["stopped"] = "sequence complete"
    nxt = "A" if st["stopped"] else st["seq"][st["pointer"]]
    rc2, out2 = ssh(a.host, f"echo {VAL[nxt]} > /home/pi/BM_Devel_Pi/r2_start_delay_s && cat /home/pi/BM_Devel_Pi/r2_start_delay_s")
    st["next"] = {"wake_utc_hour": (a.wake + 1) % 24, "arm": nxt, "written": out2.strip(), "rc": rc2}
    json.dump(st, open(a.state, "w"), indent=1)
    print(f"[r2switch] wake {a.wake:02d}Z arm {arm} applied={counted} {('budget_left=' + rec.get('budget_left', '?') + ' heal_msgs=' + rec.get('heal_msgs', '?')) if arm == 'B' else ''}"
          f" | counted {st['pointer']}/{len(st['seq'])} | next {(a.wake + 1) % 24:02d}Z = {nxt} (wrote {out2.strip()!r})"
          + (f" | STOPPED: {st['stopped']}" if st["stopped"] else ""))
    return 3 if st["stopped"] and "2 B" in st["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
