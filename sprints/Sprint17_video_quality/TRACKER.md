# Sprint17 — Tracker

Branch: feature/sprint17-video-quality (off development @ a38b3fd).
SPEC: sprints/Sprint17_video_quality/SPEC.md (SPECCED 2026-08-18).
Units: bmcam000 (dev), bmcam003 + bmcam004 (A/B pair, Nick granted
2026-08-18 — see §5 hazard note).

## 1. Spec + evidence

- [x] Sensor-mode probe BEFORE design (`tools/probe_video_sensor_modes.sh`,
      `runs/sprint17_sensor_mode_probe_20260818/`). Found: production video
      1.88x upscaled; `--roi` relative to the sensor MODE's field, not the
      sensor; 1920x1080 encoder ceiling; `--qp` absent from this build;
      1080p30 teardown crash.
- [x] SPEC written, reviewed with Nick, decisions locked (six-row preset
      table; `--qp` dropped; mux dead time accepted for MVP; 1080p30
      blocked; migration = option A).

## 2. Implementation

- [x] `video_geometry.py` — sensor-mode table (measured FOVs + readout fps
      caps), preset table, `crop_to_roi` against the MODE's field,
      `available_pixels`, the enforced no-upscale rule, fps clamp + 1080p30
      block, storage/bits-per-pixel math, loud `describe()`.
- [x] Video-only YAML keys: `video.preset` / `crop_native_xywh` / `output` /
      `sensor_mode`; stills keys no longer read by the video path.
- [x] Encoder knobs `video.encoder.{profile,level,intra,denoise,sharpness}`,
      emitted only when set (absent block == today's defaults).
- [x] `--mode` ALWAYS passed (D-S17-2).
- [x] Sprint15 `crop_xywh_to_roi` / `even_video_output_size` REMOVED; a test
      asserts they stay gone.
- [x] Sidecar v2: preset, sensor_mode, avail_px, scale, encoder block.
- [x] Settings GUI: video preset dropdown + five encoder fields; the stills
      width field relabelled "Photo resolution (stills only)".
- [x] Boot log prints the full geometry with both coordinate systems, the
      available detail, the scale factor, GB/day and ring window.

## 3. Tests — 737 green (baseline 692, skipped=1)

- [x] `tests/test_video_geometry.py` (40 tests) anchored to MEASURED
      hardware values, not to the module's own arithmetic: the 1536x864
      field-of-view trap, the 533x299 defect reproduced then refused, the
      no-upscale boundary, preset-table invariants, migration behaviour,
      fps rules, parsing, mode auto-pick, field math.
- [x] Config-level contract tests in `test_video_config.py`.
- [x] `rc_field_template` completeness + runtime-manifest coverage tests
      (see §6 — both found real pre-existing bugs).
- [x] Stills path byte-identical: only `video_*` modules changed.

## 4. Hardware gates (bmcam000, camera freed and restored each time)

- [x] Gate 1 — suite green, stills untouched.
- [x] Gate 2 — every preset ffprobe-verified at its stated output size from
      a crop supplying >= that many available px. **6/6 PASS**
      (`runs/sprint17_preset_validation_20260818/`).
- [x] Gate 3 — upscaling config refused at config time, loudly and by name
      (unit-tested; the message carries the arithmetic).
- [x] Gate 4 — `--roi` mapping proven on hardware: the crop libcamera
      ACTUALLY applied, read from its own `-v 2` output in native
      coordinates, matched the preset's crop for all six presets.
- [x] Gate 6 — encode wall time inside clip wall time at every preset;
      confirmed again in production (encode_s 300.7-301.0 s for 300 s
      clips). CMA watched: `stills_roi_1600p` is the tight one (1.7 MB
      free) — see hazards.
- [x] Gate 7 — 1080p30 blocked at config time; 720p30 still boots.
- [ ] Gate 5 — A/B clip set in a LIT scene for Nick's eyeball. Started
      (§5); the current pair is indoor-daylight and both units UNDERSHOOT.

## 5. Fleet state (2026-08-18 20:35Z, all on the Sprint17 runtime)

| unit | preset | output | fps | cap | achieved | ring | role |
|---|---|---|---|---|---|---|---|
| bmcam000 | stills_roi_1000p | 1000x562 | 14 | 2.5 | 1.07 | 7.6 d | migration default in HIL |
| bmcam003 | wide_1080p | 1920x1080 | 15 | 9.3 | 5.42 | 1.5 d | A/B high |
| bmcam004 | wide_1080p_lean | 1920x1080 | 15 | 6.0 | 5.37 | 1.5 d | A/B lean |

Each unit: YAML backed up (`.before_sprint17_*`), runtime backed up by
`deploy_rc_runtime.sh` (`/home/pi/backups/BM_Devel_Pi_before_rc_deploy_*`),
branch at `~/repos/bm_cam_legacy_sprint17`.

**First A/B result (`runs/sprint17_ab_20260818/`): 9.3 Mbps buys nothing
over 6.0 at this scene complexity.** The high unit reached 58 % of its cap
(5.42 Mbps), the lean unit 89 % (5.37 Mbps) — within 1 % of each other.
Both ring windows land at ~1.5 days rather than the table's 0.87/1.34,
because neither fills its cap. If this holds in water, `wide_1080p_lean`
is the better default and `wide_1080p` is headroom nobody spends.
Measured dead time at ~200 MB clips: 7.4-8.4 % (boundary 22-25 s).

Caveats before trusting it: an indoor office scene is not particulate
water, and the three units are not framed on the same scene — 003 and 004
are in the same room from different positions, bmcam000 in another. A
strict A/B needs them physically aimed at one lit scene (Nick's end).

**HAZARD carried:** bmcam003 is Sprint18's designated unit and bmcam004
was the untouched control. Nick granted both for the A/B on 2026-08-18.
Sprint18 work on bmcam003 must account for the Sprint17 runtime now
deployed there; the pre-deploy tar is the way back.

## 6. Pre-existing bugs found (not Sprint17 regressions)

- [x] `video_geometry.py` was missing from `tools/rc_runtime_manifest.txt`.
      A field update copies ONLY manifest files, so it would have installed
      a `video_recorder.py` that cannot import — the bmcam003 2026-08-01
      missing-module lesson, about to repeat. Fixed + test.
- [x] `video_settings.py` has never been in `rc_run_capture_cycle.sh`'s
      py_compile gate (since Sprint15): a syntax error in the settings
      backend would surface only when a customer opened the page.
      Fixed + test.
- [x] `rc_field_template` has no `camera_controls` block, so focus fields
      render VIEW-ONLY on every fresh unit — the same gap patched on
      bmcam004 on-unit in Sprint16 but never in the template. Added
      present-but-auto (reproduces prior full-auto behaviour). **The focus
      VALUES a fresh unit should ship with are Nick's call.**

## 7. Remaining

- [ ] Gate 5: lit-scene A/B with the units aimed at one scene; denoise
      sweep (cdn_off / cdn_fast / cdn_hq) at a fixed preset — the knob most
      likely to matter underwater and still completely untested.
- [ ] Overnight HIL on the three units; replace the SPEC's interpolated
      dead-time estimates with measured `boundary_s` at 1080p.
- [ ] Re-test the 1080p30 teardown crash (Finding 5) to decide whether the
      block can be lifted.
- [ ] Decide `wide_1080p` vs `wide_1080p_lean` as the shipped default once
      water footage exists.
- [ ] PR -> development.
