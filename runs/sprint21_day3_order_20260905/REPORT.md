# Sprint 21 — Day 3: colour before or after fusing (ask B)

**Run:** `runs/sprint21_day3_order_20260905/` · 2026-09-05 · `tools/bm_fuse_order_ab.py` (new)
**Data:** the three complete days from the day-0 archive, 5 frames each. **No field unit touched.**

---

## 1. The answer

**Fuse the raw frames, then run Nereus v3 once on the composite.** That is arm A2, and it
is the simplest of the four options: one card read instead of five, no per-frame
photometric step, no reimplementation of the colour pipeline.

| Arm | What it does | B/G repeatability by day (%) | Mean | Lightness | Card ΔE2000 |
|---|---|---|---|---|---|
| A1 | v3 per frame, then fuse | 5.98 · 2.81 · 7.12 | 5.30 | **13.70** | 30.3 |
| **A2** | **fuse raw, then v3 once** | **4.68 · 5.72 · 2.30** | **4.23** | 16.85 | 30.3 |
| A3 | card-anchored per frame, fuse, v3 | 3.69 · 9.45 · 15.51 | 9.55 | 17.99 | 31.1 |
| A4 | reef-anchored per frame, fuse, v3 | 7.00 · 6.04 · 5.90 | 6.31 | 30.29 | 30.5 |

Lower is better: it is how much a colony's blue-over-green ratio changes when the
composite is built from one half of the day's frames rather than the other.

## 2. Read this honestly: the arms are not statistically separable

A different arm wins on each of the three days. The spread between the arms' means (2.30)
is smaller than the spread within a single arm across days (2.62). **Three days and three
colonies cannot rank these four methods.**

What the data does support:

- **A2 has the best mean and the second-tightest spread**, and it is the cheapest and
  simplest. Choosing it costs nothing even if the ranking is noise.
- **A3, card-anchored per-frame normalisation, is the one to avoid.** Worst mean by a wide
  margin and by far the least stable (sd 5.91 against 1.75 for A2). This is the same
  effect measured on day 0 in isolation: the card sits on bright near-field sand and does
  not track the shaded coral wall, so anchoring on it injects the card's own lighting
  variation into the colonies.
- **A4, my own day-1 proposal, is not vindicated.** It is mid-pack on colony colour and
  clearly the worst on lightness stability (30.3% against 13.7% for A1). The affine fit is
  estimated from noisy data, and within a single day its estimation noise exceeds the
  drift it was correcting.
- **Card ΔE2000 ties across all four arms** (30.3–31.1), exactly as predicted. Every arm
  ends anchored on the card by construction, so that metric cannot discriminate and was
  never the gate.

## 3. Why the simplest option wins, and why that follows from day 2

Per-frame normalisation existed to make frames from different conditions comparable. The
day-2 decision removed that need: composites are built within a single day, where the
frames are already photometrically similar. Correcting a problem that is no longer there
only adds the estimator's own noise.

So the minted grouping decision simplifies the pipeline rather than complicating it:

    register  ->  fuse raw  ->  Nereus v3 once

The reef-anchored normalisation stays in `tools/bm_temporal_fuse.py` as rung L3, because
it is the right tool if cross-condition fusion is ever revisited. It is simply not used in
the production path.

## 4. A bug worth recording, the second of its kind

The first run of this experiment anchored each half-composite on its own best frame rather
than on a shared reference. That made the gate measure the difference between two anchors
instead of reproducibility, and it penalised precisely the arms that normalise. It changed
the numbers substantially (A2 4.07 → 2.30, A4 8.71 → 5.90) without changing the winner.

This is the same mistake found and fixed in day 1's split-half metric. It is worth naming
the general form: **any comparison of two composites must fix every anchor they share, or
it measures the anchors.**

## 5. What to look at

`cut_sheets/arms.jpg` — the four arms side by side on the same coral crop, each labelled
with its repeatability.

## 6. Next

Day 4 is the bleaching sensitivity number: colony outlines, the blue-over-green index, the
variance components, and the minimum change detectable against colony size and baseline
length. The 4.23% colony repeatability measured here is the noise floor that any change
signal has to clear.
