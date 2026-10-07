#!/usr/bin/env python3
"""hil_r3_switch.py — R3-PACE adaptive arm switch (hil/gates/R3_PACE.md), run once per bmcam003 wake at ~HH:01:30Z.

Reads the CURRENT wake's pacing from its cycle log (`[RC] pacing (…): chunk_b64_chars=… delay_s=X`, read-only) → arm
A (1.0 s) or B (1.3 s); counts it (off-sequence wakes count for their arm without advancing the ABBA pointer); then
writes the NEXT wake's pacing into the base camera_config.yaml `uplink:` → `msg_interval_s:` line (the YAML is read
at boot) and reads it back. State: --state JSON (seq, pointer, counts, history, stopped). Every ssh is bounded.
Inputs:  --state runs/r3_pace_<date>/schedule.json, --host bmcam003, --wake HH, [--no-write]
Outputs: one line; exit 0 ok, 3 stopped/complete, 4 Pi unreachable / no pacing line
Example: python3 hil/tools/hil_r3_switch.py --state runs/r3_pace_20261007/schedule.json --wake 16
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys

VAL = {"A": "1.0", "B": "1.3"}       # B is overridden by state["b_value"] (EM: 1.3 default or 1.5, Nick decides)


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
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    st = json.load(open(a.state)) if os.path.exists(a.state) else {
        "seq": list("ABBA" * 7 + "AB"), "b_value": "1.3", "pointer": 0, "counts": {"A": 0, "B": 0}, "history": [], "stopped": None}
    if st.get("stopped"):
        print(f"[r3switch] stopped earlier ({st['stopped']}); nothing written"); return 3
    rc, out = ssh(a.host, "cd /home/pi/BM_Devel_Pi && grep -h '\\[RC\\] pacing' $(ls -t cron_logs/rc_cycle_*.log | head -1) | tail -1")
    m = re.search(r"delay_s=([0-9.]+)", out)
    if rc != 0 or not m:
        print(f"[r3switch] wake {a.wake:02d}Z: Pi unreachable or no pacing line (rc {rc}): {out.strip()[:200]}"); return 4
    d = float(m.group(1))
    VAL["B"] = str(st.get("b_value", "1.3"))
    arm = "A" if abs(d - 1.0) < 0.05 else ("B" if abs(d - float(VAL["B"])) < 0.05 else "?")   # "?" = excluded
    rec = {"wake_utc_hour": a.wake, "arm": arm, "delay_s": d, "line": out.strip()}
    if arm in ("A", "B"):
        st["counts"][arm] += 1
        if st["pointer"] < len(st["seq"]) and arm == st["seq"][st["pointer"]]:
            st["pointer"] += 1
        else:
            rec["note"] = f"off-sequence {arm}"
    st["history"].append(rec)
    if st["pointer"] >= len(st["seq"]):
        st["stopped"] = "sequence complete (12/arm)"
    if a.no_write:
        json.dump(st, open(a.state, "w"), indent=1)
        print(f"[r3switch] recorded wake {a.wake:02d}Z arm {arm} (delay_s {d}) counts={st['counts']} (no write)"); return 0
    nxt = "A" if st["stopped"] else st["seq"][st["pointer"]]
    py = ("import re; p='camera_config.yaml'; s=open(p).read(); "
          f"s2=re.sub(r'(?m)^(  msg_interval_s:)\\s*[0-9.]+.*$', r'\\1 {VAL[nxt]}  # R3-PACE arm {nxt}', s, count=1); "
          "assert s2 != s or 'msg_interval_s: " + VAL[nxt] + "' in s, 'no msg_interval_s line'; open(p,'w').write(s2)")
    rc2, out2 = ssh(a.host, f"cd /home/pi/BM_Devel_Pi && python3 -c \"{py}\" && grep -E '^  msg_interval_s:' camera_config.yaml")
    st["next"] = {"wake_utc_hour": (a.wake + 1) % 24, "arm": nxt, "written": out2.strip(), "rc": rc2}
    json.dump(st, open(a.state, "w"), indent=1)
    print(f"[r3switch] wake {a.wake:02d}Z arm {arm} (delay_s {d}) | seq {st['pointer']}/{len(st['seq'])} counts={st['counts']}"
          f" | next {(a.wake + 1) % 24:02d}Z = {nxt} (yaml: {out2.strip()!r}, rc {rc2})"
          + (f" | {st['stopped']}" if st["stopped"] else ""))
    return 0 if rc2 == 0 else 4


if __name__ == "__main__":
    sys.exit(main())
