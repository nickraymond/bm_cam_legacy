# Weekend RC2 soak run-book (Sat 10/10 – Sun 10/11, Nick offline)

Rev 2, Fri 10/9 6:20 AM PDT (EM draft Thu 7:30 PM + TE2's §3). Nick's go/no-go at Fri 5 PM after the rehearsal.
Times are Pacific with AM/PM. Board: https://claude.ai/artifact/R4bv59riyrbtbkKcAmyXZM

## 1. What runs

| unit | Spotter | build | config | backend |
|---|---|---|---|---|
| bmcam003 | SPOT-33507C | RC2 = development 9ec4cb7 (runtime = 786b100) | reef config (1.0 s, cap 195, hourly, no window) + JPEG XL B3a 1600×900 + exposure profile LOCKED (Nick, Fri 1:30 PM): camera.exposure.profile low_gain, max_shutter_us 60000, max_gain 1.0 → analogue gain 1.12, auto shutter ≤ 60 ms; applied as a command overlay (bmcam003 cmd 1000164 since Thu 5 PM; bmcam004 by command after the 2 PM deploy, in effect from the 5 PM wake) | self_heal ON, remote_commands ON, cap 24, reach-back ON (heal_since 10/8 20:00Z) |
| bmcam004 | SPOT-31593C | same RC2 (deployed Fri ~2 PM) | same | self_heal ON, remote_commands ON, cap 24, reach-back ON (heal_since 10/8 16:00Z) |

Both units identical; **pass bar is judged on the worst performer** (decided Thu 11:05 AM).
v3 colour card in both scenes, in open shade (Nick, Thu).

## 2. Weekend bar (decided Thu 9:40 AM, Nick)

- Commands: 100 % of sent commands confirmed and in effect (one key, contrast toggle, never superseded).
- Images: every image complete within **48 h** (the 24 asks/day cap binds for JPEG XL; ~37 asks/day needed).
- Backlog shrinking by Tue (heal queue not growing).
- 0 SSH into either unit; 0 Spotter resets from us.
- Rig health: no CRIT; WARNs at the :10:30 bus-off edge are benign (probe fix pending).

Read-out Mon 10/12 AM: n wakes per unit, completion time distribution, first-send loss, command table, heal asks used/day, energy per wake if the SD logs are pulled, colour error per received image (rig tool #96).

## 3. Test Engineer section (TE2, Fri 10/9 ~7 AM, merged by the EM)

### Stop rules (applied unattended, per unit; the other unit keeps running)

- **SSH needed to recover a unit → stop that unit only.** Never touch field units (SPOT-33361C, bmcam001/002).
- **2 commands lost → stop sending commands to that unit; keep imaging.**
  - **(new) Definition of "lost":** no ack, `<CF>` or matching cfg hash at the backend within **3 Spotter syncs of the send**.
  - Overnight we saw:
    - a command miss its first sync because the Sofar mailbox was empty (1000164, 22:10Z);
    - acks rejected at a queue stall and recovered by the unit's `d:1` re-send one wake later (REEF cmd 2).
  - Neither is a loss.
  - **(Fri 5:55 PM)** Score a command on the unit's own console line `[bmcamNNN] OK id=… cfg=…` or the backend ack, NOT on the Spotter's "Remote message received" line: 1000169 was applied (ack 5:04:50 PM) with no Spotter receipt line at all (same class as resets #2/#3).
- **(new) Unit misbehaviour → stop commands and flag; keep imaging unless SSH is needed:**
  - a hard cut (node current still above the halted ~0.018 A at bus-off) twice in a row;
  - `skipped_no_budget` or `[PHASE][WARN] skipping` in 2 consecutive wakes;
  - `[CFG][ERR]` / an LKG fallback at boot.
- **(new) NOT a stop reason** (measured, not failures): Spotter-side first-send loss. This covers health-check syncs, HDR messages, queue_full rejects, and lost START/END messages. Overnight it ranged 0.5–39 % per wake.
- Rig CRIT (charger fault, nereus000 battery) → push-notify Nick; no remote action.
- Spotter reset / bus hard cut → log it as an event; the wake restarts on its own; no action.
  - **(new)** After any Spotter reboot, the boot-anchored health check re-phases (gotcha 8).
  - **(new)** A cloud reset lands ~80 s after bus-on, so it hard-cuts a running Pi (gotcha 13). Don't send one unattended.

### Command schedule

- EM sends through the backend product path, one key: contrast 1.0 ↔ 1.1, only when the previous command is answered (plan dry-run first; never supersede).
- **(new) Timing note:** a send at :33 reaches the console at the next :10 sync (37 min later), is applied after the transmit at the following boot (~:04), and is in effect at the wake after that. So in effect ≈ 2.5 h after the send. With an answered-only gate, 2:33 AM / 10:33 AM / 6:33 PM on both units fits: ≥ 8 h between sends.
- **(new)** SPOT-31593C (bmcam004) receives its mailbox at ~:16 (console 2026-10-09: rsd at 01:16:58Z, 02:16:11Z), not :10. A :33 send still lands at the next sync.

### Left alone over the weekend

- Base YAML, overlays (other than the EM's contrast commands), crontab, sent/, /dev/shm, heal_since, caps (from the draft).
- **(new)** Bridge config on both Spotters; Spotter cfg; no console `cmd.txt` writes.
- **(new)** Runtime + packages: no deploys, no development merges aimed at the units, `libjxl-tools` stays.
- **(new)** nereus000 services (spotter-monitor, rig health, dashboard). The rig-health bus_v WARN at :10:30 (bus-off edge) is benign until the probe fix lands.

### Mac / sessions

- `caffeinate -i` held through Sun night, **lid open + on AC**: a closed lid sleeps anyway (gotcha 14).
- EM + TE2 sessions open.
- **(new)** TE2 per-wake scoring is chained in-session (each job re-arms the next), so it stops if the session dies. Imaging and heals don't depend on it. For a weekend that must leave evidence even if the session dies: a detached `nohup` collector (as the R3 driver) that saves the console excerpt + cycle log per wake. I can install that Fri afternoon if wanted.

### Open item for Nick / EM (not a TE call)

- **SPOT-33507C's health check now fires at ~:02:45 every hour, mid-burst.** Overnight it caused 31–39 % loss on 4 of 17 wakes.
- JXL-RC backlog at 6 AM: 179 chunks over 6 open images.
- A Spotter reset before the weekend would re-phase it, but only to a random minute. Decide with the overnight numbers.

EM decisions on TE2's items: detached collector = YES, install Fri afternoon; lid open + AC confirmed; the health-check re-phase question is Nick's Friday card (see §4).

## 4. Exposure decision (DECIDED Fri 1:30 PM)

Both units: analogue gain locked at the floor (1.12), auto-exposure shutter capped at 60 ms, otherwise free (the low_gain profile). Night frames come out dark by design below ~5 lux; judged on completion/decode, not visibility. Evidence: by-band and locked-gain cut sheets (runs/color_pilot_sunrise2_20261009), Channel Islands lux/depth gut check (runs/ci_lux_depth_20261009). Not in the weekend: > 60 ms paths, EV bias, lux gate (next week).

## 5. Monitoring (EM, automatic)

- Rig health poll every 15 min (nereus000 alerts.json); session 1:1s every 2 h; Render liveness + latest-ingest timestamps folded into the poll.
- Backend checks per wake: latest media per device, percent received, render_state; decode confirmed when complete.
- Messages to Nick over the weekend: only a CRIT, a stop-rule trigger, or a dead host. No status.

## 6. Rollback

- Camera: `hil_deploy_window.sh` rollback tgz restores the pre-deploy snapshot (note: it also restores sent/ to that snapshot).
- Backend: `PATCH heal_since null` per Spotter turns reach-back off; `self_heal false` stops heals; both reversible.
- Nothing merges to development/staging during the soak except docs.

## 7. Monday queue (not weekend work)

Deploy runbook: a deploy wake drops any rsd that landed at the sync before it (bmcam004 2 PM Fri lost 100231/100232; the backend re-ask rule recovers them, ~1 extra wake) → add 're-ask after a deploy' to hil_deploy_window.sh notes; SYNC-PHASE DEPENDENCY (found Sun 4:30 AM, TE2): SPOT-31593C's mailbox sync moved from ~:16 to ~:08, i.e. 10–50 s AFTER bmcam004's halt (~:07:20–:08:20) while the bus is still on → command 1000079 and rsd 100268 landed with no listener and were not applied at the 4 AM boot; every later 004 command/rsd will miss until the phase moves again. Mechanism (TE2 console, Sun 5 AM): no Spotter reboot; SPOT-31593C's health check fires :07:2x (boot-anchored, −1 s/h) = bus on, Pi halted → anything it delivers is dropped; the :16 report sync happens after bus-off, the Spotter holds those items and the next boot applies them. From 11 PM Sat items started reaching the mailbox before :07 (why is not visible from the bench) so the health check became the carrier. EM call (revised 5 AM): no re-phase, no new ids; count 1000079 as loss 1; from now send 004's items so they reach the mailbox :08–:15 (one-shot 10:11 AM: same-id re-send of 1000079 + 003's slot), and let the :33 cron skip as in_flight. bmcam003 unaffected (health check :02:45 with the Pi up, report :10). ROOT CAUSE (TE2, Sun 5:30 AM): SPOT-31593C's hourly report re-anchored at 11:03 PM Sat on a console line with a corrupted RTC stamp ('2072-00-07T06:03:07' = Spotter clock glitch) and now runs every ~62 min (11:03, 12:05, 1:07, 2:09, 3:11, 4:13 …); the 1–3 AM reports fell in the :07–:10 dead zone, hence the drops; from ~5:15 AM the report sits after bus-off again and is a good carrier. → Sofar ticket item: report cadence drift after an RTC glitch (adds to the health-check/HDR collision notes). PRODUCT ITEM for Monday (Nick, d30): delivery must not depend on the Spotter's sync minute falling inside the wake — options: listen until the known sync minute has passed (sync-aware halt, R2-DELAY family), mote buffer that survives bus-off, or backend re-send of unacked ids at the next wake. HEAL STARVATION (found Sat 8 AM, both EM backend view + TE2 console): bmcam003's oldest partials (Fri 2/4/5 PM; JXL-RC 58158/58183/58188/58191) got no heal for 24–36 h while newer partials took the asks; TE2's rsd audit (runs/jxl_rc_20261008/analysis/rsd_lane_audit_20261010.md, 14 asks): backfill lane DOES fire (4/14, ~1 in 3.5) and asks are near full (mean 37/40) — the cause is (a) soonest-to-expire keeps picking the pre-RC2 leftover 58137 (45/184 pjpg, needs ~4 asks) ahead of near-complete images, and (b) heal chunks lost in queue stalls (58158 asked 35, gained 19) → ROOT CAUSE (EM, Mon 5 AM, nvd heal_commands.py:325 pack_command): greedy whole-image-only packing, newest first — an image is added only if ALL its missing chunks fit the remaining 40-chunk budget, so a ~40-missing image is skipped (left_out 'chunks > 40') whenever a smaller fresh partial precedes it and the ask leaves under-filled (bmcam003 Mon 5 AM preview: 12/40 sent, 58331 with 66 missing skipped); Monday backend item (Nick decision d28): slice the next image to fill the budget, backfill order = cheapest-to-complete first, cap asks per hopeless image, re-ask after stall loss; Progressive preview PR 2 = nvd #138 (ready Sat 6:30 PM, HELD): libjxl flush preview for partial B3a stills 'preview from n/N chunks', no migration/wire change, first picture at 48 % of chunks; needs the frontend QC gate (gallery badge + chip) before merge; Nick question d29: previewed nrjxl partials become processing-eligible like JPEG partials today; Format rollback drill (Nick, Fri 7:30 PM): on one RC2 unit send `still.format pjpg` by remote command (the catalog's 'back to pjpg' preset), confirm a progressive JPEG arrives and decodes, then switch back to nrjxl — proves the field rollback and the before/after demo; focus parity command (manual 1.82) on both units; Heals-after-image (bm #146) merge + A/B; seeded 13-day heal gate; card-WB Phase B camera code (incl. gain range > 8); nrjxl metadata on the detail page (nvd #134); shareable time-range URLs (nvd #135); V1 focus ladder + CMA check; map page #131 after the pool gate.
