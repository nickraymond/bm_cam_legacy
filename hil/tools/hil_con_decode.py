#!/usr/bin/env python3
"""hil_con_decode.py — HIL (adapted copy of runs/s5_console_20260928/con_decode.py): filter a Spotter console excerpt (stdin, as printed by
console.sh) down to what the ladder needs, and decode the hex dump the Spotter prints for
every cellular send back to ASCII.

Input:  console lines "<monitor ts> <spotter text>" (spotter_serial_monitor.py format).
Output: one line per event:
  CON  <ts> <text>          a console line from a unit ([bmcamNNN] ...) or a bm pub echo
  CELL <ts> <len> <ascii>   a spotter/transmit-data payload (the cellular queue)
  SPOT <ts> <text>          Spotter errors / queue-full lines
and a final "SUMMARY cell=<n> con=<n>" line, so "help adds nothing to the cellular
queue" is a count, not a reading.

Example: hil/tools/hil_console.sh SPOT-33507C 'bm pub bmcam/cmd {...} 1 1' 8 | python3 hil/tools/hil_con_decode.py
Limitation: a hex dump is recognised by the "[BM_TX] ... Message:" header followed by
lines of two-digit hex groups; anything else interleaved ends the dump.
"""
import re
import sys

HEX = re.compile(r"^(?:\S+Z)?\s*(?:Message:)?\s*((?:[0-9a-f]{2}\s?)+)\s*$")
TS = re.compile(r"^(\S+Z)\s?(.*)$")


def main():
    cell = con = 0
    dump = None          # [ts, length, bytearray] while collecting
    out = []

    def flush():
        nonlocal dump, cell
        if dump is not None:
            cell += 1
            text = bytes(dump[2]).decode("ascii", "replace")
            out.append(f"CELL {dump[0]} len={dump[1]} {text}")
            dump = None

    for raw in sys.stdin:
        line = raw.rstrip("\n")
        m = TS.match(line)
        ts, body = (m.group(1), m.group(2)) if m else ("", line)
        if dump is not None:
            h = HEX.match(body.strip()) or HEX.match(line.strip())
            if h and "[" not in body:
                dump[2].extend(int(x, 16) for x in h.group(1).split())
                continue
            if "[BM_TX] [DEBUG] Message:" in body:
                continue
            flush()
        if "Submitted spotter/transmit-data" in body:
            n = re.search(r"Len: (\d+)", body)
            dump = [ts, n.group(1) if n else "?", bytearray()]
            continue
        if (re.search(r"\[bmcam\d+\]|^\d+\.\d+ [0-9a-f]{16}, ", body) and "power |" not in body) or "bm pub bmcam" in body:
            con += 1
            out.append(f"CON  {ts} {body}")
        elif re.search(r"Unable|is full|ERROR|rejected", body) and "power |" not in body:
            out.append(f"SPOT {ts} {body}")
    flush()
    print("\n".join(out))
    print(f"SUMMARY cell={cell} con={con}")


if __name__ == "__main__":
    main()
