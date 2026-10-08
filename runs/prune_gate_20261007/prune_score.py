#!/usr/bin/env python3
"""prune_score.py — PRUNE gate (hil/gates/PRUNE_GATE.md) per-wake check from saved artifacts. Read-only.

Inputs:  --before sent_lists/<prev>.tsv  --after sent_lists/<HH>Z_end.tsv  --cycle pulled/cycle_<HH>Z.log
         (tsv rows: key_s <TAB> stem <TAB> has_payload, from sent_list.py; key_s = rc_media_key.decode_key seconds)
Checks:  1 keyed send: '[KEY] media key K from Spotter UTC' then '[KEY] sent record'
         2 order: any '[KEY] pruned' / '[KEY][WARN] … behind a gap' line comes AFTER '[KEY] media key'
         3 deletions: every record in before but not after had key age > 14 d vs this wake's key; 0 at <= 14 d
           (and 0 undatable deleted); expected deletions = before-records older than 14 d at this key
Output:  one JSON line; exit 0 = all checks pass, 1 = a check failed
Example: python3 prune_score.py --before sent_lists/04Z_predeploy.tsv --after sent_lists/05Z_end.tsv --cycle pulled/cycle_05Z.log
"""
import argparse, json, re, sys

RETAIN_S = 14 * 86400


def load(p):
    rows = {}
    for line in open(p):
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2:
            rows[parts[1]] = None if parts[0] in ("None", "") else float(parts[0])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True); ap.add_argument("--after", required=True)
    ap.add_argument("--cycle", required=True)
    a = ap.parse_args()
    before, after = load(a.before), load(a.after)
    lines = open(a.cycle, errors="replace").read().splitlines()
    i_key = next((i for i, l in enumerate(lines) if "[KEY] media key " in l), None)
    i_rec = next((i for i, l in enumerate(lines) if "[KEY] sent record:" in l), None)
    prune_lines = [(i, l) for i, l in enumerate(lines) if "[KEY] pruned" in l or "behind a gap" in l]
    key = re.search(r"media key (\S+)", lines[i_key]).group(1) if i_key is not None else None
    # this wake's key time = the newest record in `after` (the one this wake wrote)
    new = [s for s in after if s not in before]
    now_s = max((after[s] for s in new if after[s] is not None), default=None)
    deleted = [s for s in before if s not in after]
    bad = [s for s in deleted if before[s] is None or now_s is None or now_s - before[s] <= RETAIN_S]
    expected = sorted(s for s, t in before.items() if t is not None and now_s is not None and now_s - t > RETAIN_S)
    out = {
        "key": key, "keyed_send": i_key is not None and i_rec is not None and i_key < i_rec,
        "prune_lines": [l.strip()[:160] for _, l in prune_lines],
        "prune_after_key": all(i_key is not None and i > i_key for i, _ in prune_lines),
        "before": len(before), "after": len(after), "new": new, "deleted": len(deleted),
        "expected_deleted": len(expected), "deleted_eq_expected": sorted(deleted) == expected,
        "deleted_le_14d_or_undatable": bad,
        "oldest_after_age_d": round((now_s - min(t for t in after.values() if t is not None)) / 86400, 3) if now_s else None,
    }
    out["pass"] = bool(out["keyed_send"] and out["prune_after_key"] and not bad)
    print(json.dumps(out))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
