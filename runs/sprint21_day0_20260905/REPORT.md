# Sprint 21 — Day 0 intake report

**Run:** `runs/sprint21_day0_20260905/` · 2026-09-05 · branch base `development` @ 8b7cf97
**Unit:** bmcam001 / SPOT-33361C, AOML Cheeca reef · runtime sha `0d03a62cf565`
**Scope:** archive the received frames with provenance, join delivery records, verify
registration, produce a first quick-look composite. No field unit touched.

---

## 1. Headline: no image from this camera has arrived complete in five days

24 capture cycles, 2026-09-01 → 09-05. **Complete images: 0 of 24.**

This is not a stacking result. It is a live delivery fault on a customer unit, and it
was invisible until the per-image records were pulled, because every truncated frame
still renders as a full picture. That is the progressive-JPEG design working as
intended, and it is also why the problem hid.

### What the evidence says

| Observation | Value | Source |
|---|---|---|
| Messages the Pi plans and sends | 153–190 per image | `<START IMG> length=`, `<END IMG> sent_buffers=` |
| Messages Sofar actually receives | 139–153 (median 145) | chunk reconstruction |
| Usable prefix of the image | 41–93 % (median 84 %) | `delivery/*.json` |
| Last chunk lands, on the 5-min UTC grid | 177–180 s into the lane, every time | `delivery/burst_timing.csv` |
| Grid boundaries crossed by a burst | 0 of 24 | same |

The Pi is not the problem. On the four cycles where the `<END IMG>` message got
through, it reports sending **every** buffer: `sent_buffers: 190, uart_duration_sec:
194.9`. The Pi finished its job. Roughly 45 of those messages never reached Sofar.

The cut is sharply **time-aligned, not count-aligned**: bursts start 27–36 s into a
5-minute lane, run at 0.99 msg/s, and stop at 177–180 s into that lane — a 3-second
spread across 24 cycles, versus a 14-message spread in the counts. Something downstream
of the Pi stops accepting or forwarding at roughly 2 min 58 s past the 5-minute
boundary. Note the co-located Bristlemouth temperature node transmits at :02:57 every
hour. **That association is unexplained and I am not claiming causation** — it is the
first thing to check.

### It is actively getting worse when the ladder aims higher

The quality ladder steps 90 → 80 → 70 and lands on q70 most cycles (`att=3`), q80 twice
(`att=2`). The two q80 cycles produced the **worst** delivered frames of the whole set:

| Cycle | Quality | Planned | Received | Prefix | Sharpness |
|---|---|---|---|---|---|
| 09-04 14:00Z | q80 | 190 | 152 | 74.2 % | 40.4 |
| 09-04 15:00Z | q80 | 184 | 147 | 76.6 % | 36.5 |
| 09-04 17:00Z | q70 | 156 | 142 | 91.0 % | 72.8 |

A bigger file does not buy quality here. The delivery ceiling is roughly fixed, so a
larger image simply loses a larger fraction of itself and arrives **softer**. The
camera is spending 195 seconds of transmit time and the battery that goes with it to
deliver a worse picture.

### The change that follows

`message_cap` on the reef units is **195**, set when the cycle budget allowed it. The
link delivers about **145**. Lowering the cap to ~135 would make the ladder settle
around q55–q60, which fits in the messages that actually get through, and images would
arrive **complete**. This is the Sprint 11 D8 argument exactly: a complete lower-quality
image beats a truncated higher-quality one, and here it also costs less airtime and
less power.

That is a YAML change for the next field update, not code, and it needs Nick's call
plus the catch-it-awake procedure. **Nothing in this sprint touches the unit.**

---

## 2. Frame inventory

- **26 files across three Downloads locations → 17 distinct captures.** Every duplicate
  is byte-identical to its canonical copy, including the `(2)` copy of 09-01T18:00:24Z
  that was flagged as suspicious during planning. It is not a re-save. Its odd
  appearance is explained by its delivery: an internal gap at chunk 70, only 41 % usable.
- **Mount is stable.** Maximum drift across all 17 captures is **0.88 px**, single epoch.
  Registration is a verification step, as the plan assumed.
- Sharpness tracks delivery, not optics. The two softest frames are the two q80 cycles;
  the next two softest are the two with early internal gaps (41 % and 55 % prefix).

Archive and tables: `frames/`, `frames_manifest.json`, `frames_manifest.csv`,
`cut_sheets/contact_sheet.jpg`.

---

## 3. Quick-look composites (the control, as designed)

Median stacks, which the plan uses as the dumb baseline every later method must beat:

| Group | n | Sharpness |
|---|---|---|
| Single frames | — | 36 – 91 |
| Median, one day | 5 | 43 – 62 |
| Median, all frames | 17 | 39.8 |

Confirmed on the real set: **more frames in a plain median means a softer picture.**
Stacking more is not better by itself. This is the premise of the day-1 fusion ladder,
and it now rests on the delivered data rather than a pilot.

---

## 4. What this changes for days 1–5

- **Frame weighting is no longer optional.** Usable prefix ranges 41–93 % across the set.
  Weighting by delivery state was planned as one rung of the ladder; it is now the
  single most important term, and the numbers to drive it are in the manifest.
- **A "complete frame" reference does not exist in this dataset.** Day 2 planned to make
  ground truth on the Mac with the Sprint 06 partial-transmission simulator. That is now
  the only route to a reference, and it matters more than it did.
- **Ask D's baseline is affected.** Every frame is missing final luma refinement below a
  row that moves cycle to cycle. Colour survives, which is what the blue-over-green
  index needs, so the change-detection path stands. Sensitivity estimates must be
  conditioned on prefix, not pooled.
- Days 1–5 otherwise proceed as planned.

---

## 5. Code changed today

- `tools/count_complete_images.py` — **bug fix.** `decode()` assumed every sensor row is
  a hex string. SPOT-33361C also carries a BM temperature node reporting a numeric
  value, and `bytes.fromhex()` raised `TypeError` on it, aborting the entire report for
  this unit. Non-string values are now skipped like any other non-image traffic.
  Regression test added (`tests/test_count_complete_images.py`, fails without the fix);
  12/12 pass.
- `tools/bm_reef_frame_intake.py` — **new.** The archive/manifest/registration/quick-look
  tool this report was produced with. Re-runnable daily.
- Venv: `pyserial` and `certifi` installed. The test suite could not be collected without
  pyserial, and the venv had no CA bundle, so any Sofar call failed TLS verification.

Both files are uncommitted.

---

## 6. Open questions for Nick

1. **The delivery ceiling is the biggest thing here.** Do you want a focused follow-up on
   the ~178-second wall, including whether the temperature node's :02:57 slot interacts
   with the camera burst? That is a Spotter/link question and may need Sofar.
2. Lower `message_cap` 195 → ~135 at the next field update? It should turn 0-complete
   into most-complete at a small quality cost, and save transmit power.
3. Was this unit ever delivering complete images? If it used to, something changed, and
   the cause matters more than the workaround.
