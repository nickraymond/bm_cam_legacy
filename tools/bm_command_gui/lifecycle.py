#!/usr/bin/env python3
# filename: lifecycle.py
# description: Sprint10 §7 / Sprint26 S4 c.3 — command lifecycle store for the operator GUI (commands v9).
"""
Operator GUI — command lifecycle tracking (Sprint10 DESIGN D10; commands v9
since Sprint26 S4 c.3, DESIGN_supervisor.md §6.2, PLAN_S4.md G8/G9).

Every command the operator sends moves through explicit states so the
GUI can show what is in flight and stop queue-stuffing (Sprint09:
Spotter drops are silent, the cloud queues while the node is off):

    draft -> sent_to_cloud -> awaiting_node -> acked | rejected | mismatch
                 +-> send_failed (HTTP != 202 / network error)

- sent_to_cloud: Sofar API returned 202 (enqueued in the cellular
  mailbox; NOT delivered yet).
- awaiting_node: alias state entered immediately after 202 — kept
  distinct so the UI can show "cloud accepted" separately from "waiting
  for the node's ack" as polling proceeds.
- acked: a v9 slim ack {"id","ok":1,"h"[,"s","d","v"]} with this id was
  seen at api/sensor-data. `h` (the unit's config hash after the command)
  is recorded; `s:1` = staged until cfm, `d:1` = the unit's repeat of an
  earlier answer (still acked), `v` = granted hold minutes.
- rejected: ok:0 — the unit refused it; `e` (G8 code) and `k` (first
  offending key) are recorded and shown loudly.
- mismatch: the ack came from the wrong BM node, or answers an id this
  GUI never sent — shown loudly, never swallowed.

There is no `st` snapshot in v9 (the v8 check "applied value visible in
st" is gone); the `h` hash is the proof of what the unit now runs.

Persistence: one JSONL event log (append-only, same pattern as the send
log). State is rebuilt by replay on load, so a GUI restart loses
nothing and the log doubles as the run artifact. Pre-v9 events (with a
`v` value instead of `fields`) still replay.

Pure logic + file I/O; no HTTP, no network. The server layer calls
record_*() around sofar_send_command / sofar_poll_acks.

Example (repo root):
  python3 -c "import sys; sys.path.insert(0,'tools/bm_command_gui'); \
import lifecycle as lc; print(lc.CommandLifecycle('/tmp/x.jsonl').next_command_id())"

Known limitations: node-id verification only runs when both the target's
expected node id and the sensor-data `bristlemouth_node_id` are present.
"""

import json
import os
import time

# Lifecycle states (D10; scheduled/retry states added post-diagnosis
# 2026-07-31 — see runs/remote_cmd_diagnosis_20260731/REPORT.md: mailbox
# drains can fire while the BM bus is off, consuming the command with no
# listener, so the GUI aims sends at the wake window and retries the same
# id until acked)
DRAFT = "draft"
SCHEDULED = "scheduled_wake"       # armed, waiting for the unit's next wake
SENT_TO_CLOUD = "sent_to_cloud"
AWAITING_NODE = "awaiting_node"
ACKED = "acked"
REJECTED = "rejected"                # v9 ok:0 (e/k recorded)
MISMATCH = "mismatch"
SEND_FAILED = "send_failed"
RETRY_EXHAUSTED = "retry_exhausted"  # no ack after max attempts — alert
CANCELLED = "cancelled"              # operator cancelled a scheduled send

TERMINAL_STATES = (ACKED, REJECTED, MISMATCH, SEND_FAILED, RETRY_EXHAUSTED, CANCELLED)
IN_FLIGHT_STATES = (SENT_TO_CLOUD, AWAITING_NODE)
# States that block a new send to the same spotter (one command in flight
# per spotter — stacking pending commands in the Sofar FIFO is the wedge
# risk state)
PENDING_STATES = (SCHEDULED,) + IN_FLIGHT_STATES

# commands v9 id ranges the GUI mints from (command_wire.RANGES "remote":
# cellular ack + high-water). DESIGN §6.2: the bench GUI floor moved from
# 1000 to 1e6 in S4. Kept as literals so this module stays import-free.
REMOTE_ID_FLOOR = 1_000_000
REMOTE_ID_CEILING = 99_999_999


class CommandLifecycle:
    """Replayable event-sourced store of operator commands."""

    def __init__(self, log_path):
        self.log_path = log_path
        self.commands = {}  # cmd_id -> dict (latest state + history)
        self._replay()

    # -- persistence ------------------------------------------------------

    def _replay(self):
        try:
            with open(self.log_path, "r", encoding="ascii") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue  # torn tail line
                    self._apply(ev)
        except FileNotFoundError:
            pass

    def _append(self, ev):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a", encoding="ascii") as f:
            f.write(json.dumps(ev, separators=(",", ":")) + "\n")
        self._apply(ev)

    def _apply(self, ev):
        cid = ev["cmd_id"]
        cmd = self.commands.setdefault(cid, {
            "cmd_id": cid, "state": DRAFT, "history": [],
        })
        cmd["history"].append(ev)
        cmd["state"] = ev["state"]
        for k in ("utc", "spotter_id", "node_id", "c", "v", "fields",
                  "message", "http_status", "response", "ack", "ack_node_id",
                  "mismatch_detail", "attempt", "cleared_queue",
                  "scheduled_utc", "h", "e", "k", "staged", "duplicate",
                  "granted_min", "note"):
            if k in ev:
                cmd[k] = ev[k]
        if "ts" in ev:
            cmd["last_attempt_ts"] = ev["ts"]

    # -- queries ----------------------------------------------------------

    def get(self, cmd_id):
        return self.commands.get(cmd_id)

    def _in_states(self, states, spotter_id=None):
        out = []
        for cmd in self.commands.values():
            if cmd["state"] not in states:
                continue
            if spotter_id is not None and cmd.get("spotter_id") != spotter_id:
                continue
            out.append(cmd)
        return sorted(out, key=lambda c: c["cmd_id"])

    def in_flight(self, spotter_id=None):
        """Commands awaiting an ack (retry loop + ack matcher)."""
        return self._in_states(IN_FLIGHT_STATES, spotter_id)

    def pending(self, spotter_id=None):
        """Scheduled OR awaiting — blocks new sends to the same spotter."""
        return self._in_states(PENDING_STATES, spotter_id)

    def scheduled(self, spotter_id=None):
        """Wake-scheduled commands not yet sent to the cloud."""
        return self._in_states((SCHEDULED,), spotter_id)

    def next_command_id(self, floor=REMOTE_ID_FLOOR, extra_used=(),
                        ceiling=REMOTE_ID_CEILING):
        """Monotonic fresh id in the v9 remote range: above every id ever
        logged in [floor, ceiling] and at least floor. The unit keeps a
        remote-range high-water mark (an id at or below it is refused
        e:"old"), so never reuse or go below a logged id.
        extra_used: ids seen outside this store, e.g. parsed from the
        shared CLI send log, so GUI and CLI never mint the same id. Ids
        outside [floor, ceiling] (old v8 ids, a signed service id sent by
        the CLI) are ignored: they must not push the GUI out of its range.
        Raises ValueError when the range is exhausted."""
        used = [i for i in list(self.commands) + list(extra_used)
                if isinstance(i, int) and floor <= i <= ceiling]
        nxt = max(used + [floor - 1]) + 1
        if nxt > ceiling:
            raise ValueError(f"remote id range exhausted (> {ceiling})")
        return nxt

    def all_commands(self):
        return sorted(self.commands.values(), key=lambda c: c["cmd_id"])

    # -- transitions ------------------------------------------------------

    def _now(self):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def record_scheduled(self, cmd_id, spotter_id, node_id, c, fields, message):
        """Command armed to fire on the unit's next wake (fresh uplink row).
        fields: the v9 command's fields besides id and c (kv, k, v, ...)."""
        self._append({
            "utc": self._now(), "ts": time.time(), "cmd_id": cmd_id,
            "state": SCHEDULED, "scheduled_utc": self._now(),
            "spotter_id": spotter_id, "node_id": node_id,
            "c": c, "fields": fields or {}, "message": message,
        })

    def record_sent(self, cmd_id, spotter_id, node_id, c, fields, message,
                    http_status, response, attempt=1, cleared_queue=False):
        """Log the Sofar API result for a send attempt (attempt>=2 is a
        retry of the same id; the unit's result cache answers a re-sent id
        with its original answer + d:1, so re-sends are idempotent)."""
        ok = http_status == 202
        self._append({
            "utc": self._now(), "ts": time.time(), "cmd_id": cmd_id,
            "state": AWAITING_NODE if ok else SEND_FAILED,
            "spotter_id": spotter_id, "node_id": node_id,
            "c": c, "fields": fields or {}, "message": message,
            "attempt": attempt,
            "http_status": http_status, "response": response,
            **({"cleared_queue": True} if cleared_queue else {}),
        })

    def record_retry_exhausted(self, cmd_id, attempts):
        """No ack after the last allowed attempt — surfaced loudly."""
        self._append({
            "utc": self._now(), "cmd_id": cmd_id,
            "state": RETRY_EXHAUSTED, "attempt": attempts,
        })

    def record_cancelled(self, cmd_id):
        """Operator cancelled a wake-scheduled send before it fired."""
        self._append({
            "utc": self._now(), "cmd_id": cmd_id, "state": CANCELLED,
        })

    def record_ack(self, cmd_id, ack, node_id=None):
        """Log an ack observed at the backend; verdict acked / rejected /
        mismatch (verify_ack). node_id: normalized publisher node id from
        the sensor-data entry (sofar_poll_acks.normalize_node_id), when
        available."""
        cmd = self.commands.get(cmd_id)
        state, detail = verify_ack(cmd, ack, node_id)
        ev = {"utc": self._now(), "cmd_id": cmd_id, "state": state, "ack": ack}
        if ack.get("h") is not None:
            ev["h"] = ack["h"]
        if state == REJECTED:
            ev["e"] = ack.get("e")
            if ack.get("k") is not None:
                ev["k"] = ack["k"]
        if ack.get("s") == 1:
            ev["staged"] = True
        if ack.get("d") == 1:
            ev["duplicate"] = True
        if ack.get("v") is not None:
            ev["granted_min"] = ack["v"]
        if node_id:
            ev["ack_node_id"] = node_id
        if detail:
            # S4c review NIT 11: an acked command's hint (e.g. "staged: send
            # cfm") is a note, not an error
            ev["note" if state == ACKED else "mismatch_detail"] = detail
        self._append(ev)


def verify_ack(cmd, ack, node_id=None):
    """Judge a v9 slim ack against the command this GUI sent.

    ack: {"id","ok","h"[,"e","k","s","d","v"]} (command_wire.build_ack).
    Returns (state, detail): detail is None or a human-readable line the
    GUI shows loudly.
      - no such command sent by this GUI       -> MISMATCH
      - publisher node id != the target's expected node id (field
        `bristlemouth_node_id`, verified Phase C 2026-07-27; only checked
        when both sides are present)           -> MISMATCH ("WRONG DEVICE")
      - ok == 1                                -> ACKED (h recorded by the
        caller; d:1 = a repeat of the unit's earlier answer, still acked;
        s:1 = staged until a cfm, noted)
      - ok == 0                                -> REJECTED with e / k
      - anything else                          -> MISMATCH (malformed ack)
    There is no `st` check in v9: the `h` config hash replaces it.
    """
    if cmd is None:
        return MISMATCH, "ack for a command this GUI never sent"
    expected_node = (cmd.get("node_id") or "").strip().lower()
    if expected_node and node_id and node_id != expected_node:
        return MISMATCH, (f"ack came from node {node_id}, expected "
                          f"{expected_node} — WRONG DEVICE answered")
    ok = ack.get("ok")
    if ok == 1 and not isinstance(ok, bool):
        if ack.get("s") == 1:
            return ACKED, (f"staged until confirmed: send cfm "
                           f"{{\"ref\":{ack.get('id')}}} to apply it")
        return ACKED, None
    if ok == 0 and not isinstance(ok, bool):
        key = f", k={ack['k']}" if ack.get("k") is not None else ""
        return REJECTED, f"device rejected command (e={ack.get('e')}{key})"
    return MISMATCH, f"malformed ack (ok={ok!r})"
