# SPRINT 10 — DESIGN

Two halves: (1) how we work together this sprint, (2) architectural
decisions made and their reasons. See repo CLAUDE.md for global rules;
this file adds sprint-specific ones.

## How we work (Claude Code session rules)

- **Branch:** all work on `bm_commands`, branched from `development` —
  per the repo Branching Model adopted 2026-07-26 (see CLAUDE.md): `main`
  is released code only; feature branches PR into `development`.
  *(Corrected 2026-07-27 — this doc originally predated the model and
  said `sprint-10-command-daemon` off main.)*
- **PR:** open a PR (base: `development`) when TRACKER §1–§4 are green;
  Nick reviews before Phase B hardware time is spent.
- **Tests I can interact with:** every phase produces something Nick can
  run — a make target or script (`make test`, `scripts/mock_mote.sh`)
  with human-readable output, not just a CI pass.
- **Small diffs:** one TRACKER section per commit where practical.
- **~600 line rule:** no source file grows past ~600 lines; split modules.
- **Docs discipline:** SPEC = what/why (stable), TRACKER = checklist,
  DEV_LOG = running record. Update DEV_LOG in the same commit as the code
  that prompted it.
- **Field-test bias:** when choosing between clever and boring, choose
  boring. Fragility in the field is the failure mode we fear most.
- **Ask, don't assume:** open questions go to DEV_LOG §Open Questions and
  get raised with Nick; do not silently pick an answer for anything
  marked (Q#).

## Architecture decisions (with reasons)

**D1 — Roll our own daemon; do not adopt bm_sbc.**
bm_sbc's Python client is send-only; receiving BM traffic in Python would
require extending its C gateway plus a Pi-side IPC bridge. Our daemon
already moves data both directions. Reviewed 2026-07-26.

**D2 — Threads + queues concurrency model.**
Exactly one reader thread and one writer thread own the serial port;
all other threads use `queue.Queue`. UART is full-duplex — the constraint
is software framing, not the wire. Camera libs block, which fights
asyncio; threads are the boring, working answer.

**D3 — Enum-indexed commands, never raw values.**
A corrupted raw ROI/exposure value could silently ruin every capture until
the next command window. Table lookup bounds the blast radius: invalid
index → reject + error ack. Tables live in `command_tables.py` only.

**D4 — Idempotency via command ID dedupe + full-state acks.**
Cloud-side queuing can re-deliver; bursts arrive on wake. Duplicate IDs
ack without re-applying. Acks always carry full settings state, so any
single ack tells the operator the complete truth.

**D5 — Listen the whole active window.**
Bus init and neighbor discovery delay delivery unpredictably within the
~20-min window. A fixed short listen phase would miss commands. Listener
runs for the full window alongside capture work.

**D6 — Apply between captures; persist to disk.**
No camera reconfiguration mid-capture. State file on the Pi survives power
cycles — the whole point is that a field fix sticks.

**D7 — Ephemeral-config pattern reserved for one-shot actions.**
(From Sofar discussion.) Set-flag → act → clear-flag is a good fit for
future one-shots ("capture now"); v1 settings commands use the ack/dedupe
model instead. Noted for later sprints.

**D8 — Wire format reuses the existing daemon framing.**
Do not introduce COBS/CBOR (as bm_sbc does) unless the existing framing
proves inadequate in Phase A tests. Changing framing mid-field-test is
exactly the fragility we're avoiding. (Q1 confirms current framing.)

**D9 — Operator GUI is part of v1 "done" (Nick 2026-07-27), kept MVP.**
A local operator web GUI (served on the Mac, not the customer website) is
the sending surface: select a registered SPOT-ID (+ expected node id),
set each value from preset dropdowns only, see send/queued/acked state.
Dropdown options are **generated from `command_tables.py`** — the GUI can
never offer a value the daemon can't apply, and the tables stay the
single source of truth. The future customer website builds on the same
command contract; nothing in the GUI becomes load-bearing protocol.

**D10 — GUI shows command lifecycle to prevent queue-stuffing.**
Sprint09 measured that Spotter-side drops are silent to the sender and
cloud-side commands queue while the node is off. The GUI therefore
tracks each command id through explicit states — draft → sent-to-cloud
(acknowledged by Sofar API) → awaiting node → acked (st values + node id
verified) / mismatch — and warns instead of letting the operator re-send
while one is pending. Duplicate re-sends stay safe regardless (D4 dedupe).
