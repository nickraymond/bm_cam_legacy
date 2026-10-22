# Test card: RC soak of the S3 crop build + NOAA profile on bmcam003 / bmcam004 (Fri 10/23 PM → Mon 10/26 AM)

Drafted by TE5, Thu 10/22 ~11:05 AM PDT. This is the new soak card rev that SPEC_crop_view_s3 must do 15 asks for.
- **Spec:** `~/NereusData/ops/pe_specs/SPEC_crop_view_s3.md` rev 7.3 (sha256 f9c60448701e), must dos 13, 15, 16 and 17;
  F1–F10. Bars equal the spec's; where the spec and the parent soak card differ, the stricter wins (TEAM_RULES §13).
- **Parent card:** `runs/soak_noaa_20261017/TEST_CARD.md @ 92921f76`. Its bars, stop rules and monitoring carry over
  unless changed here.
- **Decision:** Nick, Lead 003 chat, Thu 10/22 ~10:58 AM, as recorded on c_crop_view `decided`: "Let's say yes, run
  another soak with the larger crop feature …".
- **Approval:** this card runs only on Nick's own line in the TE5 chat citing this card's sha.

## Question
Does the S3 build (larger 16:9 crops shrunk to 1600×900, bm #186) with the NOAA profile hold every soak bar and every
per-wake crop bar on the bench? This covers each catalog preset set twice over the console, and one remote revert
through the backend. The Mon 10/26 AM call then picks between two builds: ship S3, or ship S2 (2cc1afdb) at 1600×900
(F9/F10).

## Units, build, profile
- **bmcam003 / SPOT-31593C is scored. bmcam004 / SPOT-33507C is report-only** (spec pass/fail line).
- **Build S3** = the bm `development` sha that carries the bm #186 merge (PR head a24dcbfd, QE 010 PASS). It is recorded
  in `run_manifest.json`. Must do 6 check before deploy:
  - `git diff --stat 2cc1afdb <S3> -- BM_Devel_Pi/` lists only #186's runtime files;
  - any other change in BM_Devel_Pi/ → stop (F8).
- **Profile = the NOAA soak profile, as bmcam003 runs it today:**
  - window 08:00–18:00 in the units' base zone, America/Los_Angeles (spec must do 15; no timezone change);
  - nrjxl B3a, `still.crop` [1504, 846, 1600, 900], focus manual 0.8, exposure profile low_gain (max_shutter 60000 µs,
    max_gain 1.0), contrast 1.0, progressive on, heal_order after, `heal_offwin_max` 40.
  - bmcam003 keeps cfg **d95aa41d**: a4e668c5 + window 08:00/18:00 (set 66381, ack `cfg=d95aa41d`,
    2026-10-17_soak_noaa/window_tue/log.txt). The key list above is bmcam004's backed-up a4e668c5 overlay
    (cmd-coverage backup_pre/bm_command_state_v2.json).
  - bmcam004 goes from a4e668c5 (07–12) to window 08:00–18:00; expected hash d95aa41d, verified by `get` (not assumed).
- **Production schedule:** hourly, 3600000 / 600000 (`hil_restore_schedule.sh`), backend rollout self_heal +
  remote_commands ON for both Spotters.

## Preconditions (each checked and logged before step 1; any miss = do not start, tell the Lead)
1. **nvd #153 deployed on staging (must do 13).** The Lead posts the sha on nick_rc_soak_lines; the TE reads the
   deployed sha the way the Lead's post names. Kill line: not deployed by Fri 10/23 noon (spec).
2. **bm #186 merged to development** (the Lead's merge) and S3 passes the diff check above.
3. **nereus000 on sustained power.** Today (Thu 10/22) it runs from an external battery: no wall power while the ebox is
   outside, and the solar panel isn't charging on a cloudy day. Required: `lifepo4wered-cli get VIN` ≥ 4800 mV and no
   `RIG-HEALTH CRIT ... ON BATTERY` in the last 2 h. Without nereus000 there are no consoles, no preset steps and no
   monitoring.
4. **Spotters:** battery ≥ 3.85 V; no new `THERMAL_FAULT` on SPOT-33507C (the parent card's power stop rule); GPS +
   Spotter UTC OK on a fresh boot of each unit (keyed image).
5. **Backups per unit:** config YAML, `bm_command_state_v2.json` and crontab, with sha256. The S2 rollback tar is on the
   unit.

## Steps (Fri 10/23 PM; TE5; console via nereus000)
1. **Hold** both buses: `hil_bus_always_on.sh SPOT-31593C` and the same for `SPOT-33507C`.
2. **Deploy S3** on each unit: `deploy_s.sh deploy <host> <S3_7>` (rc_field_update `--ref development --leave-disarmed`),
   then `deploy_s.sh rearm <host>` and `deploy_s.sh halt <host>`. Verify the unit's sha = S3 and the crontab sha256 =
   backup.
3. **First forced boot per unit** (`step.sh`-style: `bridge cfg commit` → production cycle → listen window):
   - bmcam004 gets `set` `{"schedule.window.start":"08:00","schedule.window.end":"18:00"}`, then `get` → expect d95aa41d;
   - both: keyed 1600×900 image, 0 rfb, `<WS ... sha=<S3_12>>`.
   - After this boot the TE checks `/media/{id}/hq` = a 1600×900 render for bmcam004's 3 G1 (b) images (must do 13, spec).
4. **Presets over the USB console, twice, per unit (must do 15).** Catalog order @ a24dcbfd:
   full frame [0,0,4608,2592] → 3072×1728 [768,432,3072,1728] → 2304×1296 [1152,648,2304,1296] →
   1000×562 [1804,1014,1000,562] → 800×450 [1904,1070,800,450] → 640×360 [1984,1116,640,360] →
   1600×900 [1504,846,1600,900]. Then the same order again.
   - Each preset: `set` `still.crop` in a listen window → ok-ack. The **next forced boot** is a bus power-cycle, so it is
     also the cold-reboot check. That boot must send the crop: shrunk to 1600×900 if wider than 1600, native size at or
     below; `get` shows the crop kept. In the same window, set the next preset.
   - After pass 2: one even step off 16:9, `[768, 432, 3072, 1730]` → refused with an error code (`e=…`). Then
     `reset` `still.crop` → `get` = [1504, 846, 1600, 900].
   - That is 15 boots per unit, ~8.5 min each (≈ 2 h 10 min per unit). Units run one after the other unless the TE
     checks that both consoles answer in parallel.
   - **Who types:** by default the TE, through `hil_cmd.sh` over nereus000's USB console (the cmd-coverage path), with
     every command and ack in commands.log. Nick may type them at the console instead; he says which in his OK line.
5. **Production:** `hil_restore_schedule.sh SPOT-31593C` and `SPOT-33507C`, then rollout ON (GET before/after logged).
   The scored soak starts at the first in-window production wake (08:00–17:00 wakes) after this step.
6. **Remote revert proof, once, bmcam003 (must do 17), Sat 10/24 in the window:**
   - The TE sends through the backend: `POST /devices/BMCAM_003/remote-config/changes`
     `{"set":{"still.crop":[0,0,4608,2592]},"lane":"sofar"}`, then `POST /admin/devices/BMCAM_003/commands/<id>/send`
     (the d38 path, admin token on nereus000).
   - Next in-window wake: a shrunk 1600×900 full-frame image. Then the same path with `[1504,846,1600,900]` → next
     image 1600×900 native.
   - Also logged: the plan check refuses `[1104,620,2400,1350]` (16:9, not a preset; must do 11) at `/changes`.
     No send happens.
7. **Monitoring (read-only):** as the parent card (`soak_check.sh`, `hil_media_table.py --d1-hours 24` every 6 h, rig
   health), plus per image wake: rfb, cjxl `peak_rss_kb`, MemAvailable min (sampler from cron @reboot as in G1 b), search
   s, shrink s, wake → halt, msgs, listen-tail s; and the backend render size per image.
8. **Read-out Mon 10/26 ~8:30 AM PDT** (instant fixed now: 2026-10-26T15:30:00Z), before Nick's call. Images younger than
   24 h are censored and listed.

## n, metric, bars (spec must do 16; worst wake decides; bmcam003 scored)
- **n:** every image wake on bmcam003, from step 3 to the read-out:
  - 15 console preset wakes + 1 first boot + 2 revert wakes;
  - ~10 in-window production wakes per day (Sat, Sun, Mon 08:00), ≈ 21.
  - Plus every off-window wake for the wake bars.
- **Soak bars (parent card):** images complete ≤ 24 h; commands 100 % answered ≤ 3 h; 0 FALLBACK/ERR; every scheduled
  wake.
- **Per image wake:**
  - 0 rfb;
  - cjxl `peak_rss_kb` ≤ 170 MiB;
  - MemAvailable min ≥ 50 MiB;
  - search ≤ 36 s;
  - 0 hard cuts (wake → halt ≤ 481 s);
  - ≤ 195 msgs.
  - Reported next to S2's: shrink s, listen-tail s, msgs.
- **Backend:** every image rendered at the size must do 10 predicts.
- **Heal:** chunks per wake and backlog within the d28 bars.
- **Presets:** every set ok-acked; every next boot sends the predicted size; the crop is kept across the power-cycle;
  the off-16:9 step refused; the reset gives 1600×900.
- **Revert proof:** both images arrive as predicted; the non-preset crop is refused at the plan check.
- **Pass** = every bar on bmcam003's worst wake and every must do 13/15/17 item. **Fail** = any miss → F10 (ship S2 at
  1600×900 at the Mon call). bmcam004 rows are reported, not scored.

## Stop rules
- **Parent card:** Spotter battery < 3.80 V; SPOT-33507C `THERMAL_FAULT` with battery net-negative over 3 h or < 3.85 V;
  2 scheduled wakes missed in a row → stop that unit and hold it. Tell the Lead.
- **A preset wake** with a hard cut, a kernel OOM or rfb=shrink/mem: stop the preset phase on that unit, `reset`
  `still.crop` → 1600×900, and report. On bmcam003 that is a fail (F10).
- **nereus000 ON BATTERY or unreachable:** pause the console and backend steps. Production wakes carry on; logs are
  pulled later. Resume per Nick's standing line.
- **Kill lines (spec):** nvd #153 not deployed by Fri noon, or bm #186 not merged by Fri 6 PM → no S3 deploy; S2 ships
  (F9).

## Restore after the Mon call
- **Nick picks S3:** units stay on S3 + the NOAA profile; window back per Nick.
- **Nick picks S2 (or the soak fails):** redeploy 2cc1afdb on a held bus (`deploy_s.sh deploy <host> 2cc1afd`, rearm,
  halt), then restore each unit's backed-up config and crontab (hash + sha256 checked), `hil_restore_schedule.sh`, and
  rollout ON.

## Cost
- **Bench:** bmcam003 + bmcam004 + both Spotters + nereus000, Fri ~noon → Mon ~9 AM. No other bench work in that time.
- **TE:** ~5 h Fri (deploy + 30 preset boots), ~1 h Sat for the revert proof, checks every 6 h, ~1 h read-out Mon.
- **Nick:** his OK line (+ ~30 min at the console only if he types the presets).
- **Cellular:** ~10 in-window images/day/unit, the preset and revert images, heals, commands.

## Outputs
- Run folder `~/NereusData/bm_cam_legacy/2026-10-23_rc_soak_s3/` (never git): run_manifest.json (S3 sha, nvd sha,
  hashes, approvals), backups with sha256, commands.log, steps/, per-wake logs, wakes.csv, a readout table.
- In git: this card, RESULTS.md.
