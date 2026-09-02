# day02_validation_20260902 — --look-profile flag + full-day validation

Inputs: 7 bmcam001 frames (2026-09-01 17:00/18:00 + 2026-09-02 14:00-18:00Z
hourly), staged from ~/Downloads/SPOT-33361C_BMCAM_001_Day02 (duplicate
Day01 files verified byte-identical). Confirmed geometry (deployment team,
2026-09-02): camera 17 ft (5.18 m) deep, reef 3-4 ft (z-card 1.1 m),
camera 12 in (0.30 m) off the floor. NOTE: TG-7 EXIF manometer said 1.2 m
— its gauge was likely not zeroed; flagged to deployment.

## New: 7-frame fused site depth map (depth_fusion/)

fuse_depth.py (bench venv, DA-V2 Small @2x): per-frame disparity for all 7
frames, per-pixel MEDIAN fusion. Static scene + fixed camera means each
frame is a noisy measurement of one geometry; the median cancels
lighting-driven errors (sunlit sand read far, shadow read near, caustics).
Per-pixel median abs spread across frames: 0.0177 (1.8% of range).
fused_disp_7frames.npy replaces the old 2-frame map for this site.

## New: --look-profile <json> in finish_v2

Applies the per-site L*-banded a*/b* chroma profile
(runs/tg7_look_transfer_20260902/look_profile_tg7.json). Default off.

## Full-day validation (corrected/, scores/)

Preset: v2.2 + look profile + 7-frame depth + confirmed geometry
(--z-card 1.1 --near-ratio 0.45 --camera-height-m 0.30 --water-depth-m 5.18
--lsac-filter guided_luma --finish-style v2 --red-wb-cap 1.1 --sharpen 0.4
--stretch-black 0.0 --blue-wb-cap 1.15 --stretch-white 0.98 --warm-blend
--green-trim 1.0 --look-profile .../look_profile_tg7.json)

- Card detected in all 7 frames. WB gains identical across frames (red AND
  blue pinned at caps -> gains are cap-determined; card sets exposure) —
  gallery-consistent by construction.
- dE2000 15.5-19.4, gray angular 4.0-10.0 deg across the day (pre-profile
  baseline was ~27 / ~30 deg).
- Coral a* per band within ~2 units of the TG-7 target on every frame,
  both days, 14:00-18:00 light. The profile (fitted on ONE 17:00 frame)
  generalizes across the full day.

## Deliverable

cutsheet_deployment_before_after.jpg — deployment-shareable: TG-7 framing
reference + before/after for each of the 7 frames. External-safe wording.
