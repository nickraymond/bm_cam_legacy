# Sprint 08 — Progressive-JPEG Release Candidate (deploy) — SCAFFOLD

**Status:** Scaffold / not started. **Fill the `‹S07›` placeholders from Sprint07's verdict before kickoff.**
**Created:** 2026-07-23 · **Owner:** Nick
**Repo:** `bm_cam_legacy` · **Scope:** On-device Pi **runtime** — the deploy sprint (this one *does*
edit runtime code, unlike Sprint07). Read the repo-root `CLAUDE.md` first; this spec is the source of truth.
**Follows:** `Sprint07_pi_jpeg_validation.md` — its final settings table
(`mode, q_nominal, q_floor, q_max`, per-image time budget, worst-case message counts) feeds this sprint.

> This is a **scaffold**: values Sprint07 will produce are left as `‹S07›`. Do not invent them.

---

## 1. Goal

Produce a **release candidate (RC)** the Pi runs over a weekend that:

1. Transmits **progressive JPEGs** at the Sprint07-approved settings (`‹S07›`).
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
- **M2 — Progressive-JPEG Encoder.** Encode the frozen ROI→output at a given quality → `bytes`,
  `base64_len`, `message_count = ceil(base64_len / 300)`. Reuses the Sprint06/07 encode. **Test:**
  offline on the committed reference images; numbers match the Sprint07 Pi run.
- **M3 — Adaptive Quality Selector.** Given M1 + M2 and a quality ladder (`q_nominal → q_floor`,
  `‹S07›`): encode at nominal → estimate transmit → if it won't fit the remaining budget, step down
  and re-encode → repeat to the floor. Returns `(quality, attempts, bytes, fits)`. Each attempt
  consults M1 so retries never blow the budget. This is the "intelligent quality sampling."
  **Test:** synthetic high-detail images that force step-downs; assert attempts tracked, budget
  respected, floor behavior correct.
- **M4 — Uplink Message Fields.** Extend the START/END (and/or wake-status) envelope with
  `img_format=progressive_jpeg`, `q` (quality used), `enc_attempts`, and a `complete`/`incomplete`
  flag + reason. **Content-only** change (not the transport). **Test:** assert the emitted message
  strings; confirm the backend parser / cycle-log tool can read them (see §5).
- **M5 — Incomplete-Cycle Path.** When M3 hits the floor and M1 says it still won't fit: emit the
  distinct **incomplete** message (cycle-log-parseable), then transmit as many chunks as the
  remaining budget allows and stop cleanly. **Test:** force a no-fit case; assert the incomplete
  message + a bounded partial send.
- **M6 — Power-Savings Halt.** Thin wrapper over the already-validated halt (the ~0.26 W low-idle).
  Invoked at cycle end — on success *or* budget-exhausted. **Test:** dry-run logs the intent, then a
  real halt on the Pi; confirm low-power state + wake on the next power cycle.
- **M7 — Cycle Orchestrator (RC entry).** The new runtime path wiring M1–M6: start clock → capture →
  M3 adaptive encode → M4 status message → transmit (M5 for the incomplete/bounded case) → M6 halt.
  Config-gated so the HEIC path is untouched. **Test:** off-device dry run, then one real cycle.

**YAML config additions:** `max_run_time_min`, quality ladder (`q_nominal / q_floor / q_max` =
`‹S07›`), pacing, power-halt settings, and a `capture_mode: heic | progressive_jpeg` flag.

---

## 3. Work Tracker (piecemeal, dependency-ordered)

Each row is one module / testable block. Land them bottom-up; only P7 integrates and only P8 runs on
the field cadence.

| # | Block | Builds | How it's tested independently | Status | Depends on |
|---|-------|--------|-------------------------------|--------|-----------|
| P0 | Config + RC skeleton | YAML keys + a new RC entry module that loads config and logs resolved settings, no behavior | config parses; dry-run prints resolved settings | ☐ TODO | — |
| P1 | **M1** time-budget accountant | the single budget authority | fake-clock unit tests | ☐ TODO | P0 |
| P2 | **M2** progressive-JPEG encoder | encode + message estimate | offline on reference images vs Sprint07 Pi numbers | ☐ TODO | P0 |
| P3 | **M3** adaptive quality selector | M1+M2 ladder step-down | synthetic high-detail images forcing step-downs | ☐ TODO | P1, P2 |
| P4 | **M4** uplink message fields | envelope fields (format/q/attempts/complete) | emitted-string asserts + backend parse check | ☐ TODO | P0 |
| P5 | **M5** incomplete-cycle path | no-fit message + bounded partial send | forced no-fit scenario | ☐ TODO | P3, P4 |
| P6 | **M6** power halt | wrap the tested halt | dry-run → real halt on Pi | ☐ TODO | P0 |
| P7 | **M7** orchestrator integration | wire M1–M6 into the config-gated RC path | off-device dry run → 1 real cycle on Pi | ☐ TODO | P3, P4, P5, P6 |
| P8 | Weekend RC soak | run the integrated RC on the Pi over a weekend | logs show all four behaviors (JPEG · adaptive · incomplete log · halt) | ☐ TODO | P7 |

**Legend:** ☐ TODO · 🔄 IN PROGRESS · 🔍 IN REVIEW · ✅ DONE · ⛔ DEFERRED.
Session rule (as Sprint06/07): first non-✅/⛔ row whose dependencies are ✅.

---

## 4. Placeholders to fill from Sprint07 (`‹S07›`)

- `q_nominal`, `q_floor`, `q_max` (progressive JPEG).
- `max_run_time_min` — the per-image cycle budget (Sprint07 works to ≤18 min; confirm the final number).
- worst-case message counts + pacing confirmation.
- frozen geometry (ROI / output size) carried from Sprint06/07.
- confirmation the Pi encoder + power halt behave as measured on the target board.

---

## 5. Cross-repo + scope notes

- **Backend parsing is a separate change** (`nereus-vision-dev/backend`): the new message fields
  (M4) and the incomplete-cycle message (M5) must be parsed there and surfaced in the cycle-log
  tool. This spec defines the **wire fields**; the backend consumes them in its own reviewed PR.
  Track as a handoff — the weekend test needs both sides.
- **This sprint edits runtime code.** Preserve the known-good HEIC path (config-gated). Per CLAUDE.md
  §15/16: back up `/home/pi/BM_Devel_Pi` before deploying, reversible changes only, and follow the
  capture-only → compress-only → transmit sequence; no cron until manual passes.
- **Transmit-loop touch (M5).** The time-bounded / best-effort partial send is a real change to the
  send behavior — keep it minimal, behind the incomplete path, and well-tested; do not otherwise
  alter `bm_serial.py` / `send_buffers()` chunk-loop behavior.

---

## 6. Design decisions to settle at kickoff (options → pros/cons → Nick approves)

- **Quality-ladder step policy:** fixed steps `q_nominal → q_floor`, vs. a measured jump that targets
  the budget in fewer re-encodes (each re-encode spends time — see M1).
- **Fit-decision basis:** worst-case pacing vs. measured transmit rate for the "will it fit" estimate
  (conservative avoids blowing the budget; measured packs more quality).
- **Where the RC lives:** a new mode/flag inside `main_pi_camera.py` vs. a **separate RC entry
  script** (recommend separate, so the known-good path is untouched and the RC is testable alone).
- **Halt trigger:** only on early finish, or also after a budget-exhausted best-effort send.

---

## 7. End goal / weekend acceptance

A config-gated RC on the Pi that, over a weekend, demonstrably: sends **progressive JPEGs** at
`‹S07›` settings; **adapts quality** to the time budget with attempts logged; emits the **updated
status message** and the **incomplete-cycle error log** when it can't fit; and performs the
**power-savings halt** on early finish. Logs pulled back for review. Production rollout is a final
reviewed step after the soak.

---

## 8. Findings log
_(fill in as blocks land)_
