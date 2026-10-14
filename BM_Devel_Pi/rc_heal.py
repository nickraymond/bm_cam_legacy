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
  <I{key}.{n}/{M}>{base64 chunk n}\\n   (W9: when the record says chunk_total, M = msgs)

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

Heal order (`media_key.heal_order`, registry v12 `uplink.media_key.heal_order`,
Nick 2026-10-08, EPIC_transmission_reliability):
  before  (default = today's wire) the heal chunks go BEFORE this wake's START,
          reserving the new media's whole burst.
  after   START + the new media's burst go FIRST; the heal chunks follow END (and
          the deferred ack flush), each still paced and pump-only, each still
          giving way when the budget left will not hold it + the <HL> lines.
          Measured 10/8 on the bench (REEF-RC 7 + 8 AM wakes): 40 heal chunks
          ahead of START fill the Spotter's 2-slot hand-off queue, the stall lands
          on START and the NEW image loses START + its first 6-8 chunks, so the
          freshest image then needs its own heal. `after` gives the newest media
          the clean part of the window; heals (old chunks that already waited
          hours) take what is left. <HL> stays after the heal chunks either way.
  The idle heal pass and a save_local action have no new media: their heals go
  out as before whatever the order (rc_supervisor.send_pending_heals).

Example (inside a cycle, see rc_video_tx / rc_progressive_jpeg):
  heals = rc_heal.begin_wake(daemon, settings, summary)     # None without a daemon
  ...lane plan with heals.planned_msgs extra messages...
  if heals.order == "before":
      heals.send_before_start(tx, budget, reserve_msgs=..., delay_seconds=..., sleep_fn=...)
  ...START / chunks / END, ack flush...
  if heals.order == "after":
      heals.send_after_end(tx, budget, delay_seconds=..., sleep_fn=...)
  heals.send_status_after_end(tx, budget, wake_key=media_key, delay_seconds=..., sleep_fn=...)
"""

import base64
import hashlib
import os
import time

import rc_media_key

HEAL_CAP_PER_WAKE = 40
HL_PRIORITY = {"sent": 3, "dropped": 2, "refused": 1, "requested": 0}
HEAL_ORDERS = rc_media_key.HEAL_ORDERS            # ("before", "after")
DEFAULT_HEAL_ORDER = rc_media_key.DEFAULT_HEAL_ORDER


def heal_order_for(settings):
    """`before` | `after` from the media_key island (settings["media_key_cfg"]);
    anything missing or unknown is `before` (today's wire), never an error."""
    cfg = settings.get("media_key_cfg") or {}
    order = cfg.get("heal_order", DEFAULT_HEAL_ORDER)
    return order if order in HEAL_ORDERS else DEFAULT_HEAL_ORDER


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
    send (the record's own chunk_b64_chars; W9: `/M` iff the record says chunk_total,
    M = the payload's chunk count = the original START length). Raises HealRefused."""
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
    m = total if record.get("chunk_total") else None
    return [(n, f"{rc_media_key.chunk_prefix(n, key, m)}{b64[n * width:(n + 1) * width]}\n".encode("ascii"))
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
    """The pending heals of one wake: plan -> send (before START, or after END when
    `order` is after) -> <HL> after END."""

    def __init__(self, daemon, sent_dir, summary, cap=HEAL_CAP_PER_WAKE, pump_fn=None,
                 order=DEFAULT_HEAL_ORDER):
        self.daemon = daemon
        self.state = daemon.state
        self.sent_dir = sent_dir
        self.summary = summary
        self.cap = int(cap)
        self.pump_fn = pump_fn
        self.order = order if order in HEAL_ORDERS else DEFAULT_HEAL_ORDER
        # Snapshot at wake start: only these heals age this wake.
        self.snapshot = {h["key"]: dict(h) for h in self.state.pending_heals}
        self.items = []          # [{key, id, lines:[(n, bytes)], refused}]
        self.outcomes = {}       # key -> {a, n, r, id}
        self.sent_ns = {}        # key -> [n sent this wake]
        self._merged_events = {}  # key -> [daemon heal_events merged into its <HL>]
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
        # The order tag is printed only when it is not the default (a `before` log
        # line is byte-identical to before the key existed).
        print(f"[HEAL] wake plan: {len(self.items)} pending heal(s), "
              f"{self.planned_msgs} chunk(s) this wake (cap {self.cap})"
              + ("" if self.order == DEFAULT_HEAL_ORDER else f", order {self.order}")
              + "".join(f"; {i['key']} REFUSED {i['refused']}" for i in self.items if i["refused"]))

    @property
    def planned_msgs(self):
        return sum(len(i["lines"]) for i in self.items)

    def send_before_start(self, tx, budget, *, reserve_msgs, delay_seconds,
                          sleep_fn=time.sleep):
        """Send the planned heal chunks BEFORE the new START, paced (tx, pump, sleep),
        pump-only; then update + persist the pending list. Before each chunk the budget
        must still hold it + the new media's whole burst (`reserve_msgs`). Never raises
        (a heal must not cost the new capture). Also the idle / save_local heal pass
        (reserve 0: nothing follows but the <HL>)."""
        return self._send_chunks(tx, budget, reserve_msgs=reserve_msgs, delay_seconds=delay_seconds,
                                 sleep_fn=sleep_fn, where="before START", lead_sleep=False)

    def send_after_end(self, tx, budget, *, delay_seconds, sleep_fn=time.sleep,
                       reserve_msgs=None):
        """`heal_order: after`: send the planned heal chunks AFTER the new media's END
        (and the deferred ack flush). END is not followed by a sleep, so the pacing
        sleep comes BEFORE each chunk (as the <HL> lines do). Before each chunk the
        budget must hold it + `reserve_msgs` (default: one <HL> per key planned, so
        the status still gets out); the image's own cost has already been spent, so
        this is where a short budget makes the heals give way. Never raises."""
        if reserve_msgs is None:
            reserve_msgs = len(self.items)
        return self._send_chunks(tx, budget, reserve_msgs=reserve_msgs, delay_seconds=delay_seconds,
                                 sleep_fn=sleep_fn, where="after END", lead_sleep=True)

    def _send_chunks(self, tx, budget, *, reserve_msgs, delay_seconds, sleep_fn, where,
                     lead_sleep):
        sent = 0
        lines = [(item["key"], n, line) for item in self.items for n, line in item["lines"]]
        try:
            for key, n, line in lines:
                if not budget.messages_fit(1 + int(reserve_msgs)):
                    print(f"[HEAL] budget stop: {budget.remaining_s():.0f}s left must hold "
                          + (f"the capture's {reserve_msgs} msgs" if not lead_sleep else
                             f"the {reserve_msgs} <HL> line(s)"))
                    break
                if lead_sleep:
                    sleep_fn(float(delay_seconds))
                tx(line)
                self.sent_ns.setdefault(key, []).append(n)
                sent += 1
                if self.pump_fn is not None:
                    try:
                        self.pump_fn()
                    except Exception as exc:
                        print(f"[CMD][WARN] heal-slot command pump failed: {exc}")
                if not lead_sleep:
                    sleep_fn(float(delay_seconds))
        except Exception as exc:
            print(f"[HEAL][WARN] heal send failed after {sent} chunk(s): {exc}")
        print(f"[HEAL] sent {sent} heal chunk(s) {where}: "
              + (", ".join(f"{k}:{v}" for k, v in self.sent_ns.items()) or "none"))
        self.summary["heal"] = {"planned": self.planned_msgs, "sent": sent}
        if self.order != DEFAULT_HEAL_ORDER:
            self.summary["heal"]["order"] = self.order      # default: summary unchanged
        self._finish()
        return sent

    def _finish(self):
        """Apply this wake to the pending list (spec §5) and record outcomes."""
        by_key = {i["key"]: i for i in self.items}
        kept = []
        for heal in self.state.pending_heals:
            snap = self.snapshot.get(heal["key"])
            if snap is None or heal["key"] not in by_key:
                kept.append(heal)       # arrived during this wake: next wake's job
                continue
            if snap["id"] != heal["id"]:
                # A newer rsd for this key replaced the planned one while the wake ran
                # (likely once the heal slot follows the burst, heal_order after). The
                # new one stays whole for the next wake; what this wake DID send is
                # still reported under the planned command's id (sent > requested).
                kept.append(heal)
                done = self.sent_ns.get(heal["key"], [])
                if done:
                    left = [n for n in snap["n"] if n not in done]
                    self._outcome(heal["key"], "sent", len(done), "partial" if left else "ok",
                                  snap["id"])
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
        self._merged_events = {}
        for ev in getattr(self.daemon, "heal_events", []):
            self._outcome(ev["key"], ev["a"], ev["n"], ev["r"], ev["id"])
            self._merged_events.setdefault(ev["key"], []).append(ev)
        return [build_hl_message(k, o["a"], o["n"], o["r"], o["id"], wake_key)
                for k, o in self.outcomes.items()]

    def _clear_sent_events(self, keys_sent):
        """DESIGN §4 "Heal events" (Sprint26 S3b.2): the daemon's per-command
        events merged into an <HL> that WENT OUT are cleared; the rest stay
        for the next wake (a failed or budget-cut send keeps them). A
        long-lived process would otherwise re-send every old event on every
        action and grow the list without bound. per_boot: no wire change (the
        daemon stops right after)."""
        events = getattr(self.daemon, "heal_events", None)
        if not isinstance(events, list):
            return
        sent_ids = {id(ev) for k in keys_sent for ev in self._merged_events.get(k, ())}
        if sent_ids:
            events[:] = [ev for ev in events if id(ev) not in sent_ids]

    def send_status_after_end(self, tx, budget, *, wake_key=None, delay_seconds,
                              sleep_fn=time.sleep):
        """Send the <HL> lines after END, paced (sleep BEFORE each: END is not
        followed by a sleep). Never raises. Returns the lines sent."""
        sent, keys_sent = [], []
        try:
            drain = getattr(self.daemon, "drain_rsd", None)
            if drain is not None and getattr(self.daemon, "v9_inbox", None) is not None:
                # S4 b.7 (review B8): an rsd stashed mid-burst rides THIS <HL>.
                self.summary.setdefault("command_events", []).extend(
                    e["action"] for e in drain())
            lines = self.status_lines(wake_key)
            lane = getattr(self.daemon, "lane_wait_s", None)
            for key, line in zip(list(self.outcomes), lines):
                if not budget.messages_fit(1):
                    print("[HEAL][WARN] no budget left for <HL>; skipped")
                    break
                wait = lane() if lane is not None else 0.0
                import rc_command_hooks          # S4b review R2-1: never into the halt margin
                room = budget.remaining_s() - rc_command_hooks.TAIL_SAFETY_S
                if wait > 0 and room >= wait + float(delay_seconds):
                    print(f"[HEAL] <HL> waits {wait:.0f}s for the boundary guard")   # S4 b.8
                    sleep_fn(wait)
                sleep_fn(float(delay_seconds))
                tx(line.encode("ascii"))
                sent.append(line.strip())
                keys_sent.append(key)
                print(f"[HEAL] status: {line.strip()}")
        except Exception as exc:
            print(f"[HEAL][WARN] <HL> send failed: {exc}")
        self._clear_sent_events(keys_sent)
        self.summary.setdefault("heal", {})["hl"] = sent
        return sent


def begin_wake(daemon, settings, summary, pump_fn=None, cap=HEAL_CAP_PER_WAKE):
    """WakeHeals for this wake, or None without a daemon. Pumps pending commands first
    (pump-only: persists, touches no wire) so an rsd that arrived early in the wake is
    planned now instead of next wake. Never raises."""
    if daemon is None:
        return None
    try:
        drain = getattr(daemon, "drain_rsd", None)
        if drain is not None and getattr(daemon, "v9_inbox", None) is not None:
            summary.setdefault("command_events", []).extend(e["action"] for e in drain())  # S4 b.7
        if pump_fn is not None:
            pump_fn()
        return WakeHeals(daemon, _sent_dir(settings), summary, cap=cap, pump_fn=pump_fn,
                         order=heal_order_for(settings))
    except Exception as exc:
        print(f"[HEAL][WARN] heal planning failed ({exc}); no heals this wake")
        return None
