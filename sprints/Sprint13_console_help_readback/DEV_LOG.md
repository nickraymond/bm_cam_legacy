# Sprint13 — Dev log

## 2026-08-01 (session 1) — chunk 1: tables v5, renderer, tmz, tests

- Pre-flight: PR #32 merge into development verified (5d16747); branch
  sits on it. The T1 partial results (fprintf = SD-only on v2.16.6, no
  console echo; SD path mystery) were NOT committed anywhere — they
  arrived via the kickoff brief; recorded in SPEC/DESIGN now.
  Sprint12 §5 (bmcam000 remote path) confirmed never completed —
  mailbox hazard noted in TRACKER, unit untouched this sprint.
- bm_serial.py audit (Nick asked): exactly two publish methods exist —
  spotter_tx (spotter/transmit-data: console-visible but CELLULAR
  QUEUE, costs quota) and spotter_log (spotter/fprintf: SD-only per
  T1). No console-print method in the repo; `spotter/printf` (bm_core's
  console printf, public-SDK knowledge) is the chunk-2 probe.
- Content drafts iterated with Nick to an approved v2 layout (one value
  per line, boxed cfg tables, quick actions with plain-English intent).
  His table changes, all in tables v5 (D-S13-4..7): roi pixels-first
  labels + 800x450/640x360 reef-test crops; exposure-bias naming; win
  0=12 min AS PRODUCTION SPEC (profiles moved 16→12 in the same
  commit); hlt customer labels (power savings / developer mode); awb
  custom preset DROPPED (untested values don't ship); tmz command
  (LA/NY/UTC) — audit category-C revision, gate override plumbed via
  the D-S12-6 pattern.
- New: command_help.py (render_help 143 lines / render_cfg boxed
  3-group table, both pure + table-generated, ASCII <= 72 enforced);
  QUERY_COMMANDS class (help/cfg — dedupe-id-only record, v optional).
- Caught in review: sub-1000px roi crops would have CRASHED the overlay
  (output_size_for_crop raises on upsample) — clamp added + tests.
  Also cfg column overflow on long trigger labels — generic clip added.
- Tests: 542 green (was 507) at chunk-1 commit. New: content-complete help (every
  command + every index + every note), example lines round-trip
  parse_command (a help example can never go stale), cfg source-column
  semantics incl. index-0, query no-state-change + dedupe, tmz
  overlay/gate/kwargs, roi clamp. Fallout updated: command set/version
  pins, ack byte-pin (+tmz), win reorder, awb removal (gains path kept
  covered via patched temp entry), repo-YAML 12-min pins.

## 2026-08-01 (session 1, overnight) — chunk 2: console transport

- Nick (before bed): live demo in the AM — he will trigger trg 2 (camera
  capture+send) and trg 3 (reef transfer) himself as the acceptance test
  before approving the PR. Network access to this session STOPPED for the
  night: no SSH deploy, no Sofar API; console reset also skipped (no
  deploy possible → no reason to power-cycle; zero SD-corruption risk
  overnight). Deploy to bmcam003 becomes the pre-demo step.
- Nick decision (D-S13-9): NO SD+cat — add the Sofar SDK's console
  write to our bm_serial.py. Done: `spotter_print()` (spotter/printf,
  fname_len=0, frame mirrors spotter_log). No local SDK copy on this
  Mac + no network → layout DERIVED not copied; bench echo on v2.16.6
  is the proof gate, diff vs real SDK when network returns.
- Daemon wiring: query responses render via make_query_render_fn (over
  the RESOLVED settings dict) → console queue → drain_console at idle
  drains, listen windows, and pre-halt (help must beat the power cut).
  Duplicate id acks WITHOUT re-print (D-S13-10). mock_mote now decodes
  spotter/printf frames for off-device runs.
- Tests 550 green (+8): frame byte-layout, console-vs-cellular
  separation, dedupe no-reprint, send-failure requeue, renderer-failure
  isolation, hooks flush ordering.
- docs/bmcam_command_reference.md → tables v5 (tmz, awb drop, win
  order, exposure-bias wording, on-console help pointer).
