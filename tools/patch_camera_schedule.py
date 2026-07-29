#!/usr/bin/env python3
# filename: patch_camera_schedule.py
# description: Set specific keys in a deployed camera_schedule.yaml, in place, loudly.
"""
Surgical key-value patcher for a deployed camera_schedule.yaml.

WHY THIS EXISTS INSTEAD OF sed / an ssh heredoc
A deployed field config carries device-specific drift (crop, timezone, halt
state, window) that must survive. Rewriting the file from a template would
destroy it. `sed -i` on a bare key name is worse than it looks: `enabled:`
appears in bm_commands, media_gid, power_halt and transmit_phase, so an
unscoped substitution silently edits the wrong island. And a Python heredoc
embedded in a double-quoted ssh string gets mangled by two levels of shell
quoting, which is not something to debug against a unit that halts in four
minutes.

So: block-scoped edits, one file, a timestamped backup, and a LOUD failure
if a key or block is missing. A key that is not there is not "no-op" — it
means the deployed config is not the one this patch was written for, and
continuing would leave the unit running a config nobody chose.

Inputs:  --set BLOCK.KEY=VALUE (repeatable), --append-island NAME (see below)
Outputs: the patched file, a .bak_<TS> copy, and a printed before -> after
         line for every key touched. Exit 0 only if every key was applied.

Example:
  python3 patch_camera_schedule.py /home/pi/BM_Devel_Pi/camera_schedule.yaml \\
      --set bm_serial.image_transmit_delay_seconds=1.0 \\
      --set progressive_jpeg.max_run_time_min=13 \\
      --set bm_commands.enabled=true \\
      --ensure bm_commands.post_transmit_listen_s=150 \\
      --ensure transmit_phase.enabled=true

  --set     the key MUST already exist (fails loudly otherwise)
  --ensure  the key is created inside the block (or the whole block is
            created) if absent — for the Sprint11 islands a Sprint10-era
            deployed config will not have.

Known limitations: flat two-level keys only (BLOCK.KEY). Nested keys such as
image_pipeline.camera_controls.LensPosition are out of scope by design —
this is a config nudger, not a YAML editor.
"""

import argparse
import os
import re
import shutil
import sys
import time


def find_block(text, block):
    """(start, end) of a top-level `block:` and its indented body, or None."""
    pat = re.compile(r"^" + re.escape(block) + r":[ \t]*(?:#[^\n]*)?\n", re.M)
    m = pat.search(text)
    if not m:
        return None
    start = m.start()
    i = m.end()
    while i < len(text):
        line_end = text.find("\n", i)
        if line_end == -1:
            line_end = len(text)
        line = text[i:line_end]
        # Blank lines and comments belong to the block; a non-indented
        # non-blank line starts the next top-level key.
        if line.strip() and not line[:1].isspace():
            break
        i = line_end + 1
    return start, min(i, len(text))


def read_key(body, key):
    pat = re.compile(r"^([ \t]+" + re.escape(key) + r":[ \t]*)([^#\n]*)", re.M)
    m = pat.search(body)
    return (m, m.group(2).strip()) if m else (None, None)


def set_key(text, block, key, value, create=False):
    """Set BLOCK.KEY, returning (text, before, action)."""
    span = find_block(text, block)
    if span is None:
        if not create:
            raise KeyError(f"block {block!r} not found")
        # Append a fresh island at the end of the file.
        text = text.rstrip("\n") + f"\n\n{block}:\n  {key}: {value}\n"
        return text, None, "block_created"
    start, end = span
    body = text[start:end]
    m, before = read_key(body, key)
    if m is None:
        if not create:
            raise KeyError(f"key {block}.{key!r} not found")
        header_end = body.find("\n") + 1
        new_body = body[:header_end] + f"  {key}: {value}\n" + body[header_end:]
        return text[:start] + new_body + text[end:], None, "key_created"
    new_body = body[:m.start(2)] + str(value) + body[m.end(2):]
    return text[:start] + new_body + text[end:], before, "set"


def parse_assignment(spec):
    if "=" not in spec:
        raise SystemExit(f"[PATCH][ERROR] expected BLOCK.KEY=VALUE, got {spec!r}")
    dotted, _, value = spec.partition("=")
    if "." not in dotted:
        raise SystemExit(f"[PATCH][ERROR] expected BLOCK.KEY=VALUE, got {spec!r}")
    block, _, key = dotted.partition(".")
    return block.strip(), key.strip(), value.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("path")
    ap.add_argument("--set", action="append", default=[], metavar="BLOCK.KEY=VAL",
                    help="key MUST exist; fails loudly if not")
    ap.add_argument("--ensure", action="append", default=[], metavar="BLOCK.KEY=VAL",
                    help="key/block is created if absent")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        print(f"[PATCH][ERROR] no such file: {args.path}", file=sys.stderr)
        return 1
    original = open(args.path, "r", encoding="utf-8").read()
    text = original
    failures = []

    for spec, create in ([(s, False) for s in args.set]
                         + [(s, True) for s in args.ensure]):
        block, key, value = parse_assignment(spec)
        try:
            text, before, action = set_key(text, block, key, value, create=create)
        except KeyError as exc:
            failures.append(f"{block}.{key}: {exc}")
            print(f"[PATCH][FAIL] {block}.{key} -> {exc}")
            continue
        shown = "(absent)" if before is None else before
        print(f"[PATCH] {block}.{key}: {shown} -> {value}   [{action}]")

    if failures:
        print(f"[PATCH][ERROR] {len(failures)} key(s) not applied; file NOT "
              f"written. The deployed config is not what this patch expects.",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print("[PATCH] --dry-run: nothing written")
        return 0
    if text == original:
        print("[PATCH] no change needed (all values already correct)")
        return 0

    backup = f"{args.path}.bak_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    shutil.copy2(args.path, backup)
    with open(args.path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[PATCH] wrote {args.path} (backup: {backup})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
