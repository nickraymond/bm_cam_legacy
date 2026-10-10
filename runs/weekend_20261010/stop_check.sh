#!/bin/bash
# Weekend stop-rule check (run-book section 3), READ-ONLY over the collector's evidence. Prints FLAG lines (or "no triggers").
# Unit-side triggers: CFG ERR / LKG fallback, budget skip, hard cut (no Pi-off before bus-off in wakes.csv), unit unreachable all wake
# (no cycle log), 2 in a row of any of them. Spotter-side loss is NOT a trigger. Usage: stop_check.sh [hours_back=3]
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
W=runs/weekend_20261010; HB=${1:-3}
python3 - "$W" "$HB" <<'PY'
import csv, datetime as d, glob, os, re, sys
W, hb = sys.argv[1], int(sys.argv[2]); now = d.datetime.now(d.timezone.utc); flags = []
since = (now - d.timedelta(hours=hb)).strftime("%Y-%m-%dT%H")
for h, s in (("bmcam003", "SPOT-33507C"), ("bmcam004", "SPOT-31593C")):
    wakes = sorted((r for r in csv.DictReader(open(os.path.join(W, "wakes.csv"))) if r["spot"] == s and r["window"][:13] >= since), key=lambda r: r["window"]) if os.path.exists(os.path.join(W, "wakes.csv")) else []
    bad_streak = 0
    for r in wakes:
        hh = r["window"].replace(":", "")[:13].replace("T", "T")
        cyc = os.path.join(W, "pulled", h, f"cycle_{r['window'][:10]}T{r['window'][11:13]}.log")
        C = open(cyc, errors="replace").read() if os.path.exists(cyc) else ""
        issues = []
        if not C: issues.append("no cycle log (unit unreachable all wake?)")
        if re.search(r"\[CFG\]\[ERR\]|LKG", C): issues.append("CFG ERR / LKG")
        if re.search(r"skipped_no_budget|\[PHASE\]\[WARN\] skipping", C): issues.append("budget skip")
        if not r.get("pi_off") and r.get("pi_on"): issues.append("no Pi-off before bus-off (hard cut?)")
        bad_streak = bad_streak + 1 if issues else 0
        line = f"{h} {r['window']}: " + ("; ".join(issues) if issues else "ok") + f" (halt {r.get('wake_to_halt_s')} s, qfull {r.get('queue_full')})"
        print(line)
        if issues: flags.append(line + (" | 2 IN A ROW -> STOP RULE" if bad_streak >= 2 else ""))
print("FLAGS:" if flags else "no triggers"); [print("  " + f) for f in flags]
PY
tail -1 $W/mirror.log; tail -1 $W/collector.log | cut -c1-120; pmset -g batt | head -1
