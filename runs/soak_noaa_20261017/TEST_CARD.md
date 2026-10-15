# Test card: NOAA release soak, bmcam003 + bmcam004, Sat 10/17 → Tue 10/20 (pre-registered BEFORE the test runs)

- Template: nereus-software-engineering-team `templates/test_card.md`.
- Owner: TE2. Drafted Thu 10/15 ~10 AM PDT.
- Runs ONLY on Nick's own approval line in the TE2 chat, citing this file's sha. It sits outside the Friday envelope.

## Question (Nick's go/no-go for the bmcam002 remote update, Thu 10/22)

Does the exact NOAA release build (bm fdeb01a + the bmcam002 target config) deliver 100 % of image data within 24 h of capture, with remote commands answered, on the bench Spotters with HDR off?

## Conditions

**Units:** bmcam003 / SPOT-31593C and bmcam004 / SPOT-33507C (pairing since 10/13), after the Fri 10/16 pickup and the Friday-envelope restore.
- Code fdeb01a on both, with keys still.raw.progressive=true and uplink.media_key.heal_order=after.
- Backend: nvd staging. After the d28 merge if the PjM confirms it before the start; otherwise the current one. The backend sha is recorded at the start either way.

**Config: two branches.** Which one runs depends on Nick's Fri AM answer to PgM decision d35.

| | Branch A: d35 accepted | Branch B: no decision / no |
|---|---|---|
| Config | 0cb7ad9e + `schedule.timezone=America/New_York` + `schedule.window.enabled=true`, `start=10:00`, `end=15:00` | 0cb7ad9e unchanged |
| Keys d35 lists that need NO change on the soak units | focus 0.8 (manual 0.8 already); pacing 1.0 s (the units already run `pacing=1.0s/msg`; only bmcam002 needs the base-config deploy for it) | — |
| Spotter bus | hourly 3600000 / 600000 (unchanged) | same |
| Image wakes per unit | 10:00–14:00 ET = 7–11 AM PDT: 5 / day | 24 / day |
| Other wakes | 19 / day out of window: `<WS a=skip_win>` + command listen tail, NO capture, NO heals (rc_progressive_jpeg.py W3 path) | none |
| Delta to NOAA on Tuesday's card | none (the config IS d35) | timezone + window: the soak ran 24 h / LA time |

**Sky view (GPS + cellular):**
- Both Spotters outdoors with an unobstructed sky (media keys need Spotter UTC from GPS; cellular for every byte), on nereus000's USB hub for charging; nereus000 on the 3 A supply.
- Check before the clock starts (both must pass):
  1. Spotter console `post` / `sensors` on both: GPS fix present and a cellular link (spotter-health-check skill).
  2. The first in-window wake on each unit logs `[KEY] media key … from Spotter UTC … (spotter)` and a keyed nrjxl.

**Held fixed that differs from NOAA's site (SPOT-33361C):**
- bench Spotters, Notecard WBGLW (SPOT-31593C) / WBNA-500 (SPOT-33507C), against NOAA's unit;
- dry bench scenes, not reef (bytes per image differ);
- USB charging, not solar;
- home cellular coverage;
- HDR off (`hrpm 0`, d26) on both bench Spotters, while NOAA's HDR state is as configured there;
- report minutes as re-rolled by the HDR restart.

**HDR precondition:** if d26 (`hrpm 0`) has NOT landed by the start, the card's "HDR off" condition is unmet. The soak then runs and reports as "HDR on", or waits; that is Nick's call.

## n (why enough)

- **Branch A:** start Sat ~9 AM PDT (12:00 ET).
  - Image wakes per unit: Sat 12, 13, 14 ET (3) + Sun 5 + Mon 5 = **13 per unit, 26 images**.
  - Mon's images reach 24 h by Tue 11 AM PDT, so the read-out is Tue ~11:30 AM PDT.
  - 0 misses in 26 bounds the true miss rate at ≤ 11.5 % (95 %, rule of three). That's weak; it's what a 5-image-per-day window allows over one weekend.
- **Branch B:** 48 h → **48 per unit, 96 images**. 0 misses bounds it at ≤ 3.1 % (95 %).
- **Commands, both branches:** one read-only backend command per unit every 3 h, 16 per unit, 32 total. 0 misses bounds it at ≤ 9 %.
- Two units cover both Notecard models.

## Metric + pass bars (set now)

1. **Images complete within 24 h, bar 100 %:**
   - Expected images = in-window scheduled wakes (A) or all scheduled wakes (B), from each Spotter console's bus-on log.
   - A miss = any of:
     - an expected wake with no backend media row;
     - a row not `complete` with `last_chunk_at ≤ captured_at + 24 h` (`hil_media_table.py --d1-hours 24` → `d1=FAIL`);
     - an un-keyed / legacy-fallback image.
2. **Commands answered, bar 100 %:** every backend-sent read-only command (`ping`, `get`) has its `OK id=` reply in the backend command log within 3 h of the send.
3. **Errors, bar 0:** `[RAW] FALLBACK`, `[CFG][ERR]`, overrun / crash lines. Cycle logs are read at wakes over home Wi-Fi (`hourly_verify.sh`) or pulled at the end.
4. **Also gating:**
   - every scheduled wake shows bus-on + `<WS>` on the console (A: in- and out-of-window);
   - halt ≤ 481 s uptime;
   - cjxl peak ≤ 150 MiB;
   - msgs ≤ 195;
   - no EXT4 errors in the end-of-soak dmesg.
5. **Report-only:** search3-overshoot ships (d=2.595), `queue full` runs per wake, heal chunks per wake, report minutes.

**Pre-registered risk (branch A):** heals run only in in-window wakes.
- An image from 13:00 / 14:00 ET that needs healing gets its heal at the next day's 10:00 / 11:00 ET wake, i.e. 20–22 h later. A second heal round would miss 24 h.
- A FAIL from this mechanism is a real NOAA finding, not a test artefact.

## Stop rules

- **Power:**
  - Spotter battery < 3.80 V → stop the soak for that unit (it stays in production) and tell the PjM.
  - nereus000 VIN < 4.9 V or VBAT < 3.10 V affects monitoring only, not the soak (images and commands go over cellular): fix power, log the gap.
- **Thermal:** SPOT-33507C charger `THERMAL_FAULT` with its battery net-negative over 3 h, or < 3.85 V → stop that unit's soak.
- **Console loss:** the soak continues; log the gap; > 6 h → tell the PjM.
- **Broken:** a unit misses 2 scheduled wakes in a row → stop that unit, bench hold + investigate.
- **Answered early:** none. A 100 % bar needs the full n. A miss is root-caused and the soak continues.
- **Never:** SPOT-33361C / bmcam001 / bmcam002, or any config beyond this card.

## Exact verbs

Console via nereus000 `pi@192.168.1.45` (or Tailscale `100.82.38.22`). Console ids 66101+.

**Start (Sat ~8:50 AM PDT):**
1. Pre: the sky-view checks above, plus `grep -E '^export HIL_RIG_[AB]=' hil/hil.env`.
2. **Branch A only:** in each unit's post-transmit listen window (the switch_on.sh wait pattern), send:
   - `hil/tools/hil_cmd.sh SOAK.bmcam003 '{"id":66101,"c":"set","kv":{"schedule.timezone":"America/New_York","schedule.window.enabled":true,"schedule.window.start":"10:00","schedule.window.end":"15:00"}}' 12 SPOT-31593C`
   - the same for bmcam004 with id 66102 on SPOT-33507C.
   - Expect `OK id=…` + a new cfg hash, recorded as the d35 hash.
3. **Branch B:** no config verb.

**During:**
- Commands, every 3 h per unit, alternating `{"c":"ping"}` and `{"c":"get","k":["camera.focus.lens_position"]}` (read-only verbs only):
  `python3 /home/pi/hil_r1/hil_backend_cmd.py --api https://nereus-vision-staging.onrender.com --device BMCAM_00N --json '<cmd>' --log /home/pi/hil_r1/soak_20261017_cmds.jsonl`
  driven by a nohup loop on nereus000 (no crontab change).
- Monitoring, read-only:
  - `hourly_verify.sh <host>` at wakes;
  - `hil_media_table.py --d1-hours 24` every 6 h;
  - rig health (Spotter batteries, chargers).

**End / restore (after the Tue read-out):**
- Branch A: in a listen window, `{"id":66111,"c":"reset","k":["schedule.timezone","schedule.window.enabled","schedule.window.start","schedule.window.end"]}` to bmcam003 and id 66112 to bmcam004 → back to 0cb7ad9e (verify the hash).
- Branch B: nothing.
- Stop the nohup command loop.

## Cost

- Both bench units + Spotters + nereus000, Sat ~9 AM → Tue ~noon (~75 h). No other bench work in that time.
- Cellular data:
  - A: ~5 images/day/unit + 19 `<WS>`/day + 8 commands/day;
  - B: ~24 images/day/unit + commands.
- TE2 ~3 h (start, checks, read-out).

## Is it worth it?

It's the only pre-deployment evidence for Nick's Tue 10/20 go/no-go on the bmcam002 remote update (Thu 10/22). It tests the exact release build and its NOAA config for the release goals: images complete ≤ 24 h, and remote commands answered.

---

## Rev 2 (Thu 10/15 ~10:30 AM PDT): PgM re-cut d35 → recommends **B** = hourly 24 h, ET, focus 0.8, pacing 1.0 s

**The soak is planned on B.** A runs only if Nick picks A in Friday morning's batch; the A section above stays as written.

**Does B's `schedule.timezone=America/New_York` change anything? No, for capture and cadence.**
- 0cb7ad9e already has an all-day window: `transmit_window 00:00–00:00`, `enforce_time_window: true` (the 10/14 effective render). So the schedule gate is always open in any timezone.
- The timezone feeds only:
  - the gate's local-time evaluation (no effect with an all-day window);
  - labels: `<WS … tz=…>` and `local_time`, `[RC] schedule: … tz=`, the run summary;
  - the config hash.
- Wakes come from the Spotter bridge (UTC hourly, 3600000 / 600000) and don't depend on the camera's timezone. Filenames and media keys are UTC.
- **Plan:** set it anyway, so the soak runs the exact NOAA key values: one reversible console `set`. The window stays all day, so nothing else moves. Focus 0.8 and pacing 1.0 s already match on the soak units.

**B verbs (replace "Branch B: no config verb" above):**
- Start, in each unit's post-transmit listen window:
  - `hil/tools/hil_cmd.sh SOAK.bmcam003 '{"id":66101,"c":"set","kv":{"schedule.timezone":"America/New_York"}}' 12 SPOT-31593C`
  - the same for bmcam004 with id 66102 on SPOT-33507C.
  - Expect `OK id=…` + a new cfg hash (record it). The next wake's `[RC] schedule: window=00:00-00:00 tz=America/New_York`.
- Restore after the read-out: `{"id":66111,"c":"reset","k":["schedule.timezone"]}` / id 66112 → hash back to 0cb7ad9e.

**B n and read-out:** 48 h from Sat ~9 AM PDT → 48 image wakes per unit, 96 images (0 misses ⇒ ≤ 3.1 % at 95 %). Read-out Tue 10/20 ~9:30 AM PDT, once Mon 9 AM images reach 24 h. The bars and stop rules above apply unchanged.

**Delta for Tuesday's card under B:** none in key values. The remaining differences to NOAA are the "held fixed" site list above.

---

## Rev 3 (Thu 10/15 ~12 PM PDT): SUPERSEDES the branches above. Nick decided d35 with off-window heals (bm #181); d38 (PgM)

**Build:** bm development @ **S2** = the #181 merge sha (merges Sat AM). It is deployed and gated by `GATE_CARD_offwin_heals.md` (rev 2, 8cfa74eb), then the soak runs THIS build unchanged.

**Config (d35):** 0cb7ad9e +
- `schedule.timezone=America/New_York`;
- `schedule.window.enabled=true`, `schedule.window.start=10:00`, `schedule.window.end=15:00`;
- `uplink.media_key.heal_offwin_max=40`, set explicitly at the end of the gate (66252 / 66253).

Focus 0.8 and pacing 1.0 s are unchanged (already in place). The Spotter bus stays hourly 3600000 / 600000.
- **In window** (10:00–14:00 ET = 7–11 AM PDT): capture + send + heals as before.
- **Out of window** (19 wakes / day): no capture, no START; `<WS a=skip_win>`, ≤ 40 heal chunks, and the command listen tail.

**Backend:** catalog v14 if it has merged by the start, otherwise the current one (d38: the soak doesn't wait). Without v14, the backend's predicted cfg hash won't match the units' reported hash, because v13 lacks the new key. That is LOGGED as a known mismatch, not a failure. The backend sha is recorded at the start.

**n (what it can and can't show):** the clock starts at the first in-window wake after the gate (~Sat 11 AM PDT = 14:00 ET).
- In-window images per unit: Sat 1 + Sun 5 + Mon 5 = **11 → 22 images in all**.
- Late-window images (13:00 + 14:00 ET captures, the ones d35 is about): Sat 1 + Sun 2 + Mon 2 = **5 per unit → 10**.
- Off-window wakes: ~50 per unit, so the heal mechanism runs ~100 times.
- **CAN show:** whether off-window heals finish late-window images within 24 h in the normal case. It catches any systematic failure: heals not sent off-window, the give-up horizon (G8: 3 wakes ≈ 3 h) dropping heals before the backend re-asks, re-ask timing, or loss exceeding 40 × wakes.
- **CAN'T show:** a rare-miss rate. 0/22 bounds ≤ 13.6 %, 0/10 late ≤ 30 % (95 %). Nor NOAA's site loss rates or cellular. A clean result means "no systematic failure seen in 22 / 10", not "≥ 99 %".

**Headline bar (set now):** **100 % of late-window images (13:00 + 14:00 ET captures) complete ≤ 24 h after capture.**
- A miss = no backend row for an expected late wake, `d1=FAIL` at 24 h, or an un-keyed image.
- Also gating, all 100 % / 0:
  - every in-window image complete ≤ 24 h;
  - every backend-sent command answered ≤ 3 h (read-only ping/get every 3 h per unit = 8 per unit per day);
  - every off-window wake shows `[HEAL] off-window wake: … heal_chunks ≤ 40`, with 0 captures / 0 START outside the window;
  - 0 FALLBACK / ERR;
  - every scheduled wake present;
  - halt ≤ 481 s;
  - cjxl ≤ 150 MiB;
  - msgs ≤ 195.
- Report-only: heal chunks per off-window wake, `wakes_left` give-ups, queue-full runs, energy per off-window wake (from the bridge `power |` samples, as in the gate's G7).

**Read-out:** Tue 10/20 ~11:30 AM PDT. Mon's 11 AM PDT (14:00 ET) images reach 24 h at Tue 11 AM.

**Conditional step (d38), once v14 merges (Sat AM, or Tue AM before the go/no-go):**
- One remote set on bmcam003 through the backend product path: `uplink.media_key.heal_offwin_max` 40 → 39, then 39 → 40, in the next listen tails.
- Pass: both answered (`OK id=`), AND the backend's predicted cfg hash = the unit's reported hash (`GET /devices/BMCAM_003/remote-config`: `reported.hash` == the plan's prediction) after the second set.
- **Needs Nick's own line in the TE2 chat** (a remote write).

**Dependency: nereus000.**
- The gate (bus holds, forced boots, console sets) and the backend command loop (`hil_backend_cmd.py` uses the admin token that lives on nereus000) both need nereus000 up.
- If it's still down Sat AM:
  - the deploy can go through `hil_deploy_window.sh` in hourly windows (ssh only);
  - the gate's console steps can't run (ask Nick: wait, or a Spotter console straight into the Mac);
  - the soak's commands would need the backend owner to send them.

**Exact verbs:**
- Start: the gate card's steps 1–8 (deploy S2 on a held bus, gate, then soak config + `hil_restore_schedule` hourly). Then the backend command loop: a nohup loop on nereus000, every 3 h per unit alternating `{"c":"ping"}` / `{"c":"get","k":["camera.focus.lens_position"]}`:
  `python3 /home/pi/hil_r1/hil_backend_cmd.py --api https://nereus-vision-staging.onrender.com --device BMCAM_00N --json '<cmd>' --log /home/pi/hil_r1/soak_20261017_cmds.jsonl`
- Monitoring (read-only):
  - per-wake `hourly_verify.sh` (home Wi-Fi);
  - `hil_media_table.py --d1-hours 24` every 6 h;
  - console counts; rig health.
- Restore after the read-out, per Nick's Tue decision:
  - keep (NOAA config), or
  - console `reset` of `schedule.timezone`, `schedule.window.*`, `uplink.media_key.heal_offwin_max` (ids 66111 / 66112), with code staying S2 (cap reset = default 40) or a rollback redeploy of fdeb01a on Nick's line.

## Rev 3.1 (Thu 10/15 ~12:15 PM PDT): one report-only metric, no bar change

- `<HL a=dropped r=expired>` count per unit per day (console + cycle logs). Expected **0** at cap 40 on hourly wakes: each rsd's 3-wake budget is reset by new asks, and the list drains every wake.
- Any non-zero count is listed with its key and whether the image still completed within 24 h.
