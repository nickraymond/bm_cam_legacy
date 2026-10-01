# Release R1 — remote control + self-healing media (plan of record)

Owner: Nick (release decision). Coordinator: EM session "Engineering Manager coordination".
Locked with Nick 2026-10-01. Times are **PDT (UTC−7)**. Ship target: **Fri 2026-10-09**.

Every session working toward this release reads this file first. If your work changes a date,
a gate or a scope line, tell the EM session (one line) — do not edit the plan silently.

---

## 1. Definition of done (Nick, 2026-10-01)

| # | outcome | measured by |
|---|---|---|
| D1 | **Self-healing:** lost chunks are re-requested and completed by the backend, no human | 100 % of clips complete within **3 h** of capture; **0 redundant heals** |
| D2 | **Remote control from a frontend UI:** change exposure, crop or video settings on a unit and see whether it is in effect; a bad value is refused before it is sent; **no unit ever needs SSH to recover** | UI demo on staging against a bench unit (Nick tests it); API "hard mode" edge-case suite PASS |
| D3 | **On-demand capture:** `trg` returns a still or a clip | media row complete per trigger |
| D4 | **Visible:** every command, ack and heal shows on logs.html | spot check in every gate's RESULTS.md |
| D5 | **Proven:** soak in production mode defaults (bus 10 min on / hour, heal cap 24/day) passes on the release units | G4 + G5 below |

**Release units:** bmcam003 (SPOT-33507C) and bmcam004 (SPOT-31593C). bmcam001/002 get upgraded
**after** R1 is stable (they run legacy `main`, v8 verbs only — no `set`).

**Commands are issued two ways:** the UI (Nick confirms it works) and the API/script "hard
mode" (Test Engineer drives limits and edge cases).

## 2. Gates (each gate = a RESULTS.md with PASS/FAIL per criterion)

| gate | when (PDT) | what | pass | owner |
|---|---|---|---|---|
| **G1** R5 indoor 24 h auto-heal | ends Thu 10/1 ~22:30 | Sprint26 R5 (KICKOFF §3): conductor + backend auto-send, both rigs, indoors = signal-constrained worst case | 0 clips lost, 0 redundant heals, every command on logs.html; `REASK_S` chosen from the data | S6b HIL session |
| **G2** config UI | Fri 10/2 → Mon 10/5 | frontend on the Sprint27 API (`/remote-config/*`), on staging | Nick changes exposure, crop, a video setting on a bench unit; sees refused / sent / acked / in effect | UI session (nvd) |
| **G3** hard mode | Sat 10/3 → Mon 10/5 | API/script edge-case suite on bmcam003/004: every control-tier key, boundary values, refusals, too_big, next_boot keys, back-to-back sets, set during a clip, reset | every refused value refused before send; every sent value acked; 0 units needing SSH | Test Engineer |
| **freeze** | Mon 10/5 EOD | RC1 = development tip; production config on both units (10 min/hr bus window, heal cap 24/day) | release notes list every merged PR | EM + Nick |
| **G4** outdoor tethered 12 h | Tue 10/6 | units outside in the box, mains power, nereus000 on the consoles | D1, D3, D4 hold; no bus drops | Test Engineer + Nick (box) |
| slack | Wed 10/7 | one day to fix + re-run a failed gate | — | — |
| **G5** outdoor solar 24 h | Thu 10/8 08:00 → Fri 10/9 08:00 | Spotter solar, **no nereus000**, no human | D1, D3, D4 hold for 24 h; commands only via cellular | Test Engineer |
| **ship** | Fri 10/9 | Nick decides: development → main | — | Nick |

After ship, G5 keeps running through the weekend as a soak (not a release gate).

## 3. Workstreams

| stream | repo | session | delivers |
|---|---|---|---|
| Sprint26 R5 close-out | both | "Run S6b HIL: backend auto-sends heal commands" | G1 RESULTS.md, `REASK_S` recommendation |
| Sprint27 remote config | both | "Spec remote camera config from the backend" | bm #98 (video set safety rule + catalog), nvd `/remote-config/*` PR, bench ladder |
| /dev/shm render wipe | bm | "Fix supervisor settings re-resolve tmp-file error" | bm #97 merged + deployed; host fix `RemoveIPC=no` on bmcam003/004 (approved by Nick 2026-10-01) |
| Config UI | nvd | "Build remote-config UI for Release R1 (G2)" | G2; local demo ready 2026-10-01 |
| Per-Spotter rollout settings | nvd | "Build S6b backend code: Sofar sender, auto-send, logs" | self_heal / remote_commands / link / heal cap on `external_gateways`, admin API; replaces the `*_DEVICES` env lists; merged before freeze |
| Test Engineer | `nereus_HIL_tooling` (new repo) | new session | G3, G4, G5; sole bench owner after G1. For R1 it runs the existing tools (conductor, ladder scripts) and moves them into the new repo as it goes — the repo build never blocks a gate |

Retired: "Finish S5 details, then take the rig for 24 h gate" (superseded by G1 + G4/G5).

## 4. Rules for every session

- **Bench:** one owner at a time. Until G1 closes: the S6b HIL session. After: the Test Engineer.
  Dev sessions do not touch nereus000, the Spotters or bmcam003/004 without the owner's hand-over.
- **Report to the EM (one line, via session message)** when: a PR is ready for Nick, a gate
  passes or fails, or you are blocked. No status chatter.
- **Gate evidence:** `runs/<gate>_<date>/RESULTS.md` with a PASS/FAIL row per criterion.
  Trust artifacts, not exit codes (CLAUDE.md).
- **Merges:** Nick merges. bm PRs → `development`; nvd PRs → `staging`
  (open nvd PRs only after any staging demo push — see the #59 trap).
- **Decisions** go to Nick with options, numbers and a recommendation.

## 5. Decision log (Nick, 2026-10-01)

| topic | ruling |
|---|---|
| Test Engineer | yes; own repo `nereus_HIL_tooling` + session (Nick sets up); sole bench owner after G1; runs the #97 host fix |
| G5 | 24 h solar unattended (not 48 h) |
| `BM_HEAL_AUTOSEND` | stays live after G1; cap → 24/day at freeze |
| `REASK_S` | decided from G1 results (indoor = worst case) |
| Sprint27 Q1 auth | two roles: viewer (no admin token), admin (admin token, may send commands); role check in one place — user accounts coming soon |
| Sprint27 Q2 tiers | **scope add:** all 7 `camera.image_processing.*` keys editable with rpicam-validated ranges/enums; `exposure.mode`, `still.save.quality` stay read-only |
| video retry | yes: a clip that fails with custom camera controls is retried once with defaults (makes "a bad value never loses clips" true) |
| Sprint27 Q3 | `mode.output=save_local` from the UI: next sprint |
| Sprint27 Q4 | ship the camera video safety rule in R1 |
| Sprint27 Q6 | "capture now with settings" button: next sprint |
| Sprint27 Q7 | message caps (still + video) warn > 300, refuse > 500; budgets warn 18 / refuse 30 min; WB gains refuse > 8.0 |
| staging demo | temporary push of s27 + UI after G1, Nick tests, reset, then PRs |
| rollout model | **per-Spotter** settings (not per-device Render env); global env = kill switches only; iridium defaults OFF. Fri demo uses the env once |

Note: a 500-message cap at the validated 1.3 s/msg is 650 s, longer than the 10-min production
bus window; caps above ~450 only complete on a longer window.

## 6. Open risks

| risk | effect | mitigation |
|---|---|---|
| Scope added 2026-10-01 (image keys + video retry ~10 h, per-Spotter settings) | uses the Wed slack day | estimates requested from the owning sessions; EM flags any slip at once |
| Spotter queue overflow (S5 F1) | re-asks cost 90 min each | D1 allows 3 h; outdoors should have better signal |
| bmcam004 bus drops (3× on 2026-09-30, cause unknown) | G4/G5 failure | Test Engineer watches the bus during G3/G4 |
| G5 has no console | a wedged unit can only be recovered by cellular command | G3 proves remote recovery first |
| bmcam001 0 complete images since 2026-09-01 (TODO-SPOT-001) | post-R1 field upgrade | out of R1 scope; Nick owns |
