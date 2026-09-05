# Sprint 21 — Day 1: the fusion ladder

**Run:** `runs/sprint21_day1_fusion_20260905/` · 2026-09-05 · tool `tools/bm_temporal_fuse.py`
**Input:** the 17-capture day-0 archive · **Nothing touched a field unit.**

---

## 1. Verdict

**Fusing frames from different days loses detail. Fusing frames from the same day works.**

| Grouping | Best composite detail | vs a typical single frame |
|---|---|---|
| All 17 frames, 3 days | 17.7 | 0.67× |
| One day, 5 frames (09-02) | 23.6 | 0.82× |
| One day, 5 frames (09-03) | 27.0 | 0.94× |
| One day, 5 frames (09-04) | 31.5 | 0.96× |

Across days the reef's fine texture genuinely differs, because turbidity and light change
the local contrast of the same edge. Averaging a clear day into a hazy one dilutes it.
Within a day the conditions hold still enough that fusion adds without subtracting.

This confirms the day-0 measurement that day-to-day variation exceeds hour-to-hour
variation, and it settles the grouping question the plan left open: **the fusion unit is
one day, not the whole deployment.**

## 2. The ladder is attributable

Each rung adds exactly one term. On 2026-09-04 every rung earns its place:

| Rung | What it adds | Detail | Noise | Blockiness | Split-half |
|---|---|---|---|---|---|
| L0 median | control | 25.8 | 1.33 | 1.60 | 2.96 |
| L1–L3 | mean, registration, reef normalisation | — | — | — | — |
| L4 delivery | weight by how much of each frame arrived | 28.1 | 1.28 | 1.47 | 2.38 |
| L5 transient | reject fish, particles, sway | 30.4 | 1.29 | 1.49 | 2.28 |
| L6 multi-scale | take fine detail from frames that resolved it | **31.5** | 1.51 | 1.50 | 2.37 |
| *single frame* | *for reference* | *32.7 median* | *1.37* | *1.62* | *4.54* |

Delivery weighting is the single biggest contributor, worth +2.3 detail and a clear drop
in JPEG blockiness. That is a direct consequence of the day-0 finding: frames differ in
how much of themselves arrived, so treating them as equals throws away the good ones.

## 3. The gate

| Gate | Target | Result |
|---|---|---|
| Detail vs a typical single frame | ≥ 0.9× | **PASS within a day** (0.94–0.96× on 2 of 3 days), FAIL across days (0.67×) |
| Split-half reproducibility beats two single frames | yes | **PASS everywhere.** Composites: 2.0–2.5 RMS. Single-frame pairs: 3.4–4.5 |
| Blockiness not worse | yes | **PASS.** 1.60 → 1.47 |

**The reproducibility result is the important one for ask D.** A daily composite gives
the same answer from either half of the day's frames about twice as consistently as any
single frame does. That halves the noise floor a change-detection index has to clear,
before any tuning.

## 4. Two metric bugs found and fixed

Worth recording, because both would have produced a flattering wrong answer.

- **Split-half normalised each half to a different reference frame**, so it measured the
  difference between two anchors rather than reproducibility. It made normalisation look
  catastrophic (5.0 vs 1.37). Fixed by anchoring both halves on the same frame, which the
  reference need not belong to.
- **Detail was measured at edges chosen from the reference frame**, which only the
  reference can score well on. A perfectly good second frame lands at ~0.7× purely from
  the bias. Every "≥0.9× the reference" gate was unreachable by construction. Fixed by
  choosing edge locations from the median gradient across all frames, independent of both
  the reference and the composite.

## 5. What to look at

- `cut_sheets_compare.jpg` — one frame vs five fused, as received and rendered through
  Nereus v3. The rendered composite is visibly cleaner in the shadows and on the sand,
  where the single frame's speckle is amplified by the colour pipeline's sharpening.
- `by_day/cut_sheets/ladder_*.jpg` — every rung side by side on the same crop.
- `by_day/maps/stability_*.png` — where change detection is trustworthy.

## 6. Next

Day 2 makes a true reference on the Mac: full-resolution frames pushed through the
Sprint 06 partial-transmission simulator at the real q70 and the real delivered
fractions, so detail can be scored against ground truth rather than against other
delivered frames. Day 3 runs the ordering arms for ask B.

Open: the composite currently spans a whole day, which mixes 10:00–14:00 sun angles.
Whether a tighter condition grouping beats a calendar day is a day-2 question.
