# Sprint12 kickoff prompt (paste into a fresh session)

Copy everything below the line.

---

You are a worker session on the BM camera project (repo `bm_cam_legacy`).
Your job is **Sprint12: remote config commands** — add `hlt` (power-halt
override) and `twn` (transmit-window override) to the camera command set so
that the two settings that stranded us on 2026-07-31 (halt mode and the
capture window) can be changed over the Spotter USB console AND the Sofar
Command API, without SSH or a site visit.

**Read first, in this order:**
1. `CLAUDE.md` (repo root) — note the Branching Model.
2. `sprints/Sprint12_remote_config_commands/SPEC.md` — the plan, including
   the safety requirements (ack-before-halt is the hard part).
3. `sprints/Sprint12_remote_config_commands/TRACKER.md` — your checklist.
4. The Sprint10/11 command machinery you are extending:
   `BM_Devel_Pi/command_tables.py`, `command_bindings.py`,
   `command_state.py`, `rc_command_hooks.py`, and Sprint11's DESIGN.md
   decisions D2 (next-boot application) and the deferred-ack work (C3).
   Do not re-litigate those decisions.

**House rules that bit previous sessions:** branch from `development`, never
commit to it directly; tick TRACKER boxes only with artifacts; the units
self-halt at cycle end (real halt since 2026-07-31) — use the
`bmcam-field-update` skill's disarm flow before touching a unit, and re-arm
when done. Delivery lag from Spotter to the Sofar API can reach ~60 min;
0 rows early is not failure.
