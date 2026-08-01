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

## 2026-08-01 ~06:30Z — SESSION END (archived mid-§5); exact hand-off state

**bmcam003: DONE, field-normal.** v4 build (ecb9a4d), armed, real halt,
daemon on, bench gate §4 fully PASSed. Spotter SPOT-33507C always-on;
console `reset` = wake lever. No action needed.

**bmcam000: v4 deployed (7d29566); USB sweep HALF done; left SAFE-IDLE:**
- Set phase 12/12 PASS: every v4 command applied + persisted via console
  `bm pub`; 88 acks on the wire (evidence: cron_logs/
  sprint12_remote_listen_loop.log on the unit + bmcam000_usb_sweep_results.txt
  in the run folder). NOTE: applies were processed by a concurrently
  running bench-cycle loop, NOT the per-step driver cycles — that loop's
  start had been REJECTED in-session but executed anyway (rejection
  raced the parallel tool call); benign here but it double-opened the
  UART (D11 violation) — incident-worthy, documented.
- Factory-reset phase INCOMPLETE: state file still holds the set-phase
  values (roi2 foc1 awb1 exp1 win1 txd5 cap1 src1 hlt2 twn2). Restore =
  ten zero-commands, or simply delete
  /home/pi/BM_Devel_Pi/bm_command_state.json (documented stock path).
- **OPEN BUG (the blocker):** console `bm pub` → Pi delivery on
  SPOT-31593C worked at ~05:18Z (cycle A: 7 frames) but has delivered
  ZERO frames since the 05:52Z Spotter reset, across 2 further resets
  and 240+ intact, error-free console sends — while spotter/utc-time
  broadcasts still reach the Pi fine (mote→Pi path alive) and the
  neighbor re-joins. Not injection (246 intact echoes), not WiFi
  (power_save now off persistently), not the Pi (loops ran). Decisive
  next experiment (defined, NOT run): replicate cycle A exactly — set
  state file aside (src back to live camera = long cycles), ONE
  listener, one burst; if that still hears nothing, the Spotter-side
  pub path is genuinely wedged → full physical power cycle of the
  SPOT-31593C setup is the next lever.
- Unit left: DISARMED (armed line saved in
  /home/pi/crontab_armed_sprint12_bmcam000.txt AND
  crontab_rearm-style backup), bus always-on, Pi idles at boot. With
  hlt=2 in state it will not halt if cycles run. Re-arm after the sweep
  finishes.
- Sofar mailbox: a benign `ping` (id 3002) still queued (+ possible
  twn 2 id 3001 straggler); dedupe makes both harmless whenever they
  land.

PR #32 open; §5 tracker updated to match this state.
