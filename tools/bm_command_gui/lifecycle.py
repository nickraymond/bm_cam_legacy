#!/usr/bin/env python3
# filename: lifecycle.py
# description: Sprint10 §7 — command lifecycle store for the operator GUI.
"""
Sprint10 operator GUI — command lifecycle tracking (DESIGN D10).

Every command the operator sends moves through explicit states so the
GUI can show what is in flight and stop queue-stuffing (Sprint09:
Spotter drops are silent, the cloud queues while the node is off):

    draft -> sent_to_cloud -> awaiting_node -> acked | mismatch
                 \-> send_failed (HTTP != 202 / network error)

- sent_to_cloud: Sofar API returned 202 (enqueued in the cellular
  mailbox; NOT delivered yet).
- awaiting_node: alias state entered immediately after 202 — kept
  distinct so the UI can show "cloud accepted" separately from "waiting
  for the node's ack" as polling proceeds.
- acked: an ack with this command id was seen at api/sensor-data AND
  its `st` matches what the command asked for (expected node id check
  is wired in once Phase C shows which sensor-data field carries it —
  see TODO below).
- mismatch: ack seen but `st` disagrees with the expectation — shown
  loudly, never swallowed.

Persistence: one JSONL event log (append-only, same pattern as the send
log). State is rebuilt by replay on load, so a GUI restart loses
nothing and the log doubles as the run artifact.

TODO(Phase C): node-id verification. api/sensor-data entries carry the
ack payload in `value`; which entry field (if any) identifies the
publishing BM node is unknown until we see a real cloud-delivered ack.
verify_ack() takes the raw entry dict so this lands as a small patch.

Pure logic + file I/O; no HTTP, no network. The server layer calls
record_*() around sofar_send_command / sofar_poll_acks.
"""

import json
import os
import time

# Lifecycle states (D10)
DRAFT = "draft"
SENT_TO_CLOUD = "sent_to_cloud"
AWAITING_NODE = "awaiting_node"
ACKED = "acked"
MISMATCH = "mismatch"
SEND_FAILED = "send_failed"

TERMINAL_STATES = (ACKED, MISMATCH, SEND_FAILED)
IN_FLIGHT_STATES = (SENT_TO_CLOUD, AWAITING_NODE)


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
        for k in ("spotter_id", "node_id", "c", "v", "message",
                  "http_status", "response", "ack", "mismatch_detail"):
            if k in ev:
                cmd[k] = ev[k]

    # -- queries ----------------------------------------------------------

    def get(self, cmd_id):
        return self.commands.get(cmd_id)

    def in_flight(self, spotter_id=None):
        """Commands awaiting an ack (the GUI warns before re-sending)."""
        out = []
        for cmd in self.commands.values():
            if cmd["state"] not in IN_FLIGHT_STATES:
                continue
            if spotter_id is not None and cmd.get("spotter_id") != spotter_id:
                continue
            out.append(cmd)
        return sorted(out, key=lambda c: c["cmd_id"])

    def next_command_id(self, floor=1000):
        """Monotonic fresh id: above every id ever logged and above floor.
        (Device dedupe keeps last 32 ids — never reuse a logged id.)"""
        used = [c for c in self.commands]
        return max(used + [floor - 1]) + 1

    def all_commands(self):
        return sorted(self.commands.values(), key=lambda c: c["cmd_id"])

    # -- transitions ------------------------------------------------------

    def _now(self):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def record_sent(self, cmd_id, spotter_id, node_id, c, v, message,
                    http_status, response):
        """Log the Sofar API result for a send attempt."""
        ok = http_status == 202
        self._append({
            "utc": self._now(), "cmd_id": cmd_id,
            "state": AWAITING_NODE if ok else SEND_FAILED,
            "spotter_id": spotter_id, "node_id": node_id,
            "c": c, "v": v, "message": message,
            "http_status": http_status, "response": response,
        })

    def record_ack(self, cmd_id, ack, entry=None):
        """Log an ack observed at the backend; verdict acked/mismatch."""
        cmd = self.commands.get(cmd_id)
        detail = verify_ack(cmd, ack, entry)
        self._append({
            "utc": self._now(), "cmd_id": cmd_id,
            "state": ACKED if detail is None else MISMATCH,
            "ack": ack,
            **({"mismatch_detail": detail} if detail else {}),
        })


def verify_ack(cmd, ack, entry=None):
    """Return None if the ack matches the command's expectation, else a
    human-readable mismatch description (shown loudly by the GUI).

    Checks, per SPEC GUI item 4:
      - device ok flag (ok=0 means the daemon rejected it)
      - commanded value visible in ack `st` (settings commands only;
        `st` is command-space per the §3 touched-semantics note)
      - node id: TODO(Phase C) — needs the real sensor-data entry shape.
    """
    if ack.get("ok") != 1:
        return f"device rejected command (ok={ack.get('ok')}, e={ack.get('e')})"
    if cmd is None:
        return "ack for a command this GUI never sent"
    c, v = cmd.get("c"), cmd.get("v")
    if c and c != "ping":
        st = ack.get("st", {})
        if st.get(c) != v:
            return (f"applied value disagrees: sent {c}={v}, "
                    f"ack st.{c}={st.get(c)!r}")
    return None
