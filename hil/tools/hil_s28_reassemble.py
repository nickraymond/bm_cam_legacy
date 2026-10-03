#!/usr/bin/env python3
"""hil_s28_reassemble.py — Sprint28 R1.1: rebuild one keyed still from the Spotter console and
prove it is byte-identical to what the unit sent.

Input:  stdin = hil_con_decode.py output (CELL lines carry the cellular payloads as ASCII), or
        a file with --in; --key the 6-char media key (default: the last START's key=);
        --sent the unit's sent record (pulled app/sent/<stem>.sent.json) for the sha256.
Output: --out <path> the reassembled payload; prints START fields, chunks seen / expected,
        missing chunk indices, sha256, whether it matches the sent record, and for an nrjxl
        the NR header check (magic, method 14, flags 0x04, 4 plane lengths summing to the
        blob). Exit 0 only when complete AND (no --sent, or sha256 matches).
Example:
  hil/tools/hil_console.sh SPOT-33507C - 0 600 | python3 hil/tools/hil_con_decode.py \\
      | hil/tools/hil_s28_reassemble.py --sent pulled/R1/sent.json --out analysis/r1.nrjxl
Limits: duplicate chunks (keyframe-repeat style) keep the first copy; a chunk whose
        base64 does not decode is reported as missing. Self-contained (no BM_Devel_Pi import).
"""
import argparse
import base64
import hashlib
import json
import re
import sys

START = re.compile(r"<START IMG> filename: ([^,]+),.*?length: (\d+), key=([0-9a-z]{6})(.*)")
CHUNK = re.compile(r"<I([0-9a-z]{6})\.(\d+)(?:/(\d+))?>([A-Za-z0-9+/=]*)")


def uvarint(buf, pos):
    n = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, pos


def nr_check(blob):
    """The CONTAINER.md §1 shape (not the crc: that needs the full packer)."""
    if blob[:2] != b"NR":
        return "not an NR container"
    pos, vals = 6, []
    for _ in range(6):
        v, pos = uvarint(blob, pos)
        vals.append(v)
    for _ in range(vals[5]):
        _, pos = uvarint(blob, pos)
    n_len, pos = uvarint(blob, pos)
    lens = []
    for _ in range(n_len):
        v, pos = uvarint(blob, pos)
        lens.append(v)
    ok = blob[2] == 14 and blob[3] == 4 and n_len == 4 and pos + sum(lens) == len(blob)
    return (f"NR method={blob[2]} flags=0x{blob[3]:02x} w={vals[0]} h={vals[1]} "
            f"black={vals[2]} white={vals[3]} params={vals[5]} planes={lens} -> "
            + ("OK" if ok else "BAD SHAPE"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--key")
    ap.add_argument("--sent")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    text = open(args.inp).read() if args.inp else sys.stdin.read()
    starts = [m for m in START.finditer(text)]
    if args.key:
        starts = [m for m in starts if m.group(3) == args.key]
    if not starts:
        print("no keyed START found")
        return 2
    s = starts[-1]
    name, total, key, rest = s.group(1), int(s.group(2)), s.group(3), s.group(4)
    print(f"START {name} length={total} key={key}{rest.split(chr(10))[0][:160]}")
    chunks = {}
    for m in CHUNK.finditer(text):
        if m.group(1) == key:
            chunks.setdefault(int(m.group(2)), m.group(4))
    missing = [i for i in range(total) if i not in chunks]
    print(f"chunks {len(chunks)}/{total} missing={missing[:40]}{' ...' if len(missing) > 40 else ''}")
    if missing:
        return 1
    blob = base64.b64decode("".join(chunks[i] for i in range(total)))
    with open(args.out, "wb") as fh:
        fh.write(blob)
    sha = hashlib.sha256(blob).hexdigest()
    print(f"wrote {args.out} {len(blob)} B sha256={sha}")
    if name.endswith(".nrjxl"):
        print(nr_check(blob))
    if args.sent:
        want = json.load(open(args.sent)).get("sha256")
        print(f"sent record sha256={want} -> {'MATCH' if want == sha else 'MISMATCH'}")
        return 0 if want == sha else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
