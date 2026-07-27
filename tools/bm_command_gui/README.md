# BM Camera Command GUI — operator runbook (Sprint10 §7)

Local operator tool for sending remote commands to BM camera units via
the Sofar cloud (SPEC "Operator GUI"; DESIGN D9/D10). Runs on the Mac,
serves localhost only. NOT the customer website.

## Start

```bash
export SOFAR_API_TOKEN_BM_REEF=...   # already in ~/.zshenv on Nick's Mac
python3 tools/bm_command_gui/server.py
```

Open <http://127.0.0.1:8770>. Stop with Ctrl-C. No dependencies beyond
the repo (Python stdlib only).

## Using it

1. **Target** — pick the unit (list lives in
   `tools/bm_command_gui/targets.json`; add new units there). The
   expected BM node id is shown next to the selector.
2. **Command / Value** — dropdowns are generated from
   `BM_Devel_Pi/command_tables.py` (tables version shown in the
   header). There is no free-form input anywhere, by design.
3. **Send** — the command id is allocated automatically (never reuses a
   logged id). A green banner = Sofar accepted it (HTTP 202) into the
   **cloud mailbox**; the node executes it on its next wake, so
   delivery is not immediate.
4. **Watch the state column:**
   - `awaiting node` (yellow) — accepted by the cloud, ack not yet seen
     at the backend. **Normal for 15–45 min** (node duty cycle + the
     13–30 min Notecard backend lag). Do not re-send.
   - `acked` (green) — the ack arrived and the applied `st` values
     match what was commanded.
   - `mismatch` / `send failed` (red) — shown loudly with the reason.
     A `mismatch` means the device answered but disagreed (rejected
     value, or a different applied value) — read the detail.
5. **In-flight lockout** — while a command awaits its ack for a target,
   Send is refused with a warning (the Spotter queue has 2 slots and
   drops silently; the cloud mailbox re-delivers anyway). The "send
   anyway" checkbox overrides deliberately. Sofar's hard limit of
   **1 send/min per Spotter** is enforced separately and cannot be
   overridden.
6. **Acks** — polled from `api/sensor-data` automatically every 2 min;
   "Check for acks now" forces a sweep. "Not seen yet" never means
   "not delivered" — backend lag rules.

## State on disk (all append-only artifacts)

- `runs/gui_commands.jsonl` — command lifecycle event log (replayed on
  restart; nothing is lost by stopping the server).
- `runs/sofar_command_sends.jsonl` — every API send (shared with
  `tools/sofar_send_command.py`, so CLI + GUI share one rate-limit
  view). Token is never logged.

## Known limits (v1)

- Node-id verification of acks is pending the first cloud-delivered ack
  (we need to see which sensor-data field carries the publisher node id
  — `lifecycle.verify_ack` has the hook).
- One operator at a time; server binds 127.0.0.1 only.
- `st` in acks is command-space: 0 means "never commanded / default" —
  the YAML may still hold a bench-set manual value (see DEV_LOG §3
  touched-semantics note).
