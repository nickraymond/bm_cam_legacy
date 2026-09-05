# Sprint 21 — Cross-cycle temporal fusion · SPEC

**Status:** COMPLETE 2026-09-05. All five days ran. Branch base `development` @ 8b7cf97.
**Scope held:** shore-side only. No field unit was touched, no bandwidth changed, no Pi code
altered. Every run is a self-contained folder under `runs/sprint21_*`.

---

## 1. The production recipe

```
per day:   register  ->  fuse raw  ->  [ measure here ]  ->  Nereus v3 once  ->  [ show here ]
```

One day of frames in, two outputs out, diverging only at the last step.

| Step | Tool | Setting |
|---|---|---|
| Archive + delivery join | `tools/bm_reef_frame_intake.py` | daily |
| Register | `bm_temporal_fuse.py` | translation only, no warp below 0.5 px, new epoch above ~2 px |
| Fuse | `bm_temporal_fuse.py` | rung `L6_multiscale`, `--detail-exp 0.25`, **no photometric normalisation** |
| Grouping | — | **one calendar day. Never fuse pixels across days.** |
| Measure change | `bm_reef_change_sensitivity.py` | on the **raw fused composite**, `--measure-layer raw` |
| Render | `bm_grvi_correct.py` | once, on the composite, with the V3 preset |

## 2. Gates, and whether they were met

Written before the runs, scored after.

| Gate | Target | Result |
|---|---|---|
| Composite beats a single frame against ground truth | SSIM ≥ best single | **PASS** 0.8824 vs 0.8735 |
| Split-half reproducibility beats two single frames | yes | **PASS** 2.0–2.5 vs 3.4–4.5 RMS |
| Blockiness not worse than a single frame | yes | **PASS** 1.60 → 1.47 |
| Every ladder rung attributable | yes | **PASS** each rung's gain isolated |
| Detail vs a typical single frame, on delivered data | ≥ 0.9× | **PASS within a day** (0.94–0.96×), FAIL across days (0.67×) |
| Ordering arms separable | rank them | **NOT MET** — 3 days cannot rank 4 methods; see §3 |
| Change sensitivity quantified | a number with caveats | **PASS** ~10 % on ≥3,000 px, 2–4 week baseline |

## 3. Decisions minted

1. **Fuse within one calendar day only.** Equal-size test: same-day 0.995 against a single
   frame, same-hour-across-days 0.897, all seventeen frames 0.67. Directional rather than
   airtight (the combinations share frames), but the all-frames collapse is unambiguous.
   *Carve-out:* change detection still COMPARES across days. Its baseline is a per-colony
   statistic computed from daily composites, not a fused image. "Never fuse pixels across
   days" and "never compare across days" are different rules; only the first is adopted.
2. **Fuse raw, then colour-correct once.** Per-frame photometric normalisation is dropped
   from the production path. Decision 1 removed the problem it existed to solve, and within
   a day its estimation noise exceeds the drift it corrected. Rung L3 stays in the tool for
   any future cross-condition work.
3. **Measure change on the raw fused composite, never on the rendered one.** Rendering
   halves sensitivity (10.6 % → 25.6 %). Any per-composite colour correction is worse still
   (50–115 %), because the differential index already cancels the water cast for free while
   a colour fit does not.
4. **Do not ship change flags yet.** Three days cannot set a threshold. Ship the composite;
   bank the baseline.

## 4. Out of scope, recorded elsewhere

- **TODO-SPOT-001** — bmcam001 has delivered zero complete images since at least
  2026-09-01. Spotter timing bug, Nick owns it. This is the largest available lever on
  every number in this sprint.
- **Icebox** — per-cycle high-resolution tile rotation. Tail loss is deterministic, the Pi
  has no downlink to learn what arrived, and cross-day tiles seam. Needs its own spec.
- **Sprint 20** — capture-side burst stacking and the red HDR bracket. Complementary and
  non-overlapping: Sprint 20 improves each frame, this sprint makes the sequence mean
  something.

## 5. Tools added

| Tool | Purpose |
|---|---|
| `tools/bm_reef_frame_intake.py` | archive frames with provenance, join delivery records, verify registration, quick-look |
| `tools/bm_temporal_fuse.py` | the attributable fusion ladder, stability and coverage maps |
| `tools/bm_temporal_fuse_validate.py` | ground truth by simulating the real capture and delivery chain |
| `tools/bm_fuse_order_ab.py` | the four ordering arms for ask B |
| `tools/bm_reef_change_sensitivity.py` | null, transfer and minimum detectable change |

Fixed: `tools/count_complete_images.py` crashed on this Spotter's temperature node.
Regression test added; 12/12 pass.

## 6. Method note worth keeping

Four metric bugs were found and fixed during the sprint, all of the same family: **a
comparison that does not fix every anchor it shares measures the anchors, not the thing.**
Two were split-half comparisons anchored on different reference frames; one was a detail
metric that chose its edges from the reference frame, making its own gate unreachable; one
was a cache keyed on `id()` of a freed array. Each would have produced a confident wrong
answer. Any future gate here should be checked against that family first.
