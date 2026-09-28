#!/usr/bin/env python3
# filename: command_guards.py
# description: Sprint26 S4 b.5 — guarded keys: records, cfm, boot/action/uptime counters and the automatic revert (DESIGN §6.3).
"""
Guarded keys (DESIGN §6.3; PLAN_S4.md G10; consensus R10, R11, NEW-1).

  guarded_revert  comms-path keys (uplink.uart.*, commands.enabled,
                  commands.topic), mode.output -> save_local, and every signed
                  service change. The value applies at once (in the overlay)
                  with a record. Without a `cfm` for that set id it reverts:
                    - default: after 2 TRANSMITTING actions or 3 boots,
                    - mode.output: after 3 boots or 2 h of uptime (a save_local
                      stay_on unit neither transmits nor reboots; a remote cfm
                      takes 15-45 min; G10b),
                    - every key: after 2 h of uptime in effect as a backstop
                      (S4b review #1: a trigger-only stay_on unit on wall power
                      has no sends and no reboots, so a wrong topic or baud rate
                      would otherwise never revert; uptime is monotonic, not
                      wall clock, and accrues on idle ticks too),
                  whichever comes first. No wall clock: a broken comms key can
                  kill the time read. Counters start when the value is IN
                  EFFECT (the first boot after, for a next-boot key; G10a).
  guarded_stage   power.halt.enabled: true, power.bus_always_on: true. Stored,
                  NOT applied, until `cfm` (ack s:1).

A revert restores the overlay value that was there before the set (or the
YAML), journals `revert` with the limit that fired, and leaves a note: a
`<CF v=1 h=.. reverted=<key> lim=<limit> ref=<id>>` that always goes cellular
(G10f) at the next process with a daemon.

Record (V9State.guarded[path]):
  {"ref": id, "cls": "revert"|"stage", "before": value|None, "had": bool,
   "new": value, "lim": "tx2_boot3"|"boot3_2h", "in_effect": bool,
   "boots": n, "actions": n, "uptime_s": s}

Inputs:  V9State (every change inside its transaction), key metadata.
Outputs: lists of (path, limit) reverted; notes in state.extra["notes"].

Known limitations: counting is per process start ("boot"); a stay_on config
restart (exit 72) is a boot too — a bad UART value therefore reverts at the
3rd start, before the wrapper's crash-loop cap of 5 (tested).
"""

import config_registry as R

BOOTS_LIMIT = 3
ACTIONS_LIMIT = 2
SAVE_LOCAL_UPTIME_S = 2 * 3600.0
REMOVE = "__remove__"


def guard_class(key, value, service_signed=False):
    """-> "revert" | "stage" | None for setting `key` to `value`."""
    if key.guard == R.SERVICE:
        return "revert" if service_signed else None
    if key.guard in (R.GUARDED_REVERT, R.GUARDED_STAGE):
        if key.guard_when is None or value in key.guard_when:
            return "revert" if key.guard == R.GUARDED_REVERT else "stage"
    return None


def limit_for(path):
    return "boot3_2h" if path == "mode.output" else "tx2_boot3"


def new_record(state, path, cid, cls, value):
    """A record for a guarded set (inside the caller's transaction). A second
    guarded set of the same key keeps the ORIGINAL pre-guard value."""
    prev = state.guarded.get(path)
    if prev is not None and prev.get("cls") == "revert":
        had, before = prev["had"], prev["before"]
    else:
        had, before = path in state.overlay, state.overlay.get(path)
    key = R.BY_PATH[path]
    before_id = prev.get("before_id") if prev is not None and prev.get("cls") == "revert" \
        else state.overlay_ids.get(path)
    return {"ref": cid, "cls": cls, "had": had, "before": before, "before_id": before_id,
            "new": value,
            "lim": limit_for(path), "in_effect": key.apply == R.NEXT_ACTION and cls == "revert",
            "boots": 0, "actions": 0, "uptime_s": 0.0}


def _revert_one(state, path, rec, why):
    """Inside a transaction: restore the pre-guard overlay value, drop the
    record, leave the <CF reverted> note. -> (path, old, new) for the journal."""
    old = state.overlay.get(path)
    state.overlay_ids.pop(path, None)
    if rec.get("had"):
        state.overlay[path] = rec["before"]
        if rec.get("before_id") is not None:
            state.overlay_ids[path] = rec["before_id"]
    else:
        state.overlay.pop(path, None)
    del state.guarded[path]
    notes = state.extra.setdefault("notes", [])
    notes.append({"reverted": path, "lim": why, "ref": rec.get("ref")})
    return (path, old, rec["before"] if rec.get("had") else None)


def _journal(state, done, why):
    for path, old, new in done:
        state.journal("revert", path, old, new, cid=None)
        print(f"[GUARD] REVERTED {path}: {old!r} -> {new!r} (limit {why}, no cfm)")


def count_boot(state):
    """A counted process start (before the UART opens). Reverts records whose
    boot limit is spent; starts the counters of values that take effect now.
    Persists (the boot counter too). -> [(path, limit)] reverted."""
    reverted = []

    def mutate(st):
        done = []
        st.boot_counter += 1
        for path, rec in list(st.guarded.items()):
            if rec.get("cls") != "revert":
                continue
            if rec.get("in_effect") and rec.get("boots", 0) >= BOOTS_LIMIT:
                done.append(_revert_one(st, path, rec, "boot3"))
                reverted.append((path, "boot3"))
                continue
            if rec.get("in_effect"):
                rec["boots"] = rec.get("boots", 0) + 1
            else:
                rec.update(in_effect=True, boots=1)
        return done
    done = state.transaction(mutate)
    _journal(state, done, "boot3")
    return reverted


def count_action(state, transmitted, uptime_s=0.0):
    """After an action (per_boot and stay_on): a transmitting action counts
    toward tx2; uptime accrues toward the save_local 2 h limit. Persists only
    when something changed. -> [(path, limit)] reverted."""
    if not state.guarded:
        return []
    reverted = []

    def mutate(st):
        done = []
        for path, rec in list(st.guarded.items()):
            if rec.get("cls") != "revert" or not rec.get("in_effect"):
                continue
            rec["uptime_s"] = rec.get("uptime_s", 0.0) + max(0.0, float(uptime_s))
            if rec["uptime_s"] >= SAVE_LOCAL_UPTIME_S:
                done.append(_revert_one(st, path, rec, "2h"))
                reverted.append((path, "2h"))
            elif rec["lim"] != "boot3_2h" and transmitted:
                rec["actions"] = rec.get("actions", 0) + 1
                if rec["actions"] >= ACTIONS_LIMIT:
                    done.append(_revert_one(st, path, rec, "tx2"))
                    reverted.append((path, "tx2"))
        return done
    done = state.transaction(mutate)
    for path, why in reverted:
        _journal(state, [d for d in done if d[0] == path], why)
    return reverted


def confirm(state, ref):
    """cfm: the record(s) of set id `ref`. A revert record is dropped (the
    value stays); a staged value is applied now. Inside the caller's
    transaction. -> [(path, old, new)] overlay changes, or None if nothing was
    waiting for that id."""
    hits = [p for p, rec in state.guarded.items() if rec.get("ref") == ref]
    if not hits:
        return None
    done = []
    for path in hits:
        rec = state.guarded.pop(path)
        if rec.get("cls") == "stage":
            old = state.overlay.get(path)
            state.overlay[path] = rec["new"]
            state.overlay_ids[path] = ref
            done.append((path, old, rec["new"]))
    return done


def take_notes(state):
    """The pending <CF reverted> notes (removed from the state by the caller's
    transaction once queued)."""
    return list(state.extra.get("notes", []))
