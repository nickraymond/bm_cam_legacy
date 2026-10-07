# Sprint28 LADDER R5 — low-gain stills on hardware (bm #133, stacked on #120)

Question: does libcamera's AGC honour the patched `exposure_modes.normal` (shutter [100, cap], gain [1.0, max_gain])
that `camera.exposure.profile low_gain` writes into a per-unit tuning copy? Desk tests (23) cover the patch, not the AGC.
Owner: Test Engineer. **Plan approved by the EM 2026-10-05 (GO to deploy the #133 branch on bmcam003 for R5).** Update 10/5: Nick set the cap default to **30000 µs** (#133 head b19a4aa); the shipped default runs first, 16667 is the optional night comparison; max_gain stays 16.
Merge order (EM): #120 → development after the R4 verdict (R4.3 fallback rate = Nick's product decision);
#133 → development after R5.1–R5.3 PASS in the Wed 08:00Z pull, before the RC deploy. R5 FAIL → RC without low-gain. Times UTC (PDT = UTC − 7). Evidence: `runs/s28_ladder_20261004/` (R5 section).

## Host: bmcam003 / SPOT-33507C, production per_boot wakes, outdoors (proposed)

Why bmcam003 and not nereus002: bmcam003 is a TE bench host (ssh pre-approved), already outdoors on the production
hourly schedule, and the day → dusk → night run gives bright AND dim scenes with no hands-on and no bus change.
bmcam004 is busy (RAW reference + #120 tip deploy, Tue 16:00–17:00Z). nereus002 is not a TE host. Alternative if the
camera session owns nereus002: a lens cover there gives dim in minutes (both caps in one hour).
bmcam003 has no libjxl: R5 tests TODAY's still path (pjpg); the nrjxl `--raw` path gets low_gain in the RC run.

## Schedule (SPOT-33507C: a command sent after :10 lands at the next :10 sync and applies at the following boot)

| when (Z) | step |
|---|---|
| Tue 10/6 ~14:50 | nereus000: `sudo systemctl stop hil-r1-cmdres.timer` (its hourly `set mode.media` would fight R5; restarted for the RC) |
| Tue 15:00 window | one-window deploy of `feature/sprint28-low-gain` **b19a4aa** (catch at boot, ARMED crontab backup, disarm, SIGTERM, `rc_field_update --ref feature/sprint28-low-gain --leave-disarmed` from /tmp, verify sha, re-arm, halt). Read once: `--list-cameras` model + `exposure_profile/` state dir |
| Tue ~15:15 | backend (Sofar lane): `mode.media=still`, `camera.exposure.profile=low_gain` (cap default **30000**) → applies at the 17:00 boot |
| Tue 17:00 → 01:00 | **bright** wakes (cap 30000, the shipped default) |
| Wed 02:00 → 03:00 | **dusk** wakes (cap 30000; sunset ~01:45Z) |
| Wed ~02:15 | backend: `camera.exposure.max_shutter_us=16667` (optional comparison) → applies at the 04:00 boot |
| Wed 04:00 → 06:00 | **night** wakes (cap 16667, comparison) |
| Wed ~06:15 | backend, refusal check: one change `camera.controls_enabled=true, camera.exposure.enabled=true, camera.exposure.shutter_us=10000` → expect refusal (backend plan rule if the catalog has it, else unit `e:xk`); config hash unchanged |
| Wed 08:00 window | read-only pull of `cron_logs/rc_cycle_*.log` + still sidecars since Tue 15:00 (one ssh, no write); then the RC deploy per R1RC |

## Criteria

| id | PASS when | evidence |
|---|---|---|
| R5.1 tuning copy used | every low_gain wake's capture shows `--tuning-file <app>/exposure_profile/tuning/…` and libcamera's "tuning file …/exposure_profile/tuning/…" (not `/usr/share/…/imx708_wide.json`); sidecar `exposure_profile_applied true` | cycle logs, sidecars |
| R5.2 dim: shutter capped first | every dusk/night wake: ExposureTime ≤ cap (30000 for the default phase, then 16667; +1 line-time tolerance) and AnalogueGain > 1.0 only when ExposureTime is at the cap (within 2 %); gain ≤ max_gain 16 | END `et_us` / `ag`, sidecar metadata |
| R5.3 bright: floor gain | every daylight wake: AnalogueGain ≈ 1.0–1.12 with ExposureTime < 30000 | same |
| R5.4 refusal | the fixed-shutter + low_gain change is refused (`xk` or backend plan); no wake captures with a fixed shutter | console `<CF>`, ledger |
| R5.5 no regression | every wake delivers its still (complete ≤ 3 h, 0 redundant heals); wake→halt ≤ 540 s; START every wake | media table, wake reports |

Measured, not gated: auto vs low_gain at the same light (Tue morning 15:00/16:00 wakes are still auto = baseline); the
30000 (dusk) vs 16667 (night) behaviour (both may sit at max_gain in full dark: then R5.2 shows shutter = cap, gain > 1).

## Restore

The RC deploy (Wed, R1RC) replaces the runtime with the development tip; restart `hil-r1-cmdres.timer` for the RC.
After R5 (PASS or FAIL): reset `max_shutter_us` to the default 30000. If R5 FAILS: backend `camera.exposure.profile=auto`, `max_shutter_us` reset to the default 30000, `mode.media` per the RC; the unit needs no
other restore (the tuning copy lives in `<app>/exposure_profile/`, unused under auto).
