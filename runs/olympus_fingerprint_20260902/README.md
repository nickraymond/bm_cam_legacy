# olympus_fingerprint_20260902 — TG-7 "true color" reference fingerprint

Deployment-team reference image: `P9011394.jpg` (archived here as
`P9011394_orig.jpg`; `P9011394_2000w.png` is the working downscale).
**OM System TG-7**, 2026-09-01 11:18 local, same AOML reef structure as the
bmcam frames, **Reef Reference Card V2 in frame**. EXIF: WhiteBalance=1
(manual — TG-7 underwater WB preset, LightSource=255), 1/800s f/2.8 ISO100,
flash off => shallow bright midday, in-camera pipeline only (no post).
`olympus_close1/2.png` are earlier screenshots (no EXIF; superseded).

## Findings (fingerprint_tg7_vs_seathru.png, coral-only crops)

1. **The TG-7 signature is the a* axis, not b*.** Coral a* is flat at ~0
   (-0.5..-3) across every luminance band — that is what "brown, no green
   washout" means numerically. b* ramps -10 (shadows) -> +14 (highlights);
   our v2.1 b* curve already roughly matches that shape.
2. **Our v2.1 gap is a residual green cast**: a* -13 (shadows) to -42
   (highlights) on the bmcam frames. This dwarfs the yellow-axis tuning.
3. **Sea-thru is not ground truth.** Run on the TG-7 image it over-warms an
   already-neutral render: a* pushed to +13..+4, delta-b* +10..+25
   everywhere, card patch dE2000 23.0 -> 27.7 (worse), even though gray
   angular improves 20.3 -> 13.0 deg. Chasing sea-thru's render chases an
   overshoot.
4. **"True color" is still not colorimetric truth**: the TG-7's own card
   score is dE2000 23.0 / gray 20.4 deg (scores_tg7/) — attenuation at the
   card's distance remains. The perceptual target is coral a* ~ 0, not
   card-perfect patches.

## Caveats

- Cross-camera / cross-conditions: TG-7 at ~1m depth in bright midday sun,
  bmcam at 4.6m, different optics + sensor. Part of the a* gap is scene
  physics; the card in this frame lets a future run separate that.
- Sea-thru run used the research-only quarantined bench
  (research/seathru_benchmark, hainh/sea-thru MIT + DA-V2 Small), z ramp
  0.5-4.0 m assumed. Benchmark use only — never ships.

## Artifacts

- `fingerprint_tg7_vs_seathru.png` — TG-7 | TG-7+sea-thru | delta-b* map,
  plus coral-crop luminance and a*/b*-by-band vs our v2.1 + bmcam sea-thru
- `scores_tg7/`, `scores_tg7_st/` — card scores (TG-7 as-is; after sea-thru)
- `seathru_out/` — sea-thru render + depth + params
- `crop_*.png` — the exact crops behind the histograms

## Next (not yet run)

Retarget v2.1's finish at the TG-7 signature: drive coral-band a* toward 0
(the green residual), keep the current b* ramp. Candidate knobs: raise
red WB relative to green (TG-7's UW preset applies strong red gain), or an
a*-anchored green trim in the L*35-80 bands. Run our full card-anchored
pipeline on P9011394 itself (card is in frame) to separate camera physics
from correction error.
