#!/usr/bin/env python3
"""console_loss.py: first-send loss for one wake key, counted from a Spotter console excerpt (decoded 'Added message'
hex dumps), for wakes where START was rejected and hil_r2_score.py cannot run. Read-only.
Usage: console_loss.py <console_excerpt.txt> <key>   -> M, got, missing, loss %, gap runs, START/END seen, queue_full span"""
import itertools, re, sys
L = open(sys.argv[1], errors="replace").read().splitlines(); key = sys.argv[2]
msgs, cur = [], None
for l in L:
    m = re.search(r"Added message\(id: (\d+) len: (\d+)\)", l)
    if m:
        cur = {"t": l[21:44], "hex": []}; msgs.append(cur); continue
    if cur is not None and re.match(r"^\S+Z  ([0-9a-f]{2} ?)+$", l):
        cur["hex"] += l.split()[1:]
idx, M, start, end = set(), None, None, None
for m in msgs:
    b = bytes(int(h, 16) for h in m["hex"])
    r = re.match(rb"<I" + key.encode() + rb"\.(\d+)/(\d+)>", b)
    if r: idx.add(int(r.group(1))); M = int(r.group(2))
    if b.startswith(b"<START"): start = m["t"]
    if b.startswith(b"<END"): end = m["t"]
miss = sorted(set(range(M or 0)) - idx)
runs = [f"{g[0][1]}x{len(g)}" for _, g in ((k, list(g)) for k, g in itertools.groupby(enumerate(miss), lambda x: x[1] - x[0]))]
full = [l[21:44] for l in L if "is full" in l]
print(f"key {key} M {M} got {len(idx)} missing {len(miss)} loss {100*len(miss)/M:.2f} % gaps {runs} START {start} END {end} "
      f"queue_full {len(full)} {full[0] if full else ''}..{full[-1] if full else ''}")
