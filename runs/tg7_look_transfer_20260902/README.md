# tg7_look_transfer_20260902 — learning the Olympus look from our own images

Question (Nick): how do we get our renders closer to the TG-7 output as
post-process, given the 8-bit-crushed red? Olympus does not publish its
underwater-mode internals (scene-adaptive red-restoring WB presets
Shallow/Mid/Deep + graded scene modes is all that is public), so instead of
reverse-engineering docs we FIT the look empirically from the paired scene:
the TG-7 frame P9011394 shows the same reef as the bmcam frames.

## Prototypes (both applied on top of v2.2 = v2.1 + green-trim 1.0)

A. **Band Lab look profile** (`look_profile_tg7.json`, `*_lookA.png`):
   per-L*-band a*/b* deltas between our 17:00 coral crop and the TG-7 coral
   crop, applied as a smooth L*-interpolated chroma offset. 12 numbers,
   deterministic, no ML.
B. **Red reconstruction** (`*_redreconB.png`): R = poly2(G,B) least-squares
   fitted on TG-7 pixels (healthy red), synthesized red blended 0.7 into
   our render.

## Results (cutsheet_look_transfer.jpg, scores/)

- **A lands on the TG-7 signature almost exactly** — coral a* within ~1
  unit of target on BOTH frames (fit on 17:00 only; generalized to 18:00),
  b* matched. Card scores: dE2000 15.5/18.4, gray angular 4.1/6.6 deg —
  project-best, better than the TG-7's own card numbers (23.0 / 20.4 deg).
- B moves a* correctly but drifts b* warm and flattens texture tonality;
  keep as a fallback idea (could feed a per-site refit).

## Product concept this validates

**Site look profile**: one TG-7 (or any trusted reference camera) shot of a
deployment site calibrates a 12-number profile; every bmcam frame gets it
applied after the v2.2 pipeline. Cheap to store/transmit, per-site, and
re-fittable from any new reference dive. Not information recovery — the
capture-side fix (TODO-CAM-001 experiment C, awbgains) remains the real one.

## Caveats

- Profile is fitted on ONE site / one lighting hour; needs validation across
  the deployment's hour-by-hour light (the 18:00 generalization is a good
  early sign).
- Card color patches desaturate slightly under the profile (cosmetic layer
  only; the measurement layer stays un-tone-mapped by design).
- TG-7 reference itself is a graded render, not colorimetric truth.

## Reproduce

The prototype script is inline in the session log; profile JSON + outputs
here. Promotion path: add `--look-profile <json>` to finish_v2.
