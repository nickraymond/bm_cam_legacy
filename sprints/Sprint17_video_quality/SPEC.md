# Sprint17 — GoPro-class video quality (video-only geometry + encoder knobs)

Status: SPECCED 2026-08-18 (spec review with Nick — preset table
locked at six rows, `--qp` dropped, mux dead time accepted for MVP,
1080p30 blocked pending Finding 5). Migration default is the one open
item (§5.1). Expands TODO-BM-013.
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
with its own Zero 2W memory history (manifesto rule 6). **DROPPED
from this sprint** (Nick, 2026-08-18); logged as Research-grade. And
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
`stills_roi_1000p` preset — the *intended* geometry of today's config,
rendered honestly. It is NOT byte-identical to shipped behaviour,
because shipped behaviour is the bug. Full reasoning and the options
weighed: §5.1.

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

Dead time scales with clip BYTES (overnight: 75 MB -> 5 s, 150 MB ->
23 s, 300 MB -> 40 s), and bytes scale with bitrate x clip length — so
the dead-time FRACTION is driven by bitrate, roughly independent of
clip length: ~13 % at 8 Mbps, ~2 % at 2 Mbps. Only the small fixed
per-boundary cost amortises over longer clips. Three points do not
fit a clean line (a linear model over-predicts badly at 75 MB), so
these are interpolations to be replaced by measured `boundary_s`. At
the 1080p bitrates this sprint wants, the tax is real either way.

Options — **DECIDED: (a), accept and measure** (Nick, 2026-08-18),
because every alternative touches the crash contract:

- (a) accept it, surface it in the CSV *(MVP now — chosen)*
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

## 4. Preset table (LOCKED, Nick 2026-08-18)

Six rows. `scale` <1.0 is a downscale (honest); 1.00x is 1:1; >1.0 is
refused at config time. Bitrates are the 0.3 bits/pixel/frame Hero8
class target for the full rows, ~0.18 bpp for the `_lean` rows. Ring
window is on the 116 GB card at the 75 % cap (~87 GB usable).

| preset | native crop (FOV) | mode | output | fps | scale | Mbps | GB/day | ring | dead time (est.) |
|---|---|---|---|---|---|---|---|---|---|
| `wide_1080p` **(default)** | 0,0,4608,2592 (full) | 2304x1296 | 1920x1080 | 15 | 0.83x | 9.3 | 100 | 0.9 d | ~15 % |
| `wide_1080p_lean` | 0,0,4608,2592 (full) | 2304x1296 | 1920x1080 | 15 | 0.83x | 6.0 | 65 | 1.3 d | ~10 % |
| `wide_720p` | 0,0,4608,2592 (full) | 2304x1296 | 1280x720 | 15 | 0.56x | 4.0 | 43 | 2.0 d | ~8 % |
| `wide_720p_lean` | 0,0,4608,2592 (full) | 2304x1296 | 1280x720 | 15 | 0.56x | 2.5 | 27 | 3.2 d | ~3 % |
| `stills_roi_1000p` *(migration default)* | 1504,846,1600,900 | 4608x2592 | 1000x562 | 14 | 0.63x | 2.5 | 27 | 3.2 d | ~3 % |
| `stills_roi_1600p` | 1504,846,1600,900 | 4608x2592 | 1600x900 | 14 | 1.00x | 6.5 | 70 | 1.2 d | ~11 % |

Dropped from the draft table on Nick's call: `tight_1080p_1to1` (the
fragile one — 12 fps readout cap, CMA to 292 kB free) and
`legacy_v15_asis`. The 1:1-versus-downscale question survives anyway:
`stills_roi_1600p` is a true 1.00x preset, so the A/B still answers
"does real per-pixel detail beat a clean downscale?" without shipping
the fragile geometry.

`legacy_v15_asis` is no longer a preset, but the shipped-behaviour
"before" clips are not lost — every unit is recording that geometry
right now, and the overnight corpus
(`runs/sprint16_overnight_20260818/`) already holds hours of it. The
A/B "before" comes from there, not from a menu entry.

Dead-time estimates are interpolated from the overnight observations
(75 MB -> 5 s, 150 MB -> 23 s, 300 MB -> 40 s), not from a fitted
model — the fit is poor at small sizes. The A/B CSV replaces them with
measured `boundary_s`.

## 5. Decisions taken at spec review (Nick, 2026-08-18)

1. **Preset table**: the six rows above. LOCKED.
2. **Migration**: see §5.1 — the question Nick asked to have laid out.
3. **`--qp`**: DROPPED. Not reachable in rpicam-apps v1.12.0 without
   moving video to Picamera2. Logged as Research-grade in TODO-BM-013.
4. **Mux dead time**: accept for MVP and surface it in the CSV
   (option (a) of D-S17-6). Options (b)/(c) are next-sprint work; both
   touch the crash contract, which is not worth reopening for a
   percentage this sprint can simply measure.
5. **30 fps**: allowed at 720p and below, **blocked at 1080p** by a
   config-time validation rule, until Finding 5 (the 1080p30 teardown
   crash) is reproduced and understood. Every table row is 15 fps
   (14 on the full-mode rows), so this constrains only manual fps
   edits.

### 5.1 Migration — what an existing unit gets when it has no video geometry keys

The choice is what a `video:` island with no `crop_native_xywh` /
`output` / `sensor_mode` resolves to on the first boot after deploy.

**First, the thing that is true of every option:** there is no
"preserve current behaviour" choice. Current behaviour IS the defect —
a 1.88x upscale of a crop nobody configured, at
`(1770,996,1066,599)`. Any correct geometry changes what the camera
sees. "Conservative" here means *smallest honest change*, not *no
change*.

**Option A — `stills_roi_1000p` (recommended).**
The unit boots with the geometry its YAML always *meant*:
crop `(1504,846,1600,900)`, full sensor mode, 1000x562 out.

- Field of view: shifts from the accidental box to the configured
  stills box — wider and re-centred. Video framing finally matches
  what the stills crop says.
- Detail: 533x299 real px -> **1599x899** real px behind a 1000x562
  output. A 0.63x downscale, i.e. genuine oversampling. This is the
  entire quality fix, delivered at the current output size.
- fps: 15 -> 14 (full-mode readout caps at 14.35).
- Storage: 2.0 -> 2.5 Mbps; ring window 4.0 d -> 3.2 d.
- Dead time: ~2 % -> ~3 %.
- Risk: low. Same file-size class, same cadence, same thermal
  envelope — the overnight 7 h-clean evidence still broadly applies.

**Option B — `wide_1080p`.**
The unit boots at full-sensor 1080p, 9.3 Mbps.

- Field of view: the **whole sensor** — much wider than either the
  accidental crop or the configured stills crop. Underwater that cuts
  both ways: more scene, but also more water column and backscatter in
  frame, and a smaller subject.
- Storage: ring window 4.0 d -> **0.9 d**. This is the real cost. A
  unit expected to hold ~4 days of footage now holds under one; two
  days out of contact and the footage is gone.
- Dead time: ~2 % -> ~15 %. Real recording loss, every clip.
- Thermal/CPU at that sustained rate has 20 s of evidence, not 7 h.
  bmcam003 ran 8 Mbps/30 fps clean overnight, which is reassuring but
  is one unit at a different geometry.

**Option C — refuse to boot without explicit keys.** Rejected. Rule 5's
unbrickable guarantee says a field unit must record, not brick, on a
config nit.

**Recommendation: A.** Two reasons that matter more than the quality
delta:

1. A migration default is what a unit gets when *nobody chose
   anything*. Option B silently rewrites the deployment economics —
   a 4x shorter footage window — for units whose owner never opened
   the GUI. That is a decision to be made per unit, with eyes open,
   not inherited from a code deploy.
2. It protects the fleet's current roles. bmcam004 is the untouched
   control; if a deploy silently moved it to full-sensor 1080p it
   would stop being a control.

The quality fix is **not** deferred by choosing A — A *is* the fix
(533x299 -> 1599x899 of real detail). What A defers is the resolution
increase, which is a separate, storage-expensive decision.

Sequencing that makes the eventual answer cheap: run this sprint's HIL
on bmcam000 at `wide_1080p` overnight. Then flipping the shipped
default to 1080p becomes a one-line change backed by a night of soak
data — encode headroom, thermals, real `boundary_s`, real ring
behaviour — instead of a preset table's arithmetic.

**Boot must say so, loudly.** Whichever option lands, the boot log
prints the old accidental native box, the new one, the available px
and scale factor, and the ring-window estimate — so a unit that
changed its framing announces it rather than being discovered later in
the gallery.

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
7. The 1080p30 block proven: a config asking 30 fps at 1920x1080 is
   refused at config time, loudly and by name (Finding 5); 30 fps at
   720p still boots.
8. bmcam003/004 untouched.
