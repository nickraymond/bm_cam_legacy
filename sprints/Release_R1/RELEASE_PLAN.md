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

## 2a. Timeline pulled in (Nick, Fri 2026-10-02 PM)

G1, G2, G3 passed early and every R1 PR is merged. The outdoor box is ready **Sat 10/3 AM**.

| when (PDT) | step |
|---|---|
| Fri 10/2 | #106 checks M1–M6 on both units; **code freeze Fri EOD**; production config set (per-boot wake, Spotter bus 10 min/hour, heal cap 24/day per Spotter) |
| Fri 10/2 20:00 → Sat 10/3 08:00 | **G4** outdoor 12 h, nereus000 on the consoles, production mode (pulled in again by Nick, Fri 19:15) |
| Sat 10/3 08:00 → Sun 10/4 08:00 | **G5** outdoor 24 h on solar, no nereus000, no human (daylight start) |
| Sun 10/4 | exit review → **ship decision** (5 days early); Mon–Fri = slack for any fix + re-run |
| after exit | next sprint: RAW → JPEG-XL (own sprint, Nick 2026-10-01) |

## 2c. R1 scope refined (Nick, Mon 2026-10-05). Ship Fri 10/9

R1 is the next release and contains:
1. **Remote commands:** fix camera settings without a diver (Sprint26/27 + the command-resilience fixes; R1G PASS 10/5).
2. **RAW images in some capacity:** JPEG-XL stills (Sprint28, bm #120) captured at the **lowest possible analog gain**
   so colour correction doesn't boost red-channel noise (gain-priority exposure: gain at the sensor floor, shutter
   up to a cap, raise gain only when capped; accept a darker RAW rather than exceed the cap; the cap is a setting).
3. **Backend + frontend support** for the new image types and controls: ingest/render/DNG/download (nvd #83/#90,
   live), remote-config UI for still.format, raw and exposure keys.

The two epics (message transmission reliability, PR #129; JPEG-XL quality/budget) are sub-categories of this same
work. R1 ships what is proven by Thursday; the epics continue after.

| day | step |
|---|---|
| Mon 10/5 | low-gain exposure built (camera), remote-config UI check (backend/QC); bmcam004 R4 (12 nrjxl wakes) running |
| Tue 10/6 | R4 verdict; #120 merged to development after the TE gate; low-gain bench on nereus002 + bmcam004 |
| Wed–Thu | **RC gate:** development tip (commands + nrjxl + low gain) on both units, ≥ 24 wakes, outdoor |
| Fri 10/9 | ship decision: development → main |

## 2b. Re-plan after G4 FAIL (Nick, Sat 2026-10-03)

G4 FAILED (`runs/g4_outdoor12h_20261002/RESULTS.md`): D1 17/28, trg 2/6 (budget defect), 9/32 acks lost at
the Spotter. Nick: fix now; ship target back to **Fri 10/9**; no G5 before ship (solar 24 h runs after).

**R1 exit = commands first** (replaces D1's 3 h limit for the ship gate):
- every command confirmed at the backend (direct ack, re-sent `d:1` ack, or heartbeat hash);
- every trg delivers its clip or reports its failure (`tr=` on `<WS>`); none silent;
- no unit needs SSH;
- every clip completes eventually, with 0 redundant heals. The 3 h target moves to the comms-reliability sprint (C1).

| rig | unit / Spotter | role |
|---|---|---|
| repair | bmcam003 / SPOT-33507C (worst in G4) | iterative R1 fixes, command resilience (R1F baseline, bm #121 gate) |
| comms | bmcam004 / SPOT-31593C | C1 comms reliability: :15 vs :00 bus start, then message size (`hil/gates/C1_comms_reliability.md`) |

Fixes: bm #121 (trg budget, ack re-send, `tr=`), nvd #84 (merged: heartbeat fallback, `tr=` parse), bm #122 (END temp
after burst). R1.1: camera `post` command (`sprints/R1.1_post_command/SPEC.md`, approved).

The table below is the original plan, kept for reference.

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
| G1 result (bm #102) | backend auto-heal PASS both rigs (60/60 heals, 0 redundant); 4 bmcam004 clips lost Spotter-side (SPOT-31593C mem_fault_reboot FW v2.16.8 + 3 h no GPS) |
| D1 counting | D1 judged on clips that reached Sofar; Spotter-side whole-clip losses reported separately as an external defect; Nick files a Sofar ticket. Next sprint: heartbeat lists recent clip keys so the backend can detect + re-request whole lost clips |
| G1 cut | ended 22:30 PDT 10/1 with no drain (Nick); clips in flight = "stopped early" |
| `REASK_S` | keep 5400 s (only 3/60 heals needed a re-ask) |
| rollout model | **per-Spotter** settings (not per-device Render env); global env = kill switches only; iridium defaults OFF. Fri demo uses the env once |
| G4 FAIL (10/3) | fix now, ship Fri 10/9; no G5 before ship; worst rig = repair rig; R1 exit = commands first, 3 h D1 → comms sprint (§2b) |
| ack fixes (10/3) | approved: camera re-sends last 2 boots' acks as `d:1`; `tr=<id>:sent\|budget\|fail` on `<WS>` (additive wire); backend hash fallback |
| `post` verb (10/3) | approved: terse console self-report + one `<PS>` line, on request only; nereus000 may auto-post to bench rigs each wake |

Note: a 500-message cap at the validated 1.3 s/msg is 650 s, longer than the 10-min production
bus window; caps above ~450 only complete on a longer window.

## 6. Open risks

| risk | effect | mitigation |
|---|---|---|
| Scope added 2026-10-01 (image keys + video retry ~10 h, per-Spotter settings) | uses the Wed slack day | estimates requested from the owning sessions; EM flags any slip at once |
| Spotter queue overflow (S5 F1) | re-asks cost 90 min each | D1 allows 3 h; outdoors should have better signal |
| SPOT-31593C (bmcam004) firmware: mem_fault_reboot + no-GPS silent drops; likely also the 9/30 bus drops | whole clips lost in G4/G5 (counted separately from D1) | Sofar ticket; Test Engineer logs Spotter reset reason + GPS state in every gate |
| G5 has no console | a wedged unit can only be recovered by cellular command | G3 proves remote recovery first |
| bmcam001 0 complete images since 2026-09-01 (TODO-SPOT-001) | post-R1 field upgrade | out of R1 scope; Nick owns |
