#!/usr/bin/env python3
# filename: command_replies.py
# description: Sprint26 S4 W8a — v9 replies: slim ack, reply lane by id range, one human console line per answer.
"""
How the unit answers a command on the v9 path (DESIGN §6.1 "Reply lanes",
§6.2 "Ack"; PLAN_S4.md G7, G8, G9, G13).

  - Every answer is printed on the Spotter console for a person (free,
    instant, visible on USB with no internet), ASCII only:
        [bmcam003] OK id=7 ping cfg=a41c09e2
        [bmcam003] REJECTED id=8 e=val cfg=a41c09e2
  - A cellular ack {"id","ok","h"[,"e","k","s","d","v"]} is queued only for
    the remote and service id ranges (command_wire.CELLULAR_RANGES). Console,
    heal and conductor ids get the console line only (heals: <HL> is the
    cellular answer).
  - `h` = the effective config hash after the command (hash_fn()).

Inputs:  (id, ok, error code, the parsed command, duplicate?) per answer.
Outputs: (ack JSON or None, [console lines]).

Known limitations: b.2b answers the v8 dispatcher's results (verbs still v8
until b.2c); the text describes them plainly.
"""

import command_wire as W

TRG_LABELS = {0: "cancelled", 1: "capture, save only", 2: "capture + output per mode",
              3: "stored reef reference", 4: "stored reference card"}


def _describe(result):
    cmd, value = result.get("cmd"), result.get("value")
    if cmd is None:
        return ""
    if cmd in ("ping", "help", "cfg"):
        return cmd + (" (reference follows)" if cmd in ("help", "cfg") else "")
    if cmd == "trg":
        if value == 0:
            return "trg 0: pending trigger cancelled"
        return f"trg {value} armed: {TRG_LABELS.get(value, '?')} (next decision point)"
    if cmd == "rsd":
        if isinstance(value, dict) and value.get("x"):
            return "rsd: every pending heal cancelled"
        heals = (value or {}).get("h", []) if isinstance(value, dict) else []
        return f"rsd: {len(heals)} heal(s) queued (<HL> reports each)"
    if cmd == "wap":
        return f"wap {value}: network change dispatched"
    return f"{cmd}={value} (next action)"


class V9Replies:
    def __init__(self, host, hash_fn):
        self.host = host
        self.hash_fn = hash_fn

    def _hash(self):
        try:
            return self.hash_fn()
        except Exception as exc:              # an answer never fails on the hash
            print(f"[CMD][WARN] config hash unavailable for the ack: {exc}")
            return None

    def reply(self, command_id, ok, error, result, duplicate=False, key=None, text=None,
              staged=False, granted=None):
        h = self._hash()
        rng = W.id_range(command_id) if isinstance(command_id, int) else None
        ack = None
        if rng in W.CELLULAR_RANGES:
            ack = W.build_ack(command_id, ok, h=h, e=None if ok else error, k=key,
                              s=staged, d=duplicate, v=granted)
        what = text if text is not None else _describe(result)
        if duplicate:
            what = (what + " " if what else "") + "(duplicate: original answer)"
        head = "OK" if ok else "REJECTED"
        err = "" if ok else f" e={error or 'err'}" + (f" k={key}" if key else "")
        line = f"{head} id={command_id}{' ' + what if what else ''}{err}" + \
            (f" cfg={h}" if h else "")
        return ack, [W.console_line(self.host, line)]
