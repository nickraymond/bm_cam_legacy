#!/usr/bin/env python3
# filename: rc_heal.py
# description: Sprint25 S5 — re-send requested chunks of a keyed media (rsd) + the <HL> status line.
"""
Sprint25 S5 — the heal slot (SPEC_resend_heal.md §4-5, RESEND_DEVICE.md §4-5).

An operator's `rsd` command (command_messages.parse_rsd) puts heals on the
CommandState pending list: {key, n:[chunk indices], id, wakes_left}. On the next
wakes, BEFORE the new START, this module re-sends those chunks from the sent
record rc_media_key wrote when the media first went out:

  <I{key}.{n}>{base64 chunk n}\\n       byte-identical to the original chunk

and, AFTER END, one status line per key per wake:

  <HL v=1 key=<key> a=<requested|sent|refused|dropped> n=<n> r=<token> id=<cmd id> w=<wake key>>

Rules
  - Only with the command daemon running (no daemon = no pending list = the
    cycle's wire is unchanged).
  - <= HEAL_CAP_PER_WAKE (40) chunks per wake, newest heal first, and never the
    room the new capture needs: before each heal chunk the budget must still hold
    that chunk + the capture's whole burst (`reserve_msgs`, the caller's count
    incl. START/END and the keyframe repeat).
  - Paced exactly like the burst (tx, pump, sleep delay); pump-only: commands
    arriving in the slot are parsed + persisted, nothing else touches the wire.
  - The payload's sha256 must match the record, else the heal is refused
    (`payload_changed`) — never send bytes the backend would splice wrongly.
  - wakes_left drops by one only for heals that existed when the wake began and
    were not finished; 0 -> removed with <HL a=dropped>. Heals that arrive in the
    middle of this wake wait for the next one.
  - <HL> priority per key: sent > dropped > refused > requested.

MVP simplification (state it in the PR): no post-END heals (the spec's +240 s
boundary rule). Anything left over waits for the next wake.

Example (inside a cycle, see rc_video_tx / rc_progressive_jpeg):
  heals = rc_heal.begin_wake(daemon, settings, summary)     # None without a daemon
  ...lane plan with heals.planned_msgs extra messages...
  heals.send_before_start(tx, budget, reserve_msgs=..., delay_seconds=..., sleep_fn=...)
  ...START / chunks / END, ack flush...
  heals.send_status_after_end(tx, budget, wake_key=media_key, delay_seconds=..., sleep_fn=...)
"""

import base64
import hashlib
import os
import time

import rc_media_key
from rc_media_id import chunk_prefix

HEAL_CAP_PER_WAKE = 40
HL_PRIORITY = {"sent": 3, "dropped": 2, "refused": 1, "requested": 0}


class HealRefused(Exception):
    """A heal that cannot be sent; str(exc) is the one-token <HL r=> reason."""


# --- validation (daemon, at command time) ------------------------------------------------

def _sent_dir(settings):
    cfg = settings.get("media_key_cfg") or {}
    return cfg.get("sent_dir") or rc_media_key.DEFAULT_SENT_DIR


def make_heal_validate_fn(sent_dir):
    """validate_fn(key, ns) -> (ok, reason) for CommandDaemon: a sent record for the
    key exists, its payload file exists, and every n < msgs. The sha256 is checked
    at send time (reading the payload here would stall the reader loop's apply)."""
    def validate(key, ns):
        rec = rc_media_key.find_sent_record(sent_dir, key)
        if rec is None:
            return False, "no_record"
        if not rec.get("payload") or not os.path.exists(rec["payload"]):
            return False, "no_payload"
        if max(ns) >= int(rec.get("msgs", 0)):
            return False, "range"
        return True, "ok"
    return validate


# --- wire --------------------------------------------------------------------------------

def heal_lines(record, ns):
    """[(n, wire bytes)] for chunks ns of a sent record, byte-identical to the original
    send (the record's own chunk_b64_chars). Raises HealRefused."""
    path = record.get("payload")
    try:
        with open(path, "rb") as fh:
            payload = fh.read()
    except (OSError, TypeError):
        raise HealRefused("no_payload")
    if hashlib.sha256(payload).hexdigest() != record.get("sha256"):
        raise HealRefused("payload_changed")
    width = int(record["chunk_b64_chars"])
    b64 = base64.b64encode(payload).decode("ascii")
    total = -(-len(b64) // width)
    if any(n >= total for n in ns):
        raise HealRefused("range")
    key = record["key"]
    return [(n, f"{chunk_prefix(n, key)}{b64[n * width:(n + 1) * width]}\n".encode("ascii"))
            for n in ns]


def build_hl_message(key, action, n, reason, command_id, wake_key=None):
    """`<HL v=1 key=.. a=.. n=.. r=.. id=.. w=..>\\n`; w omitted when the wake has no key."""
    parts = ["v=1", f"key={key}", f"a={action}", f"n={int(n)}", f"r={reason}",
             f"id={int(command_id)}"]
    if wake_key:
        parts.append(f"w={wake_key}")
    return "<HL " + " ".join(parts) + ">\n"


# --- one wake ----------------------------------------------------------------------------

class WakeHeals:
    """The pending heals of one wake: plan -> send before START -> <HL> after END."""

    def __init__(self, daemon, sent_dir, summary, cap=HEAL_CAP_PER_WAKE, pump_fn=None):
        self.daemon = daemon
        self.state = daemon.state
        self.sent_dir = sent_dir
        self.summary = summary
        self.cap = int(cap)
        self.pump_fn = pump_fn
        # Snapshot at wake start: only these heals age this wake.
        self.snapshot = {h["key"]: dict(h) for h in self.state.pending_heals}
        self.items = []          # [{key, id, lines:[(n, bytes)], refused}]
        self.outcomes = {}       # key -> {a, n, r, id}
        self.sent_ns = {}        # key -> [n sent this wake]
        self._plan()

    def _plan(self):
        room = self.cap
        for heal in self.state.pending_heals:          # newest first
            item = {"key": heal["key"], "id": heal["id"], "lines": [], "refused": None}
            rec = rc_media_key.find_sent_record(self.sent_dir, heal["key"])
            try:
                if rec is None:
                    raise HealRefused("no_record")
                lines = heal_lines(rec, heal["n"])
            except HealRefused as exc:
                item["refused"] = str(exc)
            else:
                item["lines"] = lines[:max(room, 0)]
                room -= len(item["lines"])
            self.items.append(item)
        print(f"[HEAL] wake plan: {len(self.items)} pending heal(s), "
              f"{self.planned_msgs} chunk(s) this wake (cap {self.cap})"
              + "".join(f"; {i['key']} REFUSED {i['refused']}" for i in self.items if i["refused"]))

    @property
    def planned_msgs(self):
        return sum(len(i["lines"]) for i in self.items)

    def send_before_start(self, tx, budget, *, reserve_msgs, delay_seconds,
                          sleep_fn=time.sleep):
        """Send the planned heal chunks, paced, pump-only; then update + persist the
        pending list. Never raises (a heal must not cost the new capture)."""
        sent = 0
        lines = [(item["key"], n, line) for item in self.items for n, line in item["lines"]]
        try:
            for key, n, line in lines:
                if not budget.messages_fit(1 + int(reserve_msgs)):
                    print(f"[HEAL] budget stop: {budget.remaining_s():.0f}s left must hold "
                          f"the capture's {reserve_msgs} msgs")
                    break
                tx(line)
                self.sent_ns.setdefault(key, []).append(n)
                sent += 1
                if self.pump_fn is not None:
                    try:
                        self.pump_fn()
                    except Exception as exc:
                        print(f"[CMD][WARN] heal-slot command pump failed: {exc}")
                sleep_fn(float(delay_seconds))
        except Exception as exc:
            print(f"[HEAL][WARN] heal send failed after {sent} chunk(s): {exc}")
        print(f"[HEAL] sent {sent} heal chunk(s) before START: "
              + (", ".join(f"{k}:{v}" for k, v in self.sent_ns.items()) or "none"))
        self.summary["heal"] = {"planned": self.planned_msgs, "sent": sent}
        self._finish()
        return sent

    def _finish(self):
        """Apply this wake to the pending list (spec §5) and record outcomes."""
        by_key = {i["key"]: i for i in self.items}
        kept = []
        for heal in self.state.pending_heals:
            snap = self.snapshot.get(heal["key"])
            if snap is None or snap["id"] != heal["id"] or heal["key"] not in by_key:
                kept.append(heal)       # arrived during this wake: next wake's job
                continue
            item = by_key[heal["key"]]
            if item["refused"]:
                self._outcome(heal["key"], "refused", len(heal["n"]), item["refused"], heal["id"])
                continue
            done = self.sent_ns.get(heal["key"], [])
            left = [n for n in heal["n"] if n not in done]
            if not left:
                self._outcome(heal["key"], "sent", len(done), "ok", heal["id"])
                continue
            if done:
                self._outcome(heal["key"], "sent", len(done), "partial", heal["id"])
            if heal["wakes_left"] <= 1:
                self._outcome(heal["key"], "dropped", len(left), "expired", heal["id"])
                continue
            kept.append(dict(heal, n=left, wakes_left=heal["wakes_left"] - 1))
        try:
            self.state.set_pending_heals(kept)
        except Exception as exc:
            print(f"[HEAL][WARN] pending heal list not persisted ({exc}); "
                  "the next wake may re-send (idempotent at the backend)")
        print("[HEAL] pending after this wake: "
              + (", ".join(f"{h['key']}({len(h['n'])} left, {h['wakes_left']} wakes)" for h in kept)
                 or "none"))

    def _outcome(self, key, action, n, reason, command_id):
        old = self.outcomes.get(key)
        if old is None or HL_PRIORITY[action] > HL_PRIORITY[old["a"]]:
            self.outcomes[key] = {"a": action, "n": n, "r": reason, "id": command_id}

    def status_lines(self, wake_key=None):
        """One <HL> per key: this wake's send outcomes merged with the daemon's
        per-command events (requested/refused), highest priority wins."""
        for ev in getattr(self.daemon, "heal_events", []):
            self._outcome(ev["key"], ev["a"], ev["n"], ev["r"], ev["id"])
        return [build_hl_message(k, o["a"], o["n"], o["r"], o["id"], wake_key)
                for k, o in self.outcomes.items()]

    def send_status_after_end(self, tx, budget, *, wake_key=None, delay_seconds,
                              sleep_fn=time.sleep):
        """Send the <HL> lines after END, paced (sleep BEFORE each: END is not
        followed by a sleep). Never raises. Returns the lines sent."""
        sent = []
        try:
            for line in self.status_lines(wake_key):
                if not budget.messages_fit(1):
                    print("[HEAL][WARN] no budget left for <HL>; skipped")
                    break
                sleep_fn(float(delay_seconds))
                tx(line.encode("ascii"))
                sent.append(line.strip())
                print(f"[HEAL] status: {line.strip()}")
        except Exception as exc:
            print(f"[HEAL][WARN] <HL> send failed: {exc}")
        self.summary.setdefault("heal", {})["hl"] = sent
        return sent


def begin_wake(daemon, settings, summary, pump_fn=None, cap=HEAL_CAP_PER_WAKE):
    """WakeHeals for this wake, or None without a daemon. Pumps pending commands first
    (pump-only: persists, touches no wire) so an rsd that arrived early in the wake is
    planned now instead of next wake. Never raises."""
    if daemon is None:
        return None
    try:
        if pump_fn is not None:
            pump_fn()
        return WakeHeals(daemon, _sent_dir(settings), summary, cap=cap, pump_fn=pump_fn)
    except Exception as exc:
        print(f"[HEAL][WARN] heal planning failed ({exc}); no heals this wake")
        return None
