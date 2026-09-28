#!/usr/bin/env python3
# filename: bm_service_sign.py
# description: Sprint26 S4 a.6 — sign a v9 service command (HMAC-SHA256, 16 hex) and print the Spotter console line.
"""
Sign a Nereus service command for one unit (DESIGN §6.3 `service`, O6).

`uplink.network_type` and `uplink.chunk_chars` are locked for customers; Nereus
changes them with a command signed by the unit's service key. The signature is
HMAC-SHA256(key, canonical JSON of the command without `sig`) truncated to 64
bits (16 hex), so it is good for that one command only; the service id range
(100 000 000 – 199 999 999) has a high-water mark, so a captured line cannot be
replayed.

Inputs:
  host        unit hostname; the key is read from
              ~/.config/nereus/unit_keys/<host>.key (64 hex, mode 600; copied
              from the unit's /home/pi/.config/nereus/service.key at provisioning)
  json        the command WITHOUT sig, e.g.
              '{"id":100000001,"c":"set","kv":{"uplink.chunk_chars":320}}'
Options:
  --key-dir DIR   where <host>.key lives
  --topic T       bm pub topic (default bmcam/cmd)
  --json-only     print only the signed JSON (for sofar_send_command --raw-message)
Output: one line on stdout, e.g.
  bm pub bmcam/cmd {"id":100000001,...,"sig":"3f9a0c1d2e4b5a6f"} 1 1
Exit: 0 printed; 2 bad input (not strict JSON, bad shape, over 248 B signed,
      id outside the service range, sig already present); 3 key missing/bad.

Example:
  tools/bm_service_sign.py bmcam003 '{"id":100000001,"c":"set","kv":{"uplink.chunk_chars":320}}'

Known limitations: the key never goes in the repo, a YAML or a command; this
tool only reads it. It does not send anything (paste the line on the console,
or pass --json-only output to sofar_send_command).
"""

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))

import command_wire as W  # noqa: E402

DEFAULT_KEY_DIR = os.path.expanduser("~/.config/nereus/unit_keys")


def fail(msg, code=2):
    print(f"[SIGN][ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def read_key(key_dir, host):
    path = os.path.join(key_dir, f"{host}.key")
    try:
        with open(path, "r", encoding="ascii") as fh:
            key = W.parse_service_key(fh.read())
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"no service key for {host}: {path} ({exc})", 3)
    if key is None:
        fail(f"{path} is not 64 hex characters", 3)
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        print(f"[SIGN][WARN] {path} is mode {mode:o}; chmod 600 it", file=sys.stderr)
    return key


def sign_command(text, key):
    """-> the signed compact JSON. Raises SystemExit(2) on bad input."""
    try:
        data = W.strict_loads(text)
    except W.Unackable as exc:
        fail(f"not a strict JSON command: {exc}")
    if "sig" in data:
        fail("the command already carries sig; pass it unsigned")
    cid = data.get("id")
    in_range = isinstance(cid, int) and not isinstance(cid, bool) and W.id_range(cid) == "service"
    if not in_range:
        fail(f"id {cid!r} is outside the service range 100000000..199999999")
    data["sig"] = W.sign(data, key)
    signed = W.encode_command(data)
    try:
        W.decode(signed)
    except (W.Unackable, W.Rejected) as exc:
        fail(f"the unit would refuse it: {exc}")
    if len(signed) > W.MAX_JSON_BYTES:
        fail(f"signed JSON is {len(signed)} B > {W.MAX_JSON_BYTES} B")
    return signed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("json")
    ap.add_argument("--key-dir", default=DEFAULT_KEY_DIR)
    ap.add_argument("--topic", default="bmcam/cmd")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args(argv)
    key = read_key(args.key_dir, args.host)
    signed = sign_command(args.json, key)
    line = signed if args.json_only else f"bm pub {args.topic} {signed} 1 1"
    if not args.json_only and len(line) + 1 > W.MAX_CONSOLE_LINE_BYTES:
        fail(f"console line is {len(line) + 1} B > {W.MAX_CONSOLE_LINE_BYTES} B "
             "(a longer topic than bmcam/cmd?)")
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
