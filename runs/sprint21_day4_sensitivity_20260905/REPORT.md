# Sprint 21 — Day 4: how big a change would we actually see (ask D)

**Run:** `runs/sprint21_day4_sensitivity_20260905/` · 2026-09-05
**Tool:** `tools/bm_reef_change_sensitivity.py` (new) · **No field unit touched.**
**Pre-registered before any result was looked at:** k = 3 sigma, a change must persist
across at least 2 consecutive daily composites.

---

## 1. The number

**A sustained blue-over-green shift of roughly 10–12 % on a colony of about 3,000–5,000
pixels, against a two-to-four week baseline.** Smaller colonies need more: about 18–22 %
at 600–1,600 pixels.

| Analysis window | Colony area | 3-day baseline | 14-day | 28-day |
|---|---|---|---|---|
| 24 px | 576 px | 20.5 % | 18.2 % | 17.9 % |
| 40 px | 1,600 px | 22.4 % | 19.9 % | 19.5 % |
| **56 px** | **3,136 px** | **11.9 %** | **10.6 %** | **10.4 %** |
| 72 px | 5,184 px | 12.8 % | 11.4 % | 11.2 % |
| 96 px | 9,216 px | 15.5 % | 13.8 % | 13.6 % |

Two things to read from the shape of that table. Bigger colonies help, but only up to a
point: past about 5,000 pixels a window starts pulling in neighbouring substrate and the
noise rises again. And **a longer baseline buys surprisingly little** — going from 3 days
to 28 improves sensitivity by about a tenth. The floor is set by day-to-day scene noise,
not by how long we watch. More days buy *confidence* in the threshold, not a lower one.

## 2. How it was measured

Three parts, each with its own number.

**The null.** The index is differential: a colony's log(B/G) minus the log(B/G) of the
scene's stable substrate in the same composite, so a hazier day or a different sun angle
moves both and cancels. Across the three daily composites with no known change, the
spread is **2.1–3.9 %** depending on window size. That is the noise floor.

**The transfer.** A known change was injected into a colony *before* compression and pushed
through the entire simulated chain: per-cycle lighting drift, particles, q70 progressive
encode, truncation at the real delivered fractions, the backend re-encode, fusion, and
colour correction. Each level was run over three seeds with paired degradation.

| Injected | Recovered | Transfer |
|---|---|---|
| 5 % | 0.96 % (sd 1.66) | indistinguishable from zero |
| 10 % | 6.66 % (sd 1.84) | 0.67 |
| 20 % | 12.55 % (sd 2.02) | 0.63 |
| 40 % | 26.99 % (sd 3.98) | 0.67 |

**The pipeline destroys about a third of any real change**, consistently, above 10 %. Below
that it destroys effectively all of it. The minimum detectable change above is inflated by
that factor, which is why it is a property of the whole chain and not just of the noise.

## 3. Measure in the linear layer, never on the rendered image

Running the identical analysis on the Nereus v3 rendered composites instead of the fused
linear ones:

| Measured on | Null noise (56 px) | Transfer | Detectable change |
|---|---|---|---|
| Rendered v3 composite | 2.5 % | 0.34 | 29.0 % |
| **Fused linear composite** | **2.1 %** | **0.65** | **11.9 %** |

Measuring on the picture people look at makes the camera **roughly half as sensitive**.
The v3 render exists to look right: its tone curve and its saturation stage at 0.68
deliberately compress chroma, and they compress a real bleaching signal along with
everything else. This vindicates the measurement-layer / visualisation-layer split that
was already the pipeline's stated philosophy, and turns it from a principle into a
requirement.

## 4. What to look at

`cut_sheets/injection_levels.jpg` — the same colony with a modelled bleaching change of 0,
10, 20 and 40 % pushed through the full chain. The 10 % panel is near the detection floor
and is genuinely hard to see, which is the honest visual answer to "how sensitive is this".

## 5. What this is not

- **Three days of baseline.** A standard deviation from n=3 carries roughly 50 % error, and
  the window-size trend is not monotonic, which is what that error looks like. Treat every
  number as a pilot estimate.
- **The null is unverified.** Early September is peak thermal stress in the Florida Keys.
  "Three quiet days" is an assumption. Ask AOML whether anything was happening at Cheeca
  on 2026-09-02 to 09-04, because if something was, this floor is overstated.
- **The injection is a model**, not a measured bleached-coral spectrum, and the transfer was
  measured on one colony that happens to sit in shade. A brighter colony will transfer
  differently.
- **These are patches, not colonies.** Real outlines, drawn by someone who knows these
  colonies, replace them before any of this is used.

## 6. What would move the number

In order of expected effect:

1. **Fix the delivery ceiling (TODO-SPOT-001).** Every frame today is a partial. Complete
   frames raise the transfer directly.
2. **Sprint 20 capture-side stacking.** Lower sensor noise before compression lowers the
   null.
3. **More colonies, real outlines.** Averaging several colonies of the same species tightens
   the estimate without needing a longer baseline.
4. Longer baseline. Worth doing, but the table says it is the smallest lever.

## 7. Defensible wording

The flag marks a sustained rise in blue-over-green on a marked colony against its own
multi-week baseline. Pilot sensitivity is a change of about 10 % on a colony of roughly
3,000 pixels or larger, persisting across at least two daily composites. It means worth a
diver check. It is not a bleaching diagnosis, it has not been evaluated against ground
truth, and algal turf, sediment, turbidity and lens fouling can all trigger or mask it.

---

## 8. Addendum: does the physical half of Nereus v3 help? No.

The day-4 result compared the two ends, raw and fully rendered. Nick asked for the middle:
v3's physical stages only (veil removal, per-channel gain, chroma matrix, exposure), which
run *before* the display stages that were doing the damage. Two variants were tested,
because the answer depends on whether the colour model is re-fitted for every composite or
fitted once and reused.

All at the 56 px window, k = 3, 14-day baseline:

| Measurement layer | Null noise | Transfer | Detectable change |
|---|---|---|---|
| **Raw fused composite, no colour work** | **2.1 %** | 0.65 | **10.6 %** |
| Full v3 render | 2.5 % | 0.34 | 25.6 % |
| v3 core, one model shared across composites | 8.6 % | 0.63 | 50.4 % |
| v3 core, re-fitted on every composite | 22.1 % | 0.81 | 115.2 % |

**Raw fusion wins by a factor of five over the next best option.** The result is not close.

The interesting part is *why*, because the core correction does exactly what it is supposed
to. Re-fitting per composite gives the **best transfer of any layer, 0.81**: undoing the
water veil genuinely helps an injected change survive the chain. It is simply overwhelmed
by what it costs in noise. The card fit varies day to day (veil in green spans 0.057–0.113,
gain 1.5×), and because `apply_core` is non-linear — a softplus veil subtraction followed
by a root-polynomial matrix — that variation does not cancel in the differential index. It
lands directly in the measurement.

Sharing one model across composites confirms the diagnosis: holding the correction constant
drops the noise from 22.1 % to 8.6 %. It still does not beat raw, because a fixed
non-linear correction still distorts a colony and its substrate by different amounts, which
is precisely the cancellation the raw index depends on.

**The rule this establishes:** the differential index already handles the water cast, by
construction and for free. Any per-composite colour correction, however physically
motivated, replaces a cast that cancels with a fit that does not. Measure change on the raw
linear fused composite. Colour-correct only the picture a person looks at.

### Two bugs found while running this

Both were mine, both would have produced a confident wrong answer, and both are fixed:

- **An unsafe cache key.** Fitted colour models were cached on `id()` of a numpy array.
  Python reuses the address of a freed array, so a later composite silently collided with
  an earlier one's fit. This is what produced the first run's wild scatter (transfer
  standard deviations of 14–18 %, against 1–2 % once fixed).
- **A layer that fell through its dispatch.** The `core_shared` option was not matched by
  the branch that returns the uncorrected composite, so it received the *rendered* image
  and then had the core applied on top of it. It reported a transfer of zero — the injected
  change appeared to vanish entirely, which is what prompted the check.

Neither changed a conclusion in the end, but the first one nearly did.

### Which run folder is which

| Folder | Layer | Status |
|---|---|---|
| `sprint21_day4_sensitivity_20260905` | raw fused | **the headline result** |
| `sprint21_day4_sens_core_20260905` | v3 core, re-fitted per composite | valid |
| `sprint21_day4_sens_core_shared_20260905` | v3 core, one shared model | valid |

Two further folders were produced while the two bugs above were live and have been deleted:
one was a byte-identical duplicate of the core run, the other held the dispatch-bug results
(the negative transfers). Neither contained unique valid information, and leaving folders of
known-wrong numbers next to the real ones would mislead a later reviewer.
