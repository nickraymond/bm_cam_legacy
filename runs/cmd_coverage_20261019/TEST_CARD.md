# Test card: every remote command on bmcam004 over the console (c_cmd_coverage), Mon 10/19 → Wed 10/21 noon

Pre-registered BEFORE the first command (templates/test_card.md). Owner: TE3. Written Mon 10/19 ~9:00 AM PDT.
Method, rows and verdict rules: **SPEC_cmd_coverage_bmcam004.md rev 4, sha256
9ae529792a38cd251556fad647f11f21097f1c71df6ad949ddc9977679eba412** (PE 004; QE 007 spec PASS-with-nits Sun 8:06 PM;
byte-identical copy in this commit at docs/specs/cmd_coverage_bmcam004.md). This card adds only the bench specifics.
Where they differ, the spec wins; a needed difference is a new card revision.
Runs only on Nick's own line in the TE3 chat citing this card's commit sha. Nick's Sun 10/18 ~8:20 PM line ("…then run
the command sweep Monday") is the go for the work, not the card OK.

## Question (Nick, NOAA doc 10/18 5:21 PM)
Does every remote command the camera accepts take the right action on a real camera, without bricking it, before
NOAA (10/27)?

## Conditions
- **Unit:** bmcam004 only, S2 2cc1afdb4831, soak config hash a4e668c5 (registry v14), on SPOT-33507C (bridge
  c3c564b91856226c). It leaves the soak (unscored there, Nick's B). bmcam003 / SPOT-31593C stays in the soak and is
  never touched. Never SPOT-33361C / bmcam001 / bmcam002.
- **Path:** USB console only (nereus000 → `hil/tools/hil_cmd.sh … SPOT-33507C` = `bm pub`), no cellular, no backend
  sends.
- **Boots:** forced production boots on a held bus, as in the 10/18 gate. `hil_bus_always_on.sh SPOT-33507C` (bus
  on, controller off), cron re-armed so every boot runs the production cycle, then one step = `bridge cfg commit`
  power-cycle → cycle → post-transmit listen window (commands sent here) → halt. About 8 min per boot.
- **Captures:** the soak window (07:00–12:00 LA) would block captures outside it, so the FIRST matrix row is
  `schedule.window.enabled false` (a Tier 1 row anyway): every forced boot then captures. The window rows run last;
  the Wed restore puts the soak overlay back.
- **Ids:** a file counter from **67001** (TE range 1–99999; backend ids are ≥ 100000), never reused except the
  deliberate repeated-id row.
- **Crop part (must dos 6/6b): DONE Sun 10/18 night** on Nick's TE3-chat line
  (~/NereusData/bm_cam_legacy/2026-10-18_crop_sweep_bmcam004/RESULTS.md): K0 1600×900 pickable; K1/K2/K3 rfb=mem 5/5,
  not pickable. PE 005 accepted 9:09 PM; Nick: "not shipping any of this with 10/27". The TG-7 desk K1 rerun was
  dropped by PE 004 (8:44 PM). Not repeated here.

## Monday order (spec must do 2), each step done before the next
1. Hand-over: TE2's written hand-over of bmcam004 (HANDOFF_TE2_20261018.md, Sun 5:50 PM) + this card.
2. Backups with sha256: camera_config.yaml, the v9 state file (bm_command_state_v2.json), crontab; the S2 rollback tar
   `/home/pi/backups/BM_Devel_Pi_before_rc_deploy_bmcam004_20261018T1536*Z.tgz` exists and lists. Read-only over ssh at a
   normal wake, before step 3.
3. Nick's OK on this card's commit sha, in the TE3 chat.
4. Backend traffic out (spec R6): PATCH /admin/gateways/SPOT-33507C/rollout `{"self_heal": false,
   "remote_commands": false}` from nereus000 (the token stays there), GET before/after logged. Back ON at the restore.
   Covered by Nick's card OK.
5. Encode-only: already done (above).
6. Commit `matrix.csv` (spec must do 1: the 10 verbs + all 115 keys; Tier 1 = the verbs + every catalog-v14 key with
   tier `control` or `engineering` (68) + the crop keys), then the first command.

## n, metric, bars
n = every matrix row once (spec). Verdicts SAFE / LIMITED / UNSAFE / NOT OBSERVABLE exactly as the spec's "Verdicts per
row" and "Pass / fail". Bars from the spec, restated: wake ≤ 481 s (a separate uncommanded boot, only for keys that
change wake work); cjxl `peak_rss_kb` ≤ 170 MiB and 0 rfb=mem for encode keys; 0 FALLBACK/ERR on effect boots (except the
anti-brick row, whose fallback is the expected result); 0 kernel OOM; SD free ≥ 2 GB.
**Anti-brick key (spec must do 5), chosen now:** `still.raw.rgb_encode_max_s = 5`. Expected: rfb=time JPEG fallback on
that wake and the next 2, ping answered, reset accepted, the wake after the reset sends a JPEG XL.
**Guard rows (spec must do 3b):** commands.enabled / commands.topic / uplink.uart.port / uplink.uart.baudrate run ONLY
without `cfm` (they must revert within `tx2_boot3`); never confirmed on the bench.

## Stop rules
- Spec C1–C9 as written (C1 bricked → power-cycle, UNSAFE, stop, tell PE + PjM; C9 command path cut → ssh restore from
  the step-2 backup).
- Power: SPOT-33507C battery < 3.85 V or charger THERMAL_FAULT → pause, tell the PjM.
- **Nick moves SPOT-33507C to a window (spec facts):** pause at the current step boundary when he announces it; after the
  move check console + ssh, then re-run the step in progress. Console path lost → stop and tell the PjM.
- C6 time: Tier 1 progress checked Tue 6 PM; the restore is never cut.

## Restore (spec must do 9): starts Wed 10/21 noon before any further rows; done by Wed 6 PM
`reset {"all":1}`, then the soak overlay re-applied from the step-2 backup (window 07:00–12:00 enabled,
heal_offwin_max 40, focus/exposure/contrast as backed up) → `get` hash a4e668c5 == the backup's; crontab byte-identical
to the backup; software_sha 2cc1afdb4831; `hil_restore_schedule.sh SPOT-33507C` (hourly 3600000/600000); SPOT-33507C
rollout self_heal + remote_commands ON (GET logged); one uncommanded wake verified (cfg hash, 0 [CFG][ERR]).
Rollback if needed: the S2 tar.

## Cost
bmcam004 + SPOT-33507C Mon ~noon → Wed noon (~45 forced boots for Tier 1). TE3 ~2 days. Nick ~5 min (this card).
No cellular data (console path). Displaces bmcam004's report-only soak health (unscored).

## Outputs
Run folder `~/NereusData/bm_cam_legacy/2026-10-19_cmd_coverage_bmcam004/` (never git): run_manifest.json, backups with
sha256, commands.log (every console line), per-boot cycle logs, matrix.csv filled (verdict + evidence per row),
RESULTS.md, a one-page summary for Nick. In git: this card, matrix.csv, RESULTS.md.
