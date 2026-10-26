# Test card: NOAA on-land update rehearsal on bmcam004, USB power (Mon 10/26)

Drafted by TE5, Mon 10/26 ~10:00 AM PDT, at Nick's request ("I agree to the plan", TE5 chat ~9:40 AM). **This card has open
stop points (S1–S5). The run does not start, or stops at that step, until each is closed in writing.**
- **Spec:** SPEC_e4_noaa_release rev 12.1 (sha256 992590871995): must dos 3 and 4, R12-1 and R12-2. QE 010 PASS Sun 9:17 PM
  (per Lead 004; not checked by TE5).
- **Approval:** Nick's line in the executing TE's own chat, citing this card's sha. Field units are never touched: bmcam002 and
  SPOT-33361C are out of scope.

## Question
Does the on-land runbook take bmcam004 from bmcam002's exact start state to the target build and back (one rollback), with sha
and config hash verified at each end, in ≤ 60 min, over a phone hotspot?

## Start state and target (from the spec)
- **Start:** legacy **0d03a62** + bmcam002's shipped v1 profile
  `origin/aoml-production@6cb38dd:docs/aoml_reef_2026-08-18/shipped/bmcam002_camera_schedule.yaml` (sha256 117876ae0928, TE5
  checked 10/26; md5 37fd4ffb905d per its README).
- **Target:** the branch Nick's Mon call picks: S3 = bm development **613fb2c1**, or S2 = **2cc1afdb** (R12-1), with
  bmcam002's v2 target profile.
- **bmcam004 today:** S3 613fb2c15037, cfg d95aa41d (backend, 5–8 AM wakes). Its crontab = 6e7a4080 plus the TE5_RC_SAMPLER
  line.

## Stop points (open; each must be closed in writing before its step)
- **S1 (before step 1):** Nick's S3/S2 call, in writing.
- **S2 (before step 5):** bmcam002's v2 target profile. Its name under device_profiles/ (for `--profile`), sha and pinned
  `--print-config` hash are **unknown** (spec must do 2, R12-1). Today device_profiles/bmcam002/camera_schedule.yaml is the
  HEIC-era v1 profile.
- **S3 (before step 1):** how to put bmcam004 on 0d03a62 + the shipped v1 profile. No procedure exists. Proposed (unverified):
  run 0d03a62's own `tools/rc_field_update.sh --ref 0d03a62` with the shipped YAML installed as camera_schedule.yaml. PE/TE6 to
  confirm, or name the method.
- **S4 (before step 0):** bmcam004 takes a USB supply (unknown); Nick moves it at ~10:45 AM.
- **S5 (before step 6):** how to run "a capture-only image" with no Spotter (R12-1 checks: sidecar crop 1600×900,
  params[26]=10000, decodes on the Mac with the nvd staging decoder, 0 rfb). Command **unknown**.
- **Network:** the spec says a phone hotspot. If Nick's phone isn't available, home Wi-Fi is a logged deviation, by his choice
  in the OK line.

## Steps (T0 = step 2; time box 60 min from T0 to the end of step 7)
0. **Catch the Pi awake on USB.** On boot, cron runs a cycle (~8 min). Once ssh is up, back up the crontab to
   ~/hil_backup/<TS>/crontab_ARMED.txt, comment out the @reboot cycle line, and SIGTERM rc_run_capture_cycle (as
   hil_bus_always_on does, by hand). Then record the baseline: software_sha (613fb2c15037), crontab sha256, `--print-config`
   hash (d95aa41d), and a backup tar of /home/pi/BM_Devel_Pi with its sha256 (bmcam004's own restore point).
1. **Start state (S1, S3):** install 0d03a62 + the shipped v1 profile, per S3. Verify software_sha starts 0d03a62 and the
   camera_schedule.yaml sha256 = 117876ae0928.
2. **Runbook step 0 (T0):** record software_sha.txt and the sha256 of camera_schedule.yaml. They must equal 0d03a62 +
   117876ae0928, else stop.
3. **SD download:** rsync images/ + cron_logs/ to `~/NereusData/bm_cam_legacy/2026-10-26_rehearsal_noaa_update/sd/`; check
   the file count and bytes match.
4. **Migration, dry run, then write:** `python3 ~/repos/bm_cam_legacy/tools/config_migrate_v1_v2.py --app /home/pi/BM_Devel_Pi`
   (exit 0 = parity gate OK; 3 = refused, stop), then the same with `--write`.
5. **Update (S2):** `tools/rc_field_update.sh --ref <613fb2c1|2cc1afdb> --profile <S2 name> --leave-disarmed`
   (`--accept-print-config-diff` only for the expected diff). Record the restore command it prints. Then:
   - `--print-config` hash = the pinned hash (S2);
   - S3 only: /home/pi/BM_Devel_Pi/FORCE_SHRINK_FALLBACK is ABSENT.
6. **Capture-only check (S5):** per R12-1's four checks.
7. **One rollback:** run the printed restore command (`tar xzf …before_rc_deploy_…tgz -C /home/pi`). Verify software_sha =
   0d03a62, camera_schedule.yaml sha256 = 117876ae0928, crontab as before step 5. Note the time: it must be ≤ 60 min from T0.
8. **Restore bmcam004 (not in the 60 min):** tar back to step 0's backup, then check software_sha 613fb2c15037 and
   `--print-config` d95aa41d. Crontab = 6e7a4080: re-arm, and drop the soak sampler line, since the soak is over. Halt. Nick
   moves it back to SPOT-33507C. The first production wake shows cfg d95aa41d at the backend.

## Bars (spec)
- Steps 2–7 end to end in ≤ 60 min, including one rollback.
- Each end state verified by sha + config hash.
- Any step failing twice, or the 60 min passing → roll back (step 7) and record a fail.
- Must do 9 (keyed in-window wake on the shipped profile) is **not** covered: no Spotter on USB (R12-2). Nick's call.

## Stop rules
- A rollback fails (F4) → stop and leave bmcam004 disarmed + halted; tell Nick and the Lead.
- USB power or network lost mid-step → wait; never power-cycle during rc_field_update; resume from the last verified step.
- Never write to bmcam002 or SPOT-33361C.

## Outputs
Run folder `~/NereusData/bm_cam_legacy/2026-10-26_rehearsal_noaa_update/`: commands + timestamps (rehearsal.log), every
printed restore command, sha256 of each backup, SD listing, migration output, --print-config before and after, and the timing
table. In git: this card + RESULTS.md.
