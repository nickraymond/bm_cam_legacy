# Sprint17 — GoPro-class video quality (video-only geometry + encoder knobs)

Status: DRAFT for Nick's spec review, 2026-08-18. Expands TODO-BM-013.
Branch: feature/sprint17-video-quality, off development (post-Sprint16
merge, a38b3fd).
Unit: bmcam000 ONLY. bmcam003 is Sprint18's; bmcam004 is the untouched
control.

This sprint is ENGINEERING variables only. The customer-facing
"high/medium/low" simplification is a LATER sprint and is deliberately
not designed here.

## 0. What the probe changed about this sprint

A spec-blocking measurement ran first (`runs/sprint17_sensor_mode_probe_20260818/`,
tool `tools/probe_video_sensor_modes.sh`). It moved the sprint's centre
of gravity:

**Production video is 1.88x upscaled today.** `rpicam-vid` picks the
sensor mode from `--width/--height` alone, then applies `--roi` as a
digital zoom on that mode. The shipped argv selects mode 1536x864 and
leaves **533x299 real pixels** behind a 1000x562 output. And because
that mode reads only the centre 3072x1728 of the array, the intended
crop `(1504,846,1600,900)` actually lands as `(1770,996,1066,599)` —
**video FOV has never matched stills FOV**.

So the sprint's first job is not "add 1080p". It is **make the
resolution number mean something**. TODO-BM-013's framing ("more
resolution options plus the hidden knobs") stands, but the ranking
changes: geometry correctness first, encoder knobs second. Bitrate is
demoted — at 2 Mbps the shipped config is already 0.237
bits/pixel/frame, near the Hero8 quality class. It was never the
bottleneck.

**Stills are unaffected.** The stills path captures full native
4608x2592 and lanczos-DOWNSAMPLES in software; `output_size_for_crop`
raises on any upsample. This is a video-path defect only.

## 1. Nick's constraints (2026-08-18, binding)

1. **Video and stills get independent settings.** The YAML carries
   video-specific and stills-specific geometry separately, and the
   settings GUI is how they are worked with. Rationale (Nick): stop
   overloading one set of values whose meaning silently changes when
   capture_mode flips. This is the approved break of Sprint15
   constraint 4 / D-S15-3.
2. Upscaling past the available detail stays **FORBIDDEN**. No fake
   resolution, ever — enforced in code, not by convention.
3. Wider-ROI options up to the full 4608x2592 sensor.
4. Engineering variables now; the 3-4 customer tiers come later.
5. Stills path byte-identical. Full suite (692+) green.
6. Every coordinate system labeled at every boundary.

## 2. Hardware envelope (measured, not assumed)

| fact | value | evidence |
|---|---|---|
| max encoder output | **1920x1080** | 2304x1296 -> `failed to start output streaming` (rc=255) |
| mode 1536x864 FOV | centre **3072x1728** of the array | ScalerCrop range in `modes/prod_today.log` |
| mode 2304x1296 FOV | full 4608x2592 | `modes/wide_full_1080p.log` |
| mode 4608x2592 FOV | full 4608x2592, **14.35 fps cap** | `--list-cameras`; 12.1 fps measured at 14 requested |
| CMA at 1:1 1080p full mode | **292 kB free** of 256 MB | `shortlist/s_tight_1080p14_full.log` |
| 1080p30 (binned) | 570 frames then `double free or corruption`, rc=134 | `shortlist/s_wide_1080p30.log` — ONE observation |
| `--qp` | **does not exist** in rpicam-apps v1.12.0 | `unrecognised option '--qp'` |
| libav codec path | **not built** (`libav:0`) | `rpicam-vid --version` |

Two of these delete scope. `--qp` — TODO-BM-013's "true JPEG-quality
analog" — is not reachable: rpicam-vid has no such flag, and the libav
route that would provide `-crf` is not compiled in. Constant-quality
encoding would mean moving video to Picamera2, a different capture path
with its own Zero 2W memory history (manifesto rule 6). **Proposal:
drop `--qp` from this sprint** and note it as Research-grade. And
1440p does not exist to design for.

## 3. Design

### D-S17-1 — Video-only geometry keys

New keys in the `video:` island, in NATIVE 4608x2592 coordinates:

```yaml
video:
  crop_native_xywh: [0, 0, 4608, 2592]   # NATIVE sensor coords — video only
  output: "1920x1080"                     # encoded OUTPUT px
  sensor_mode: "2304x1296"                # explicit; never "auto"
```

`progressive_jpeg.crop` / `output_width` become **stills-only** and are
no longer read by the video path. A video unit and a stills unit stop
sharing a geometry meaning.

Migration: a `video:` island without these keys resolves to the
`stills_roi_1000p` preset (§4) — the *intended* geometry of today's
config, rendered honestly. It is NOT byte-identical to shipped
behaviour, because shipped behaviour is the bug. Units get the
corrected geometry on their next boot after deploy; the change is
announced in the boot log with both the old and new native boxes.

### D-S17-2 — The sensor mode is chosen by us, never by rpicam-vid

`--mode W:H:10:P` is always passed. Auto-selection is what produced
the 1.88x upscale and the FOV shift, so it is removed as a possibility.

### D-S17-3 — The no-upscale rule, enforced

For a chosen (crop, output, mode) the resolver computes:

```
available_px = crop_native_w * (mode_w / mode_fov_w)
```

and REFUSES to boot a config where `available_px < output_w - 2` (the
2 px slack absorbs libcamera's round-down). Loud named failure at
config time, the same doctrine `output_size_for_crop` already applies
to stills. `--roi` fractions are computed against the **mode's FOV**,
not the sensor — the D-S15-3 bug, fixed at its root.

### D-S17-4 — Encoder knobs as YAML + GUI engineering fields

```yaml
video:
  encoder:
    profile: "high"      # baseline|main|high
    level: "4.2"
    intra: 0             # GOP; 0 = encoder default
    denoise: "auto"      # auto|off|cdn_off|cdn_fast|cdn_hq
    sharpness: 1.0       # 0..16, 1.0 = normal
```

All five verified present in v1.12.0. `denoise` is the one most likely
to matter underwater (particulate); `sharpness` is the cheap
perceptual lever. Absent block = today's rpicam-vid defaults, so the
knobs are opt-in.

### D-S17-5 — A/B methodology (the deliverable Nick judges)

Paired same-scene clips, ONE variable at a time, on a LIT scene or in
a daylight window — the overnight run proved a dark bench undershoots
any bitrate cap, so dark A/B clips compare nothing. Per clip the CSV
carries: preset, crop, mode, output, fps, bitrate, **available px and
scale factor**, size, `encode_s`, `boundary_s`, CPU temp, plus the
derived **GB/day** and **ring window** and **mux dead-time %**.
Comparison artifact: a cut sheet grouping the pairs, labeled with
whether thumbnails are scaled (manifesto rule 13).

### D-S17-6 — Mux dead time: measure now, fix later

Overnight data fits `dead_s ~= 3 + 0.12 x MB`, so the dead-time
FRACTION is driven by bitrate, not clip length: ~13% at 8 Mbps, ~1.7%
at 2 Mbps. Longer clips only amortise the fixed 3 s. At the 1080p
bitrates this sprint wants, that is a real tax.

Options, for Nick to rank — **MVP now: accept and measure**, because
every alternative touches the crash contract:

- (a) accept it, surface it in the CSV *(MVP now)*
- (b) overlap: start clip N+1's encoder, then mux clip N while it
  records — dead time -> ~0, but an unmuxed `.part` now outlives its
  clip, so the boot sweep must mux orphans instead of deleting them
  *(next sprint)*
- (c) serve `.h264` and mux on download in the UI *(next sprint,
  overlaps Sprint18)*
- (d) `--codec libav` writing mp4 directly — **blocked**, libav not
  built

### D-S17-7 — Encoder stderr goes to the log

`_default_run` captures stderr but prints it only on failure, which is
why mode selection was invisible for a month. Video mode logs the
encoder's mode-selection lines once per clip at INFO. Cheap, and it
makes a regression like this self-announcing.

## 4. Preset table (proposal — this is the table to review)

Engineering names. `scale` <1.0 is a downscale (honest); 1.00x is 1:1;
>1.0 would be upscaling and is refused. Bitrates are the 0.3
bits/pixel/frame Hero8 class target, rounded. Ring window is on the
116 GB card at the 75% cap (~87 GB usable).

| preset | native crop (FOV) | mode | output | fps | scale | Mbps @0.3bpp | GB/day | ring | dead time |
|---|---|---|---|---|---|---|---|---|---|
| `wide_1080p` **(proposed default)** | 0,0,4608,2592 (full) | 2304x1296 | 1920x1080 | 15 | 0.83x | 9.3 | 100 | 0.9 d | ~14% |
| `wide_1080p_lean` | 0,0,4608,2592 (full) | 2304x1296 | 1920x1080 | 15 | 0.83x | 6.0 | 65 | 1.3 d | ~10% |
| `wide_720p` | 0,0,4608,2592 (full) | 2304x1296 | 1280x720 | 15 | 0.56x | 4.1 | 44 | 2.0 d | ~7% |
| `tight_1080p_1to1` | 1344,756,1920,1080 | 4608x2592 | 1920x1080 | **12** | **1.00x** | 7.5 | 81 | 1.1 d | ~12% |
| `stills_roi_1000p` *(migration default)* | 1504,846,1600,900 | 4608x2592 | 1000x562 | 14 | 0.63x | 2.5 | 27 | 3.2 d | ~2% |
| `stills_roi_1600p` | 1504,846,1600,900 | 4608x2592 | 1600x900 | 14 | 1.00x | 6.5 | 70 | 1.2 d | ~11% |
| `legacy_v15_asis` | (auto mode, as shipped) | auto | 1000x562 | 15 | **1.88x UP** | 2.0 | 22 | 4.0 d | ~2% |

Notes for the review:

- `legacy_v15_asis` exists **only** as the A/B reference — the "before"
  in every comparison. It is not offered as a setting.
- `wide_1080p` vs `tight_1080p_1to1` is the real question this sprint
  answers with clips: whole-sensor field of view at 0.83x, versus a
  narrow window at true 1:1. Underwater, my prior is the wide one wins
  (more scene, no fps cap, comfortable CMA) — but that is Nick's
  eyeball call, not a metric's.
- **No 1080p30 row.** Finding 5 (teardown crash, one observation) has
  to be re-tested before 30 fps at 1080p is offered at all.
- `tight_1080p_1to1` is capped at 12 fps by the 14.35 fps full-mode
  readout, and runs CMA to ~292 kB free. It is the fragile option.
- Every ring window at 1080p is **~1 day**. That is the honest cost of
  GoPro-class bitrate on this card, and it is a deployment decision,
  not a tuning one.

## 5. Open questions for the spec session

1. **Preset table**: approve as-is, or change the crop boxes? The
   `tight_1080p_1to1` box is a centred 1920x1080; a card-centred or
   subject-centred box may be better for the real rig.
2. **Migration**: is `stills_roi_1000p` the right landing spot for
   existing units, or should they land on `wide_1080p` and get the
   quality jump immediately?
3. **Drop `--qp`?** (Recommend yes — not reachable without changing
   capture path.)
4. **Mux dead time**: accept for MVP (recommend), or is (b) in scope?
5. **fps ladder**: keep 30 fps only at 720p until the 1080p30 crash is
   understood?

## 6. Gates

1. Full suite green (692+), stills tests untouched, stills path
   byte-identical (diff-proven).
2. Every preset ffprobe-verified at its stated output size, from a
   crop with >= that many available px — no upscaling anywhere.
3. A boot-time refusal proven for an upscaling config (loud, named).
4. `--roi` mapping proven against mode FOV on hardware, for all three
   modes.
5. A/B clip set on bmcam000 in a lit scene, comparison artifact + CSV,
   for Nick's eyeball.
6. Encode wall time < clip wall time at every offered preset
   (`encode_s` is the meter), CMA checked at each new geometry.
7. bmcam003/004 untouched.
