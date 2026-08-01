# Sprint12 — Dev log

## 2026-07-31 (session 1) — chunks 1–3: code, tests, docs

- Chunk 1 (`86b360d`): HLT/TWN/TRG tables (v3), pending_trigger state slot.
- Chunk 2 (`25334e5`): overlays, gate window_override, trigger orchestration,
  ack-before-halt + trg3 end-to-end off-device tests. 257 green.
- Chunk 3 (`c6a51c7`): daemon enabled in 4 profiles + template (template had
  no island at all), docs/bmcam_command_reference.md, hotspot-skill Phase 0,
  REMOTE_CONFIG_AUDIT.md. Full suite 503 green (after merging development's
  GUI-test fix).
- Scope note: `trg` (incl. reference-image triggers) added mid-sprint,
  Nick-approved; "make ALL YAML remote" explicitly deferred to Sprint13
  (audit doc is the input). Config-readback (`post`-style) is the Sprint13
  headline request.

## 2026-08-01Z (same evening) — chunk 4: bench validation on bmcam003

Setup: Spotter SPOT-33507C always-on on USB (`/dev/cu.usbmodemSPOT_33507C1`);
`reset` via console = the wake lever for the real-halted Pi (Nick's call).
Unit caught awake + disarmed post-reset; field-updated to branch sha
`c6a51c7` (rc_field_update PASS, drift = bm_commands island only); bmcam003
profile installed → daemon enabled. Artifacts: `runs/sprint12_bench_20260731/`.

Injection lessons (cost 3 cycles, all documented in cycle1-3 logs):

1. **Console eats the first byte** of an injected line intermittently
   (`bm pub` → `m pub`). Mitigation: echo-verify + retry (inject_cmd.sh).
2. **A pub before the daemon's subscribe frame is lost silently** — the
   16 s bench cycle makes single-shot timing a coin flip.
3. **Fix that works: blanket injection** (spam_cmd.sh, 10 sends / 2.5 s
   apart across the whole window) — dedupe makes repeats free BY DESIGN;
   cycle 4 log shows 20 frames → 2 applied + 18 duplicate-acked. For field
   use none of this matters (the mailbox re-send doctrine already is the
   blanket), but bench operators should use spam_cmd.sh, not single shots.

Hardware evidence (cycle logs in the run folder):

- **hlt 2 + ack-before-halt** (cycle4_hlt2.log): `applied id=2004 hlt=2`,
  2 acks on the wire, THEN `halt=halt_initiated` — the cycle halted with
  its boot settings (real), exactly D2. Next boot (cycle 5):
  `power_halt: enabled=True dry_run=True ... source=command hlt=2` +
  dry-run notice; **box stayed up** — validation cadence no longer needs a
  Spotter reset per cycle from here on.
- **skip_win baseline**: gated --transmit at 22:34 EDT → `Outside transmit
  window 10:00-15:00` → skip_win WS sent, no image.
- **twn 2** (cycle C, sprint12_bench_C_twn2_transmit.log): after commanded
  wide window, same gated --transmit → `Within transmit window 00:01-23:59
  (command override)` → REAL image q80/142 msgs transmitted at 22:35 EDT.
- **twn 0 + trg 3 in the C4 tail**: both applied + acked in the 150 s
  post-transmit listen (finding-006's field-realistic arrival path);
  state after: twn=0, pending_trigger={id:2007, value:3}.
- **trg 3** (cycle D, sprint12_bench_D_trg3.log): boot consumed the
  trigger — `window gate BYPASSED for this boot only`, reef reference
  staged, `camera skipped this boot; persisted src setting untouched`,
  reference transmitted despite the YAML window being restored (which
  had just blocked the baseline run).
- **hlt 0 restore** (cycle D tail + restore_verification.txt): applied
  id=2008; state hlt=0/twn=0/pending_trigger null, applied_ids
  [2004..2008]; next-boot print-config shows power_halt AND window both
  `source=yaml` — full YAML governance restored, real halt re-armed.
- End state: cron re-armed from the saved armed backup; box up on the
  sprint build with daemon on; next boot cycle will real-halt
  (field-normal). Bench gate (§4) fully ticked; two quota images spent
  (twn-opened camera image + trg-3 reference, both COMPLETE at the
  Spotter — Sofar API rows to be confirmed in chunk 5's window).

## 2026-08-01Z (late) — v4 all-day window, bmcam000 hunt, close-out

- **tables v4 / D-S12-9** (`ecb9a4d`): Nick asked for a true 24 h window
  ("00:00-24:00?"). Tested first: "24:00" rejected by the parser, and an
  equal start/end pair was an UNSATISFIABLE empty window (never-transmit
  trap). Gate now defines start==end = full-circle ALL DAY; twn 2 =
  00:00-00:00. 507 tests green. bmcam003 redeployed to v4 + re-armed
  (verified on-unit: tables v4, twn2 = 00:00/00:00).
- **bmcam000**: correct IP, seen online at ~03:20Z (its 15/45 window),
  but watcher v1's 2 s SSH ConnectTimeout can never complete the DERP
  relay handshake ("relay sfo") — bmcam003 never hit this because it is
  LAN-direct. Watcher v2 (10 s timeout, back-to-back attempts) hunting;
  deploy + §5 remote validation proceed when caught.
- **Close-out per Nick**: ROI sweep deferred to Sprint14 (after Sprint13
  `help` is merged AND Nick-tested); Sprint13 specced (console `help` —
  verbose, Spotter-help style, NOT `hlp` — and post-style `cfg` dump;
  customer-facing, copy-paste examples); Sprint14 soak specced. PR to
  development opened for review.
