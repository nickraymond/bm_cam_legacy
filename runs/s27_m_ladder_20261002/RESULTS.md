# M106 — bm #106 (registry v7, F-G3-4/5/8) on the bench — RESULTS (`runs/s27_m_ladder_20261002`)

**M1–M6: PASS on bmcam004 (full) and bmcam003 (M1–M3). L1b: sent on both (device-view read-out after
Sofar ingest).** development 34a6222 (#106) deployed to both units 2026-10-02 13:17 / 13:37 PDT; the
retired encoder keys are gone, the measured image-processing bounds are enforced on the unit, and every
camera flag reaches rpicam exactly once.

Spec: `sprints/Sprint27_remote_config/LADDER.md` (M1–M6, L1b). Timeline: `gate.log`. Commands/answers:
`steps.log`, `commands.log`, `m6.out`. Deploy logs: `pulled/*_field_update_*.log`. Times PDT (UTC in logs).

## Criteria

| id | criterion | bmcam004 | bmcam003 | evidence |
|---|---|---|---|---|
| M1 | deploy with `--accept-print-config-diff`; diff only the `[VID] encoder knobs:` line | PASS: `-… denoise=default sharpness=1.0` / `+[VID] encoder knobs: all rpicam-vid defaults`, nothing else; SUMMARY PASS | PASS (same one-line diff) | `pulled/*_field_update_*.log` |
| M2 | first boot: registry v7, no LKG/v1 fallback, retired warnings, encoder knobs default | PASS: `[CFG] config v2 … hash=683a9a62 … registry=v7`; `encoder.sharpness=1.0 is retired (registry v7): moved to camera.image_processing.sharpness`; `encoder knobs: all rpicam-vid defaults` | PASS (hash f333bfb7) | `pulled/*_M2_boot.txt` |
| M3 | `config_v2_upgrade.py` dry run then `--write`; same hash; no warning after | PASS: "would rewrite (same values, same hash)"; rewritten, base hash 67f930c4 unchanged, backup `*.before_upgrade_20261002T201950Z`; next boot no warning, effective hash 683a9a62 == M2 | PASS (backup `…T203935Z`, f333bfb7 == M2) | `pulled/*_M3_*.txt` |
| M4 | `set video.record.encoder.denoise` → `e:key`, nothing stored | PASS: `'video.record.encoder.denoise' is not a setting e=key` | — | `steps.log` M4 |
| M5 | still + clip with denoise + sharpness: each flag ONCE, no controls_dropped | PASS: still argv `--sharpness 2 … --denoise cdn_fast` once each; live `rpicam-vid` argv (pgrep during the clip) has `--sharpness 1 --denoise cdn_hq` once each; clips produced (9.7 MB, 79/79) | — | `pulled/M5.*`, `pulled/M5_video_argv.txt` |
| M6 | IP1–IP5 on the deployed code with the new bounds | PASS: sharpness 0/16, contrast 0.5/2.0, saturation 0/2.0, brightness ±0.25, 5 denoise, 6 hdr values → all acked, argv correct, no still blank (luma mean 30–145, sd 27–81); IP5: contrast 3 / 0.49, saturation 3 / 2.01, brightness 0.5 / −0.26, sharpness 16.01 → `e=val` with the new ranges; encoder sharpness → `e=key` | — | `m6.out` |
| L1b | `/refresh` then its 8 gets in id order, paced 65 s | 003: 8/8 answered `OK … N key(s)` (no `e:big`); 004: in `l1b_bmcam004.out`; `reported_known` / `refresh_hint` read-out after ingest | | `l1b_*.out`, `api/refresh_*` |

## Notes

- `camera.image_processing.sharpness` now defaults to 1.0 on these units (the YAML's old encoder
  sharpness moved there), so `--sharpness 1` appears in every argv while image processing is enabled.
- The recorder does not log its rpicam-vid argv; M5's video proof is the live process list
  (`pgrep -a rpicam-vid` every 0.5 s during the clip).
- New tools: `hil_deploy_unit.sh` (stay_on deploy with backup/re-arm), `hil_refresh.sh` (paced L1b),
  `hil_restore_schedule.sh` (production bus schedule). All rig-guarded; `test_hil_guards.sh` 0 failures.

## After M106: production switch (Nick 2026-10-02, bridge OK in the Test Engineer chat)

See `gate.log` and `prod004.out`: overlays reset (Nick's UI values included, his decision), `mode.run`
reset → per_boot cycle → halt; bridge back to 1 / 3600000 / 600000; stub-window boot armed + halted.
bmcam003 done 14:01 PDT; bmcam004 after its L1b.

## Production verification — first scheduled window 22:00Z (15:00 PDT), both units

| unit | bus on → off | Pi load (bridge current > 0.025 A) | wake→halt | margin to cut | burst | queue-full | verdict |
|---|---|---|---|---|---|---|---|
| bmcam003 | 22:00:00 → 22:10:00 | 22:00:08 → 22:08:18 | ~8.4 min | ~1.6 min | START 22:00:58, 181/181, 279.7 s | 16 (SPOT-33507C) | PASS |
| bmcam004 | 22:00:00 → 22:10:00 | 22:00:03 → 22:08:23 | ~8.4 min | ~1.6 min | START 22:01:00, 184/184, 283.7 s | 0 | PASS |

Both at base config 67f930c4 (no overlay), per_boot, cron armed, bridges 1/3600000/600000, heal cap 24/day
on both Spotters (EM, admin API). Halt = bridge current 0.034 → 0.018 A (mote + bridge only). Worst
case: the runtime's 8-min awake budget bounds a cycle with a full 40-chunk heal pass to ≈ 8.8 min, so no
budget/window change before G4. Evidence: `console/prod_verify_00{3,4}_2200.txt`.
