# Kickoff: new EM session, Mon 2026-10-12 (sprint "Pool fast-follows")

Drafted Sun 10/11 5:30 PM PDT by the weekend EM (session "EM [9de113]"). Start the new session AFTER the Monday
7:35 AM read-out has been posted and the weekend command crons are stopped (the outgoing EM does both).
The read-out numbers land in `docs/em/HANDOVER_2026-10-12.md` Monday morning; this file is the plan.

## Paste this as the first message of the new session (working directory: bm_cam_legacy)

> You are the Engineering Manager (EM) for Nereus Vision's Bristlemouth camera program, taking over Mon 10/12 after the
> weekend RC2 soak. Read in order: `docs/em/EM_HANDBOOK.md`, `docs/em/NICK_FEEDBACK_2026-10-08.md`,
> `docs/em/HANDOVER_2026-10-12.md` (weekend verdict + live state), `docs/em/KICKOFF_EM_2026-10-12.md` (this sprint),
> `docs/em/WEEKEND_RUNBOOK_2026-10-10.md` §7 (Monday queue, findings). Board: https://claude.ai/artifact/R4bv59riyrbtbkKcAmyXZM
> (data in its db; update via ArtifactData with if_version; stamp with `date`).
>
> Sprint goal: get the pool fast-follows in flight and the two bench units pool-ready for Nick's **Tuesday 10/13 pool test**
> (8 ft, v3 card). One build day. Anything not mergeable + bench-checked by Monday evening goes to the post-pool list;
> say so instead of squeezing it in.
>
> Your first three actions:
> 1. Message TE2 ("Take over as Test Engineer (bench owner)") and the "Progressive JPEG XL preview" session by exact name:
>    introduce yourself, ask for a 3-line status (task, blockers, context fill). Flag any session stuck on a prompt to Nick.
> 2. Confirm with Nick, one card at a time: (a) the Tuesday pool time and which units go in the water, (b) the Monday merge
>    order below, (c) the three open decisions d28 / d29 / d30. Define every test with question, conditions, n, metric and
>    stop rule before it runs (handbook §9).
> 3. Run the Monday order (§2) and keep the board current.
>
> Rules that bit us this weekend: never merge to development while a deploy chain is armed; a deploy wake drops any heal
> list that landed at the sync before it (re-ask after a deploy); score a command on the unit's `[bmcamNNN] OK id=` line or
> the backend ack, never on the Spotter's receipt line; send commands to SPOT-31593C so they reach its mailbox :08–:15
> (its :07 health check is a dead zone; see run-book). Nick: Pacific AM/PM, cards + visuals, make the case in product terms,
> fewer pings, blockers one at a time (NEEDED + one ask).

## 1. Sprint short list (Nick asked Sun 10/11 5:30 PM)

### Camera (bm_cam_legacy, development)

| # | item | state | pool-blocking? |
|---|---|---|---|
| C1 | bm #146 heals-after-image (registry v12) | ready, held | yes — merge first; keeps the stream prefix intact for progressive |
| C2 | bm #147 progressive JPEG XL (`cjxl -p`, registry v13) | ready, rebase after C1; TE bench check of peak memory on nereus002 (B3a peaks 98–127 MiB; -p adds ~22 MiB; guard 250) | yes — this is what lets a scientist see a picture at ~50 % received |
| C3 | Focus parity: `camera.focus.mode manual`, `lens_position 1.82` on both units by remote command (decided d27) | no code | yes — pool replicates the reef unit |
| C4 | Format rollback drill: `still.format pjpg` by remote command on one unit, confirm a JPEG arrives, switch back | no code | yes — proves the field rollback + before/after demo |
| C5 | d30 (a) sync-aware halt: keep listening until bus-off / the Spotter's known sync minute | new code | NO — post-pool unless it is a one-line change to the halt budget; the timed-send workaround covers the pool |
| C6 | HDR off A/B (`hrpm 0` on SPOT-31593C) — card c_hdr_ab | needs Matt's answers (cfg save / reboot / power cycle) + Nick's console OK | NO — do not run during pool prep; it changes one unit's Spotter |
| C7 | Phase B card-WB coding gains, WB gain range > 8, lux gate, EV red bias | spec'd, not built | NO — second pool session; air tests = engineering proof only |

### Website / backend (nereus-vision-dev, staging)

| # | item | state | pool-blocking? |
|---|---|---|---|
| W1 | nvd #138 progressive preview ("preview from n/N chunks") | ready, held; needs the frontend QC gate (badge + chip) | yes — pairs with C2 |
| W2 | nvd #134 JPEG XL metadata sidecar on the detail page (Nick's bug 10/8) | open | yes — scientists read exposure/time there |
| W3 | d28 heal backfill ordering: cheapest-to-complete first (+ cap asks per hopeless image) | small change, evidence in TE2's rsd audit | yes if ≤ half a day; it decides how fast pool images complete |
| W4 | d30 (c) backend re-send of unacked command ids / sync-aware issue timing for rsds | small change | should — the API already hints the same-id re-send |
| W5 | d29 processing eligibility for previewed partials | decision only | with W1 |
| W6 | Viewer download policy: let viewers fetch PNG/DNG? (Nick's phone question 10/10) | decision + tiny change | optional |
| W7 | nvd #135 shareable time-range URLs, nvd #131 map page | open | NO — after the pool gate (decided earlier) |
| W8 | Cloud colour per-device toggle for pool images (measurement render) | port next week (g6) | NO — score pool images offline with the rig tool (#96) |

## 2. Monday order (one build day)

1. 7:35 AM read-out posted; weekend crons stopped (outgoing EM). Both units left as they are (RC2 + locked profile).
2. Merge bm #146 → TE2 deploys to ONE unit (bmcam004) in its bus window; A/B heals-after-image vs bmcam003 for 3 wakes.
3. Rebase bm #147 (v13, goldens) → nereus002 memory check → merge → deploy to the same unit; backend W1 QC + merge in
   parallel; confirm a partial image shows a preview on staging.
4. C3 focus command to both units (one command, two keys); C4 rollback drill on bmcam003 (pjpg one wake, back to nrjxl).
5. W2 / W3 / W4 as the backend chip's queue, in that order; QC gate on anything that touches dashboard/*.html.
6. Evening: both units on the same build + config again (pass bar = worst performer). Pool checklist to Nick: units,
   cards, bus schedule, what the dashboard will show, what we measure (completion, colour ΔE via rig #96, command confirms).

## 3. Open decisions for Nick (board)

- d28 heal backfill ordering (EM: yes, cheapest-to-complete first).
- d29 previewed partials processing-eligible (EM: yes, same as JPEG today).
- d30 sync-phase dependency fix (EM: (a) unit sync-aware halt + (c) backend re-send; (b) mote buffer later).
- d26 wave data needed per customer? (decides whether HDR-off becomes a default after C6).
- Sofar ticket: SPOT-31593C RTC glitch Sat 11:03 PM ("2072-00-07") re-anchored its report cadence; plus the `hrpm` questions.

## 4. Sessions to address by exact name

- "Take over as Test Engineer (bench owner)" — TE2, sole bench owner; light mode over the weekend; owns deploys, scoring, collector.
- "Progressive JPEG XL preview" — owns bm #147 + nvd #138; idle until the merge order starts.
- Backend chip: start fresh Monday (d21) with W2/W3/W4; the previous backend session is at its rotation point.
- Frontend QC: "Run frontend QC for Nereus Vision web app" — gate for W1/W2.
- Rig: "Raw compression test spec" (nereus002 + colour scoring tool #96) — pool colour scoring.
- NOAA session: remote_commands on SPOT-33361C still ON pending Nick's disarm (field unit — EM never touches it).

## 5. Don'ts carried over

No merges to main. No field units. No Render env/plan. No cloud commands or backend toggles outside an approved card.
Permission prompts are never relayed between sessions. One owner for the bench.
