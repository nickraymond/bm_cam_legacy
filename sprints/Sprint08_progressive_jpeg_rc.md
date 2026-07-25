# Sprint 08 — Progressive-JPEG Release Candidate (deploy)

**Status:** Ready for kickoff — `‹S07›` placeholders filled from the Sprint07 verdict on 2026-07-25.
**Created:** 2026-07-23 · **Owner:** Nick
**Repo:** `bm_cam_legacy` · **Scope:** On-device Pi **runtime** — the deploy sprint (this one *does*
edit runtime code, unlike Sprint07). Read the repo-root `CLAUDE.md` first; this spec is the source of truth.
**Follows:** `Sprint07_pi_jpeg_validation.md` — its final settings table
(`mode, q_nominal, q_floor, q_max`, per-image time budget, worst-case message counts) feeds this sprint.

> Scaffold values marked `‹S07›` in the original draft are now filled from Sprint07 (all
> Pi-measured on bmcam000; see §4 for the full table + provenance). Do not re-derive them.

---

## 1. Goal

Produce a **release candidate (RC)** the Pi runs over a weekend that:

1. Transmits **progressive JPEGs** at the Sprint07-approved settings (scene frames q13,
   card-bearing frames q15; see §4).
2. **Adapts quality to a time budget** — if an image is too detailed and its estimated transmit
   would exceed the max allowable per-image run time (from YAML), step down the quality ladder and
   re-encode.
3. Is **time-aware from cycle start** — every re-encode attempt is charged against **one** total
   budget, so capture + all encode attempts + transmit never exceed the max run time.
4. Emits an **updated status message**: image format = progressive JPEG, the quality used, and the
   number of compression attempts.
5. On a **can't-fit-even-at-floor** cycle: emits a distinct **incomplete-cycle message** (parsed by
   the cycle-log tool), then best-effort transmits as many chunks as the remaining time allows.
6. On finish (or budget exhaustion): performs the **power-savings halt** validated earlier, dropping
   to low idle until the next power cycle.

**Weekend acceptance:** a soak on the Pi that demonstrably shows — progressive JPEGs sent · adaptive
quality with attempts logged · incomplete-cycle error logs when it can't fit · early-finish power halt.

---

## 2. Design principle — one budget authority, small tested modules

The worry is complexity. The fix: **do not scatter "time awareness."** There is exactly **one**
module that owns the cycle budget (M1); capture, every encode attempt, and transmit all *ask it*
whether there is time. Everything else is a narrow, independently testable module the RC path wires
together. The known-good HEIC path stays intact — progressive JPEG is a new, **config-gated** mode.

**Module map (build bottom-up, test each off-device before integrating):**

- **M1 — Cycle Time Budget (accountant).** Pure logic, no hardware. Given cycle `start`,
  `max_run_time` (YAML), and pacing (5 s/msg), it answers: time remaining · will *N* estimated
  messages fit in the remaining budget · is there room for another encode attempt. Backbone of
  requirements 2–4 and 6. **Test:** fake-clock unit tests.
  *Sprint07 sizing input:* pipeline overhead is tiny and stable — capture 4.8–5.3 s (incl. 2 s AE
  settle), native load + crop + lanczos 2.4 s, encode ≤ 0.063 s/attempt — so re-encode attempts
  cost ~0.03–0.07 s each; the budget is dominated by transmit (5 s/msg).
- **M2 — Progressive-JPEG Encoder.** Encode the frozen ROI→output at a given quality → `bytes`,
  `base64_len`, `message_count = ceil(base64_len / 300)`. Reuses the Sprint06/07 encode
  (`tools/bm_pi_jpeg_encode.py` is the validated reference implementation: Pillow
  `quality=q, progressive=True, optimize=True`, lanczos). **Test:** offline on the committed
  reference images; numbers match the Sprint07 Pi run (`p1_grid_20260724T165653Z` — byte-exact,
  108/108 sha256 vs the Mac DOE).
- **M3 — Adaptive Quality Selector.** Given M1 + M2 and the quality ladder (**q15→q13→q11→q9**;
  card frames start at q15 with floor q13, scene frames start at q13 with floor q9): encode at the
  frame type's nominal → estimate transmit → if it won't fit the remaining budget, step down and
  re-encode → repeat to the floor. Returns `(quality, attempts, bytes, fits)`. Each attempt
  consults M1 so retries never blow the budget. This is the "intelligent quality sampling."
  **Test:** synthetic high-detail images that force step-downs; assert attempts tracked, budget
  respected, floor behavior correct. (Sprint07: q9 worst case = 126 msgs always fits the reference
  fleet; q17+ is dead — never encode above q15.)
- **M4 — Uplink Message Fields.** Extend the START/END (and/or wake-status) envelope with
  `img_format=progressive_jpeg`, `q` (quality used), `enc_attempts`, and a `complete`/`incomplete`
  flag + reason. **Content-only** change (not the transport). **Test:** assert the emitted message
  strings; confirm the backend parser / cycle-log tool can read them (see §5).
- **M5 — Incomplete-Cycle Path.** When M3 hits the floor and M1 says it still won't fit: emit the
  distinct **incomplete** message (cycle-log-parseable), then transmit as many chunks as the
  remaining budget allows and stop cleanly. **Test:** force a no-fit case; assert the incomplete
  message + a bounded partial send.
  *Sprint07 P4 evidence this is worth doing:* a tail-cut progressive JPEG renders as a full-frame
  preview from ~25% received through the real backend derivative path — a bounded partial send
  still delivers a usable image.
- **M6 — Power-Savings Halt.** Thin wrapper over the already-validated halt (the ~0.26 W low-idle;
  validated on bmcam000 in the power-modes work — `tools/power/tuned_halt.sh`, PR #5. Not
  re-tested in Sprint07). Invoked at cycle end — on success *or* budget-exhausted. **Test:**
  dry-run logs the intent, then a real halt on the Pi; confirm low-power state + wake on the next
  power cycle.
- **M7 — Cycle Orchestrator (RC entry).** The new runtime path wiring M1–M6: start clock → capture →
  M3 adaptive encode → M4 status message → transmit (M5 for the incomplete/bounded case) → M6 halt.
  Config-gated so the HEIC path is untouched. **Test:** off-device dry run, then one real cycle.

**YAML config additions:** `max_run_time_min: 18`, quality ladder (`q_nominal: 13` scene /
`15` card · `q_floor: 9` scene / `13` card · `q_max: 15`), pacing (5 s/msg, 300 b64 chars/msg),
power-halt settings, and a `capture_mode: heic | progressive_jpeg` flag.

---

## 3. Work Tracker (piecemeal, dependency-ordered)

Each row is one module / testable block. Land them bottom-up; only P7 integrates and only P8 runs on
the field cadence.

| # | Block | Builds | How it's tested independently | Status | Depends on |
|---|-------|--------|-------------------------------|--------|-----------|
| P0 | Config + RC skeleton | YAML keys + a new RC entry module that loads config and logs resolved settings, no behavior | config parses; dry-run prints resolved settings | 🔍 IN REVIEW | — |
| P1 | **M1** time-budget accountant | the single budget authority | fake-clock unit tests | 🔍 IN REVIEW | P0 |
| P2 | **M2** progressive-JPEG encoder | encode + message estimate | offline on reference images vs Sprint07 Pi numbers | 🔍 IN REVIEW | P0 |
| P3 | **M3** adaptive quality selector | M1+M2 ladder step-down | synthetic high-detail images forcing step-downs | 🔍 IN REVIEW | P1, P2 |
| P4 | **M4** uplink message fields | envelope fields (format/q/attempts/complete) | emitted-string asserts + backend parse check | 🔍 IN REVIEW | P0 |
| P5 | **M5** incomplete-cycle path | no-fit message + bounded partial send | forced no-fit scenario | ☐ TODO | P3, P4 |
| P6 | **M6** power halt | wrap the tested halt | dry-run → real halt on Pi | ☐ TODO | P0 |
| P7 | **M7** orchestrator integration | wire M1–M6 into the config-gated RC path | off-device dry run → 1 real cycle on Pi | ☐ TODO | P3, P4, P5, P6 |
| P8 | Weekend RC soak | run the integrated RC on the Pi over a weekend | logs show all four behaviors (JPEG · adaptive · incomplete log · halt) | ☐ TODO | P7 |

**Legend:** ☐ TODO · 🔄 IN PROGRESS · 🔍 IN REVIEW · ✅ DONE · ⛔ DEFERRED.
Session rule (as Sprint06/07): first non-✅/⛔ row whose dependencies are ✅.

---

## 4. Sprint07 inputs — FILLED (2026-07-25, all Pi-measured on bmcam000)

Source: `Sprint07_pi_jpeg_validation.md` §7 (runs `p1_grid_20260724T165653Z`,
`p1_pi_analysis_20260725T063719Z`, `p2_cycle_20260724T170930Z`, `p4_render_20260725T070626Z`).

| Input | Value | Provenance |
|---|---|---|
| **mode** | progressive JPEG (Pillow `progressive=True, optimize=True`, default 4:2:0) | Sprint06 P2/P3; Sprint07 P1 byte-exact on Pi |
| **q_nominal** | **13** scene frames · **15** card-bearing frames | S07 P3 + card bump (2026-07-25): card never binds the coral-anchored bands — 81 msgs at q15 |
| **q_floor** | **9** (adaptive floor); **card frames floor at q13** (q9 partial card lock needs 90% received) | S07 P3 / Sprint06 P2 |
| **q_max** | **15**, gated by a pre-transmit size check (worst coral 188 msgs = 96% of cap); **q17+ dead** (206 msgs > 195 cap) | S07 P1/P3 |
| Step-down ladder | q15 → q13 → q11 → q9 (q9 worst case 126 msgs always fits the reference fleet) | S07 P3 adaptive rule |
| **max_run_time_min** | **18** (confirmed). Measured totals: q13 nominal 14.21 min (3.8 min margin) · q15 15.80 min · at the 195-msg cap 16.38 min (1.6 min margin) | S07 P2 cycle table |
| Worst-case messages | prog q13 = 169 (alt_07) · q9 = 126 · q15 = 188 · baseline q9 = 124 · card q13 = 75 / q15 = 81 | S07 P1 (Pi bytes) |
| Pacing / chunking | 300 base64 chars/msg (= 225 raw B), 5 s/msg, `message_count = ceil(base64_len/300)`; hard cap **195 msgs** (field-tested) | Sprint06, unchanged |
| Frozen geometry | ROI **1600×900** native (scene-centered `1504,846` · card-centered `1467,1255`, coords in 4608×2592 sensor-equivalent) → **1000×562** lanczos | Sprint06, S07-validated |
| Pi encoder confirmation | **Byte-identical to the Mac DOE (108/108 sha256)** — Pillow 11.3.0 / libjpeg-turbo 3.1.1 on the Pi. Encode ≤ 0.063 s, prep 2.4 s, capture 4.8–5.3 s, peak RSS ~123 MB | S07 P0/P1/P2 |
| ⚠ CMA constraint | Native 4608×2592 capture bottoms CmaFree at **1.9 MB** with `cma=128M` — 128M is a hard floor; never run concurrent CMA users during capture | S07 P2 |
| Power halt | ~0.26 W tuned halt validated on bmcam000 in the power-modes work (PR #5, `tools/power/`); **not re-tested in Sprint07** — M6 dry-run + real-halt test covers it | power sprint |
| Backend render premise | Tail-cut progressive renders via the real backend partial-derivative path (full frame from ~25% received). Residual: ~0.5–4% of cut positions decode black and are rejected to the placeholder tile (missing preview, never a wrong one) | S07 P4 |

---

## 5. Cross-repo + scope notes

- **Backend parsing is a separate change** (`nereus-vision-dev/backend`): the new message fields
  (M4) and the incomplete-cycle message (M5) must be parsed there and surfaced in the cycle-log
  tool. This spec defines the **wire fields**; the backend consumes them in its own reviewed PR.
  Track as a handoff — the weekend test needs both sides. (Sprint07 P4 already validated the
  backend's *render* side for partials; the *parser* side for the new fields is the open half.)
- **This sprint edits runtime code.** Preserve the known-good HEIC path (config-gated). Per CLAUDE.md
  §15/16: back up `/home/pi/BM_Devel_Pi` before deploying, reversible changes only, and follow the
  capture-only → compress-only → transmit sequence; no cron until manual passes.
- **Transmit-loop touch (M5).** The time-bounded / best-effort partial send is a real change to the
  send behavior — keep it minimal, behind the incomplete path, and well-tested; do not otherwise
  alter `bm_serial.py` / `send_buffers()` chunk-loop behavior.
- **Note on `rpicam-still`:** not present on the bmcam000 Bullseye image — production (and Sprint07)
  fall back to `libcamera-still` with identical args. Don't assume the `rpicam` name in RC code.

---

## 6. Design decisions to settle at kickoff (options → pros/cons → Nick approves)

- **Quality-ladder step policy:** fixed steps `q_nominal → q_floor`, vs. a measured jump that targets
  the budget in fewer re-encodes. (Sprint07 datapoint: a re-encode costs only ~0.03–0.07 s, so extra
  attempts are nearly free — the fixed ladder's simplicity likely wins.)
- **Fit-decision basis:** worst-case pacing vs. measured transmit rate for the "will it fit" estimate
  (conservative avoids blowing the budget; measured packs more quality).
- **Where the RC lives:** a new mode/flag inside `main_pi_camera.py` vs. a **separate RC entry
  script** (recommend separate, so the known-good path is untouched and the RC is testable alone).
- **Halt trigger:** only on early finish, or also after a budget-exhausted best-effort send.

---

## 7. End goal / weekend acceptance

A config-gated RC on the Pi that, over a weekend, demonstrably: sends **progressive JPEGs** at the
§4 settings; **adapts quality** to the time budget with attempts logged; emits the **updated
status message** and the **incomplete-cycle error log** when it can't fit; and performs the
**power-savings halt** on early finish. Logs pulled back for review. Production rollout is a final
reviewed step after the soak.

---

## 8. Findings log

### P0 — Config + RC skeleton (2026-07-25, 🔍 IN REVIEW)

**Kickoff decisions (Nick-approved):**
- **D1 ladder policy:** fixed ladder, YAML-tunable — single band `q_max → q_min` stepping down
  by `quality.step` (no scene/card `frame_type`; dropped as too complex). Defaults 15/9/2 →
  `[15, 13, 11, 9]`. Ladder always terminates exactly at `q_min` even on uneven steps.
- **D2 fit basis:** config pacing (`bm_serial.image_transmit_delay_seconds` × msgs) — the send
  loop is fixed-pace by design, so config pacing *is* the transmit schedule. No measured-rate model.
- **D3 RC location:** separate entry script `BM_Devel_Pi/rc_progressive_jpeg.py`; known-good
  `main_pi_camera.py` untouched (possible merge into one entry later, post-validation).
- **D4 halt trigger:** on early finish AND after budget-exhausted best-effort send, gated by
  `power_halt.enabled` with a `dry_run` mode for bench tests.
- **D5 config home:** extended the existing `CameraSchedule`/`load_camera_schedule()` in
  `spotter_time_sync.py` (all field config lives in one file/loader; no parallel rc_config).

**Landed (all additive; 100 insertions, 0 deletions on shared files):**
- `camera_schedule.yaml`: new `capture_mode: "heic"` (default = known-good path),
  `progressive_jpeg:` block (`max_run_time_min: 18`, `message_cap: 195`,
  `quality: {q_max: 15, q_min: 9, step: 2}`), `power_halt:` block
  (`enabled: false, dry_run: true, mode: halt`). Pacing/chunking intentionally NOT duplicated —
  single source of truth stays `bm_serial:` (300 chars/msg, 5 s/msg).
- `spotter_time_sync.py`: 9 new `CameraSchedule` fields (Sprint07 §4 values as defaults), parser
  sections for the two new blocks, validation. Strict RC validation is **gated on
  `capture_mode: progressive_jpeg`** so a mistyped RC block can never fail-closed a HEIC-mode
  field unit.
- `rc_progressive_jpeg.py`: P0 skeleton — resolves + prints every RC setting (ladder, budget,
  cap, pacing+source, halt, geometry), exit 0 / loud exit 2 on bad config. No camera, no serial
  writes, no behavior.
- `tests/test_rc_progressive_config.py`: 19 off-device tests (stdlib unittest; stubs `serial`
  so it runs without pyserial/PyYAML) — parser round-trip on the committed YAML, legacy-key
  regression guard, validation gating, ladder math, skeleton output. **19/19 pass** on Mac
  (Python 3.13.5).

**Not tested:** anything on the Pi (P0 is off-device by design; nothing deployed). PyYAML path of
`load_bm_serial_config` untested off-device (Mac lacks PyYAML → pacing printed `source=default`
with identical 300/5 values; on bmcam000 it should print `source=yaml`).

### P1 — M1 time-budget accountant (2026-07-25, 🔍 IN REVIEW)

**Design (Nick-approved): PURE accounting.** `CycleBudget` charges exactly what it is asked and
reserves nothing hidden — START/END overhead (+2 msgs) and any margin are the callers' job
(M3/M5), enforced by their tests. Monotonic clock by default (a mid-cycle Spotter/RTC
system-clock set cannot corrupt the budget); `clock` injectable for fake-clock tests.

**Landed:**
- `BM_Devel_Pi/rc_time_budget.py` — `CycleBudget(budget_seconds, seconds_per_message, clock)`;
  one deadline fixed at construction (= cycle start). Queries only: `elapsed_s / remaining_s`
  (clamped ≥ 0), `exhausted()`, `has_time_for(s)` (encode attempts), `messages_fit(n)`,
  `max_messages_now()` (M5 bounded partial send). Boundary pinned: an exact fit counts as
  fitting (each paced message's 5 s includes its trailing sleep). Stdlib only, no side effects.
- `tests/test_rc_time_budget.py` — **15/15 pass** off-device (fake clock, zero sleeps):
  fresh-budget facts (1080 s = 216 paced msgs, matching the P0 derived line); S07 reference
  counts (126/188 chunks + 2, cap 195 + 2 = 985 s) fit a fresh budget; one budget charged from
  cycle start (capture 5.3 s + prep 2.4 s + 3 × 0.07 s attempts); fit flips false when one
  message short; exact-fit boundary; encode-attempt window (0.03 fits / 0.07 doesn't at 0.05 s
  left); fractional pacing; exhaustion + overrun clamp (never negative, all fits false);
  invalid inputs rejected; no hidden reserves. P0 suite still 19/19.

**Not tested:** nothing on the Pi (pure logic — nothing to run there); integration with M3/M5/M7
is by design deferred to their rows.

### P2 — M2 progressive-JPEG encoder (2026-07-25, 🔍 IN REVIEW)

**Decisions (Nick-approved):** RC gets its OWN geometry keys under `progressive_jpeg:`
(`crop: {x,y,w,h}` + `output_width`), S07 scene defaults `1504,846,1600,900 → 1000` — flipping
`capture_mode` never edits shared geometry. **Confirmed: no AprilTag detection on the Pi** — the
crop is fixed config constants (the scene default is the exact center crop; card-centered
`1467,1255` is just an alternative fixed preset for bench work).

**Landed:**
- `BM_Devel_Pi/rc_jpeg_encoder.py` — M2: `prepare_source()` (native → RGB → fixed crop →
  lanczos; runs once per cycle, reused by every ladder attempt) + `encode_progressive()`
  (the exact validated Pillow call `quality=q, progressive=True, optimize=True` into memory;
  returns bytes, `base64_len`, `message_count = ceil(b64/chunk)`, sha256). Pure — caller
  persists accepted bytes.
- `camera_schedule.yaml` + `spotter_time_sync.py`: the new geometry keys (parse + RC-gated
  validation: crop within native source, `output_width ≤ crop.w`).
- `rc_progressive_jpeg.py`: skeleton now resolves/prints the RC's own geometry
  (`(1504, 846, 1600, 900) → 1000x562`) — fixes the P0 wart where it showed the HEIC crop.
- `tests/fixtures/sprint07_p1_expected.json` — committed expected-values fixture extracted from
  the local `p1_grid_20260724T165653Z` CSVs (card + coral_primary × progressive × q{7..17}),
  provenance recorded in-file.
- `tests/test_rc_jpeg_encoder.py` — **12/12 pass** on Mac. Headline: **all 8 ladder cells
  (2 sources × q{9,11,13,15}) byte-exact vs the Sprint07 Pi run** — sha256, jpeg_bytes,
  base64_len, message_count all match (valid cross-version: S07 P0 proved Mac Pillow 12.3.0 ==
  Pi 11.3.0 bytes). §4 headline counts reproduced (card q13=75, q15=81 msgs). Also pinned:
  in-memory == file-save bytes, determinism, ceil formula, loud geometry validation.
  Config suite grew to **20/20** (geometry keys + bad-crop rejection); M1 suite still 15/15.

**Not tested:** the 7 coral alts (native sources not committed — their numbers stay covered by
the S07 run itself, incl. worst-case alt_07 q13=169); encode on the Pi (S07 already proved Pi
byte-parity; RC on-device runs start at P7); prepare-time on Pi (S07: ~2.4 s).

### P3 — M3 adaptive quality selector (2026-07-25, 🔍 IN REVIEW)

**Decisions (Nick-approved):** (C1) before every encode M3 consults M1 via
`has_time_for(ENCODE_ATTEMPT_ALLOWANCE_S = 1.0 s)` — an **assumption**, ~15× the S07-measured
≤0.063 s worst attempt (pinned by a test); (C2) when even the floor fails, M3 returns the
**floor's encoded bytes** with `fits=False` — exactly what M5's bounded partial send needs.

**Landed:**
- `BM_Devel_Pi/rc_quality_selector.py` — `select_quality(source, budget, q_max, q_min, q_step,
  message_cap, chunk_b64_chars)`. A rung fits iff `message_count ≤ message_cap` AND
  `budget.messages_fit(message_count + 2)` (START/END overhead charged here, per M1's pure
  design). First fitting rung wins. Returns `quality / attempts / fits / reason
  (fit | no_fit_cap | no_fit_budget | no_time_for_encode) / encode / attempt_log` — the log
  (quality, bytes, msgs, over_cap, budget_fit per attempt) feeds M4's `enc_attempts`.
  `compute_quality_ladder` moved here from the entry script (pure module, no serial import);
  entry re-exports it.
- `tests/test_rc_quality_selector.py` — **19/19 pass** off-device. Self-calibrating forced
  step-downs on seeded-noise 1000×562 synthetics (tests measure real per-rung counts first,
  then pick cap/budget to force behavior — no magic byte counts): fit-at-top (flat image, 1
  attempt); cap-forced single step (q15→q13, budget fine); budget-forced double step (q15→q11);
  elapsed-time charge (same budget fits q13 fresh but only q11 after the clock eats the slack);
  no-fit-at-floor for BOTH reasons (cap and budget) returning floor bytes sha-verified against
  a direct M2 encode with all 4 attempts logged; the +2 START/END boundary (chunks-only budget
  fails, chunks+2 passes); encode-guard refusal at <1 s remaining (attempts=0, encode=None).
  All other suites still green (config 20, budget 15, encoder 12).

**Not tested:** nothing on the Pi (pure logic); real capture-fed selection is P7; the
1.0 s allowance is an assumption until the P7 on-device cycle confirms attempt costs match S07.

### P4 — M4 uplink message fields (2026-07-25, 🔍 IN REVIEW)

**Decisions (Nick-approved):** compact keys in the existing envelope style; incomplete-cycle
message = WS compact shape with new action `a=inc`; builders live in a new pure RC module —
`process_image_v2.py`'s HEIC builders untouched (formatting helpers imported, not copied).

**Wire-field contract (BACKEND HANDOFF — parse these in `nereus-vision-dev/backend` + cycle-log
tool, separate reviewed PR):**

| Field | Key | Values | Rides in |
|---|---|---|---|
| image format | `fmt` | `pjpg` | START (`fmt=pjpg`), END (`fmt: pjpg`), incomplete |
| quality used | `q` | int — reuses the existing q key (HEIC cycles carried HEIC quality) | START/END/incomplete |
| encode attempts | `att` | int (M3 ladder walk length) | START/END/incomplete |
| complete flag | `cmp` | `1` / `0` | START/END |
| incomplete reason | `rsn` | `budget` \| `cap` \| `enc` \| `err` | START/END when `cmp=0`; always in incomplete |
| planned / sendable chunks | `pln` / `snd` | ints (`snd=0` valid) | incomplete only |
| incomplete-cycle message | `<WS v=1 a=inc fmt=pjpg q=9 att=4 rsn=budget pln=128 snd=37 ct=… sha=… hn=…>` — emitted BEFORE the bounded partial send | | M5 |

**Landed:**
- `BM_Devel_Pi/rc_uplink_messages.py` — `build_rc_start_message` (same base shape + budget
  discipline as the HEIC START; RC fields never dropped; HEIC-style `q` filtered from
  ride-along metadata so one q key per message), `build_rc_end_message` (wraps the existing END
  builder — RC fields are core fields, camera metadata stays budget-dropped),
  `build_rc_incomplete_message` (pure; telemetry extras caller-supplied), `reason_code()`
  mapping M3 reasons → wire codes with `err` fallback (never raises).
- `tests/test_rc_uplink_messages.py` — **17/17 pass**: exact-string asserts (complete +
  incomplete variants), `rsn` only when `cmp=0`, no q duplication, 285/295-byte budgets held
  under hostile inputs with RC fields surviving drops, ASCII sanitization, `snd=0` valid, and
  probe-style key/value extraction recovering every new field from every message type (the
  in-repo stand-in for the backend parser). All five suites green (20+15+12+19+17 = 83).

**Not tested:** the actual backend parser / cycle-log tool (separate repo + PR — the table above
is the contract); on-Pi emission (P7).
