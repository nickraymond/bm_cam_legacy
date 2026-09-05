# Sprint 21 — Day 2: scored against a known answer

**Run:** `runs/sprint21_day2_truth_20260905/` · 2026-09-05
**Tools:** `tools/bm_temporal_fuse_validate.py` (new), `tools/bm_temporal_fuse.py`
**Nothing touched a field unit.**

---

## 1. Why this day existed

Day 1 compared delivered frames against each other. That can only say which damaged frame
a composite resembles. It cannot say whether the composite recovered the reef, and because
bmcam001 has never delivered a complete image, no true reference exists in the received
data at all.

So this run manufactures one. A full-resolution reef frame with the card in it is pushed
through a model of the real chain: RC crop and Lanczos to 1000x562, per-cycle gain and
veil drift at the measured spread, drifting particles and a transiting fish, sensor noise,
progressive encode at the observed q70, truncation at the **real** delivered fractions from
2026-09-04 (74.2 / 76.6 / 88.2 / 91.0 / 91.1 %), then the backend's baseline q85 re-encode.
The original never goes through any of that, so it is the answer.

## 2. Day 1's read was too pessimistic. Fusion does beat a single frame.

Scored against ground truth, after photometric alignment so exposure offsets do not
masquerade as structure error:

| | PSNR | SSIM | Edge energy |
|---|---|---|---|
| Median single received frame | 28.1 | 0.8576 | 247.2 |
| **Best** single received frame | 28.8 | 0.8735 | 246.4 |
| Fused, L4 delivery weighting | **29.5** | **0.8824** | 245.8 |
| Fused, L5 transient rejection | 29.3 | 0.8821 | 249.2 |
| Fused, L6 multi-scale | 29.3 | 0.8819 | **249.6** |
| *Ground truth* | — | — | *255.5* |

The fused composite beats the **best** single frame on every measure, and L6 recovers
detail closest to the truth (249.6 against 255.5, versus 246.4 for the best single frame).

Day 1 reported fusion at 0.94–0.96× a single frame and called that a near-miss. That
metric was measuring the wrong thing: a delivered frame's edge energy includes its own
noise, particles and compression artefacts, which inflate it. Against truth, that
apparent deficit reverses into a gain. **Day 1's ranking of the rungs was right; its
verdict on whether fusion helps was wrong.**

Caveat, stated plainly: the model does not move shadows as the sun moves, so it flatters
fusion across time. Read it as validating the algorithm, not as licence to fuse anything.
The real-data result in §3 is what governs grouping.

## 3. The grouping decision, settled

Nick proposed: composites are built from a single day, never across days. The by-day and
by-hour comparison could not settle it, because hour groups had 3 frames and day groups
had 5, and a smaller group always preserves more detail. Holding the group size at 3:

| Grouping, 3 frames each | Combinations | Detail vs a single frame |
|---|---|---|
| Same day, 3 consecutive hours | 30 | **0.995** mean, 0.956 median |
| Same hour, 3 different days | 11 | 0.897 mean, 0.909 median |
| *All 17 frames, 3 days* | *1* | *0.67* |

Same-day wins by about 10 points, roughly 2 standard errors. Treat that as directional
rather than conclusive: the combinations share frames, so the true uncertainty is wider
than the arithmetic suggests. Combined with the unambiguous all-17 collapse to 0.67, the
decision is sound.

**MINTED: pixels are fused within one day only.**

**Carve-out, deliberately kept:** change detection compares across days by definition. Its
baseline is a per-colony *statistic* computed from daily composites, not a fused image.
"Never fuse pixels across days" and "never compare across days" are different rules, and
only the first is adopted.

## 4. What to look at

`cut_sheet_truth.jpg` — the answer, the best single received frame, and the fused
composite, side by side on the same crop, all photometrically aligned.

## 5. Next

Day 3 runs the ordering arms for ask B, now with a ground-truth harness that can score
them properly rather than by appearance. Day 4 is the bleaching sensitivity number.
