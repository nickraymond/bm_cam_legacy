#!/usr/bin/env python3
# filename: golden_hash_masked_diff.py
# description: Prove a golden re-record changed ONLY config hashes: compare every changed trace.txt against a git ref with cfg=/h= hash values masked.
"""
The F-G3-4 / Sprint28 registry-bump gate (Sprint28 SPEC r4 §3.6): adding registry keys
changes the config hash, so `cfg=` / `h=` change in every v2 trace. Nothing else on the
wire may change. This compares each `tests/golden/**/trace.txt` in the working tree with
the same file at a git ref, after masking 8-hex config hashes in the forms the wire uses
(`cfg=xxxxxxxx`, `h=xxxxxxxx`, `"h":"xxxxxxxx"`, `cfg=..` inside hex-dumped payloads is
not masked: none exist in the traces).

Inputs:  --ref (default origin/development), the working tree's tests/golden.
Outputs: one line per trace (same / hash-only / DIFFERS), exit 1 if any trace differs in
         anything but a masked hash, or if a trace was added / removed.
Example: .venv-dev/bin/python tools/golden_hash_masked_diff.py --ref origin/development
Known limitations: summary.json (file sizes / sha256 of state files that embed the
hash or new keys) is not judged here: review that diff by eye.
"""

import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HASH_RE = re.compile(r'(cfg=|\bh=|"h":"|"h": ")[0-9a-f]{8}')


def mask(text):
    return HASH_RE.sub(lambda m: m.group(1) + "########", text)


def git_show(ref, path):
    r = subprocess.run(["git", "-C", REPO, "show", f"{ref}:{path}"], capture_output=True)
    return None if r.returncode else r.stdout.decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="origin/development")
    args = ap.parse_args(argv)
    listed = subprocess.run(["git", "-C", REPO, "ls-tree", "-r", "--name-only", args.ref,
                             "tests/golden"], capture_output=True, text=True, check=True).stdout
    old_traces = {p for p in listed.splitlines() if p.endswith("trace.txt")}
    new_traces = set()
    for root, _dirs, files in os.walk(os.path.join(REPO, "tests", "golden")):
        for name in files:
            if name == "trace.txt":
                new_traces.add(os.path.relpath(os.path.join(root, name), REPO))
    bad = 0
    counts = {"same": 0, "hash-only": 0}
    for path in sorted(old_traces | new_traces):
        if path not in new_traces or path not in old_traces:
            print(f"{'REMOVED' if path not in new_traces else 'ADDED'}  {path}")
            bad += path not in new_traces
            continue
        old = git_show(args.ref, path)
        with open(os.path.join(REPO, path), "r", encoding="utf-8", errors="replace") as fh:
            new = fh.read()
        if old == new:
            counts["same"] += 1
            continue
        if mask(old) == mask(new):
            counts["hash-only"] += 1
            print(f"hash-only {path}")
            continue
        bad += 1
        print(f"DIFFERS   {path}")
        for a, b in zip(mask(old).splitlines(), mask(new).splitlines()):
            if a != b:
                print(f"   - {a[:200]}\n   + {b[:200]}")
                break
    print(f"[MASK] ref={args.ref}: {counts['same']} identical, {counts['hash-only']} differ "
          f"only in config hashes, {bad} differ otherwise")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
