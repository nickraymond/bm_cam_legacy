# EPIC: v3 reference card → cloud colour correction on the RAW (draft r1, for Nick's review)

Owner: Nick. Coordinator: EM. Started 2026-10-05. Part of R1 (RELEASE_PLAN §2d): the Friday RC should unlock it,
and the pool week (10/12) proves it.

## 1. Goal

Every JPEG-XL (nrjxl) still with a v3 card in view gets a **card-corrected underwater render made from the linear
RAW in the cloud**, plus a **colour-quality score**, both visible in the gallery. Measured against the card's true
colours, it must beat today's JPEG by ≥ 30 % lower ΔE00 (pool spec rule).

## 2. Where we are (inventory 2026-10-05; details in the EM scratchpad v3card_inventory)

| piece | status |
|---|---|
| v3 card c1 | 10 Sticker Mule matte stickers arrive 10/5 (420×270 mm, tag25h9 IDs 0–3, 4 greys + 8 colours); to be mounted on 3–6 mm board |
| card truth | **no instrument measurements for any card** (V1/V2 "measured" = camera RGB; V3 = design placeholders) |
| detection + sampling | rig `color/card.py`, `locate.py`, `patches.py`, `metrics.py`: YAML-driven, already handle V3 |
| backend detection | vendored V2 code: tag36h11 only, V2 layout, 1× scale → **cannot see a V3 card** |
| backend colour | only `cheeca_v3` (GRVI) on the 8-bit display JPEG; no after-correction score; processing off for SPOT-31593C / 33507C |
| RAW path | `raw_render.linear_camera_rgb()` exists, unused; `raw_card_v1` (Sprint28 S2b stretch) has no code |
| gallery | shows filter status only; no quality score |
| pool | spec ready, names V1; `pool_score.py` not built; rig branches `feat/card-v3-c1-reference`, `feat/pool-raw-prep` unmerged |

## 3. Plan

**Phase 0, card truth (Nick, Mon–Tue):** mount the c1 stickers; caliper the tag spacing (expected 331.0 × 181.0 mm)
and check flatness; get each patch's true colour, dry and wet (spectrophotometer, or side by side with a known
chart under the same light); write a `measured:` block in `configs/cards/nereus_v3_c1.yaml`. Start the salt-water
soak coupon. Never two c1 copies in one frame (same tag IDs).

**Phase 1, cloud RAW correction (backend session, Mon–Wed):**
1. Port the rig card stack (card YAML loader, multi-scale locate on the green channel, patch sampling, ΔE00 metrics)
   into the backend as the single card library; card YAMLs become versioned profiles. Support tag25h9 (V3) and
   tag36h11 (V1/V2).
2. New processor `raw_card_v1`: read the `.nrjxl`, decode → `linear_camera_rgb`, locate the card, gray balance on the
   grey patches + 3×3 colour matrix from the colour patches, apply on the linear RAW, render (tone curve per the
   chosen look) → `variants/{stem}__raw_card_v1.jpg` + sidecar.
3. Score in the sidecar: held-out ΔE00 after correction (fit on a subset, score the rest), grey neutrality, red-channel
   SNR, clipping, card found / not found + reason. Show it in the detail view next to the switcher.
4. Memory within Render's 512 MB; deterministic; tests on fixtures (V3 synthetic frame + real nrjxl 57389).

**Phase 2, bench proof (Thu, inside the RC gate):** one v3 card in view of bmcam003/004 in the box (check that the
card sits inside the 1600×900 nrjxl crop), processing enabled for those Spotters, and the variant + score appear
in the gallery. Pass = card found on ≥ 90 % of daylight stills, held-out ΔE00 reported, no worker failure.

**Phase 3, pool week (10/12):** rig `pool_score.py`, depths 3/6/9 ft + dusk, the v3 card at ~1 m (per Nick), the
IMX708 nrjxl vs today's JPEG at 180/250/500 msgs; pass = ≥ 30 % lower ΔE00 at every depth.

## 4. Decisions for Nick

1. Approve this plan (Phase 1 in the Friday RC; Phase 3 in the pool week)?
2. Card truth method: spectrophotometer (which one?) or side-by-side with a known chart?
3. Turn on processing (`features.image_filter = raw_card_v1`) for SPOT-31593C / SPOT-33507C once Phase 1 merges
   (a production config change)?
4. Pool card: v3 c1 only, or v3 + V1 for continuity?

## 5. Not in scope this week

Depth-dependent models (no depth on the bmcams), lens shading / flat-field calibration, temporal fusion (Sprint21),
the N6/AE3 boards, the GPL-free OpenCV build (OQ-36).
