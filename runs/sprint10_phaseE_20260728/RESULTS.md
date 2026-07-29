# Sprint10 Phase E — Cellular Queue Drain Characterization: RESULTS

**Bench:** 2026-07-29 00:28–05:50Z. bmcam003 + SPOT-33507C, bmcam000 +
SPOT-31593C, both `development@9330779`, Spotter FW v2.16.6, both Pis
disarmed and both Spotters held on continuous bus power for the run.
**Operator:** Claude session, Nick-authorized.
**Raw artifacts:** `sendlogs/`, `analysis/`, `~/spotter_logs/`, `PLAN.md`,
`DOE.md`, `PARITY.md`.

---

## 1. Headline

The blackout that eats image chunks is a **periodic, wall-clock-aligned
event on a 5-minute grid**, lasting a **median 9.0 seconds**, during
which the Spotter's 2-slot cellular queue rejects every submit. Loss is
fully described by

```
lost per boundary = max(0, D / delay − 2)        D = blackout seconds
                                                 2 = cellular queue slots
```

with **D ≈ 9 s**. Zero loss therefore requires **delay ≥ D/2 = 4.5 s** —
which is exactly why the historical 300 char / 5.0 s configuration
delivered 100 % and the current 384 char / 1.0 s does not.

**Chunk size is not the cause.** Implied D is 9.8 s at 300 chars vs
9.0 s at 384 chars — indistinguishable.

**`message_cap` is not the lever.** At a fixed delay the loss *rate* is
independent of cap, because a shorter burst crosses proportionally fewer
boundaries.

---

## 2. What was run

| Set | Purpose | Bursts | Result |
|---|---|---|---|
| SMOKE3 / SMOKE0 | end-to-end wire check | 2 × 3 msgs | payload verified as `TST,…` at each Spotter's queue |
| DISC (partial) | discriminator, aborted on re-plan | 1 × 200 | salvaged, valid |
| PAR3 / PAR0 | parity, identical matrix both units, simultaneous | 6 × 200 | see §5 |
| **E3 / E0 DOE** | **size × delay, n=3, both units** | **24 × 200** | **see §4** |

DOE factors: size {300, 384} chars × delay {1.0, 1.5} s, 200 messages,
300 s drains, counterbalanced order, n = 3 per unit (n = 6 pooled).
Design and rationale: `DOE.md`.

---

## 3. Mechanism — established, not inferred

- **35 of 35 gaps are console-confirmed queue overflows**
  (`analysis/gaps_all.csv`, verdict column). The console emits exactly
  **2 error lines per lost message** (`Queue MS_Q_CELLULAR_ONLY is full`
  + `[BM_TX] Unable to submit`), so loss is countable from the console
  alone — independent of the backend.
- **83 % of gaps start within 30 s of a 5-minute wall-clock boundary**
  (median offset **+1 s**). This is a scheduled Spotter behaviour, not a
  random sync. The bridge system partition carries
  `alignmentInterval5Min: 1` (key 6 of 18), the most likely driver.
- Drops are **invisible to the Pi** — every burst logged `sent=N/N`.

### Blackout duration is delay-invariant (the key cross-validation)

| Delay | Modal loss per boundary | Implied D = (loss + 2) × delay |
|---|---|---|
| 1.0 s | 7 | **9.0 s** |
| 1.5 s | 4 | **9.0 s** |

Two independent pacing rates imply the *same* blackout duration. That is
what makes the model predictive rather than a curve fit.

Across all 20 boundary-crossing bursts: **median D = 9.0 s**, mean 13.1 s
(mean is pulled by a tail), 75th pct 16.5 s, 90th pct 24.0 s.

### Bursts that cross no boundary lose nothing

Two DOE bursts fitted entirely between boundaries: `E3S300R1C200D1000`
and `E0S300R1C200D1000` — **both 200/200, zero loss**. The cleanest
possible confirmation of the mechanism.

---

## 4. DOE result (24 bursts, n = 6 per cell)

| Size | Delay | Replicates (lost of 200) | Mean | SD | Loss % |
|---|---|---|---|---|---|
| 300 | 1.0 s | 0, 0, 7, 7, 17, 17 | 8.00 | 7.64 | 4.00 % |
| 300 | 1.5 s | 4, 4, 4, 5, 9, 14 | 6.67 | 4.08 | 3.33 % |
| 384 | 1.0 s | 1, 7, 7, 8, 17, 43 | 13.83 | 15.18 | 6.92 % |
| 384 | 1.5 s | 3, 4, 4, 4, 11, 16 | 7.00 | 5.29 | 3.50 % |

**Main effects:** size 300 → 3.67 %, 384 → 5.21 %; delay 1.0 s → 5.46 %,
1.5 s → 3.42 %.

**These raw main effects are not significant.** Within-cell scatter (SD
up to 15.2 on a mean of 13.8) exceeds the between-cell differences. Read
directly, this DOE says only "both factors point the expected way."

The result becomes decisive only after normalising by boundaries crossed
(§3): the residual quantity D is *stable at 9 s across both delays and
both sizes*, and the apparent size effect disappears. The single 43-loss
burst is explained — it began **17 s inside** an unusually long
04:45:00 blackout, not by its chunk size.

### Per-image correction

At fixed 200 messages, 300 chars looks marginally better. A real image
needs ~28 % more 300-char chunks, so at equal image bytes the advantage
is ~10 % — inside the noise. **No reason to change chunk size.**

---

## 5. Unit parity (PAR3 / PAR0, identical matrix, simultaneous)

| Delay | bmcam003 | bmcam000 |
|---|---|---|
| 1.0 s | 1.0 % | 9.0 % |
| 3.0 s | **1.0 %** | **1.0 %** |
| 4.0 s | 0.0 % | 4.0 % |

Not at parity. Where they agreed exactly (3.0 s), they lost the same
2 messages at the *same two absolute instants* (01:15:0x, 01:20:0x) —
the periodic component is identical across units; the disagreement is
entirely the variable-duration tail. bmcam000 ran 11 blackout episodes
overnight vs bmcam003's 5. **Treat single-unit results as a lower bound.**

---

## 6. Loss-vs-delay curve and recommendation

Predicted from `lost = max(0, D/delay − 2)` per boundary, 190-message
image, D = 9.0 s median:

| Delay | Burst | Boundaries | Expected loss | Awake |
|---|---|---|---|---|
| 1.0 s | 190 s | 0.63 | 2.33 % | 3.2 min |
| 1.5 s | 285 s | 0.95 | 2.00 % | 4.8 min |
| 2.0 s | 380 s | 1.27 | 1.67 % | 6.3 min |
| 3.0 s | 570 s | 1.90 | 1.00 % | 9.5 min |
| 4.0 s | 760 s | 2.53 | 0.33 % | 12.7 min |
| **4.5 s** | 855 s | 2.85 | **0 %** | 14.2 min |
| **5.0 s** | 950 s | 3.17 | **0 %** | 15.8 min |

Independent confirmation of the 4.5 s threshold: bmcam003 delivered
**200/200 at 4.0 s** in the parity set, and Nick's production history is
100 % at 5.0 s.

### Recommended ship values

```yaml
image_transmit_delay_seconds: 5.0     # 4.5 s is the threshold; 5.0 = margin
image_buffer_size: 384                # UNCHANGED — size is not the cause
message_cap: 195                      # UNCHANGED — cap is not the lever
```

**Cost:** 15.8 min awake per image vs 3.2 min at 1.0 s. That fits the
16-min budget in a 20-min window with almost no headroom, and it gives
back Sprint09's awake-time win. This is a real trade, and it is Nick's
call whether 100 % delivery is worth it.

**Honest bound:** D varies (median 9 s, 90th pct 24 s). 5.0 s removes
*typical* blackouts, not the long tail — "100 % most cycles", not
guaranteed. bmcam000's higher episode rate shows site/signal matters.

### The structural fix (post-freeze, NOT built)

Boundaries are on **absolute wall-clock time** and bmcam003 is
NTP-synced. Starting the transmit just after a boundary and keeping the
burst under 300 s would cross **zero** boundaries — ~0 % loss at 1.0 s
pacing and 3.2 min awake, i.e. both wins at once. This is the same idea
as D16's split-burst, sharpened by measurement: the pause does not need
to be 30 s, it needs to be *phase-aligned*. Transmit-path change, frozen
for Wednesday. Recommended as the first post-release sprint item.

---

## 7. Sofar / Blues question sheet (DEV_LOG Q12)

1. **What fires on the 5-minute wall-clock boundary?** We measure a
   ~9 s window (median; up to ~24 s) in which every submit to
   `MS_Q_CELLULAR_ONLY` is rejected, starting within ~1 s of each
   5-minute mark. Is this the `alignmentInterval5Min` behaviour, a
   Notecard sync, or a message-service flush?
2. **Can it be pinned, deferred, or disabled** so it never lands inside
   a transmit — e.g. sync-on-demand, a settable interval, or a
   "do not sync while sending" hold? *(Blocks the structural fix.)*
3. **Is the 2-slot cellular queue depth configurable?** Loss is
   `max(0, D/delay − slots)`; going from 2 to 8 slots would remove the
   problem at production pacing without any awake-time cost.
4. **Is there a backpressure signal the Pi can read?** Drops are silent
   to the BM sender — the Pi logs `sent=N/N complete=True` for messages
   the Spotter discarded. Even a queue-depth read would let us pause.
5. **Why does blackout duration vary 9 → 24 s**, and does it scale with
   signal strength? Two units in the same room differed 2× in episode
   rate.
6. **Sustained forwarding rate:** Sprint09 measured ~1.27 msg/s at
   384 B. Is that the intended ceiling, and does it vary by signal?

---

## 8. Artifacts

| File | Contents |
|---|---|
| `analysis/bursts_all.csv` | 24 DOE bursts: size, delay, rep, boundaries, delivered/lost, loss % |
| `analysis/gaps_all.csv` | 35 gaps: sequence, length, absolute UTC, offset from 5-min boundary, console verdict |
| `analysis/doe/<tag>/` | per-run analyzer + correlator output |
| `sendlogs/bmcam00{0,3}/` | every send log, manifest, runner log |
| `clock_offsets.txt` | Pi-vs-bench clock offsets (bmcam000 runs −2773 s; NTP disabled by design) |
| `PLAN.md`, `DOE.md`, `PARITY.md` | design, deviations, parity analysis |
| `doe_runner.sh`, `catch_awake_disarm.sh`, `restore_field_normal.sh`, `spot_cmd.sh` | bench tooling |

## 9. Corrections to prior documents

- **PHASE_E.md §3.4 / §6 command form was wrong and failed silently.**
  `bm cfg set 0 s u …` prints `Queuing serial command` and does nothing —
  `bm cfg` forwards onto the BM bus and node `0` is not the bridge. The
  working form is `bridge cfg set <bridge_node_id> s u <key> <val>` +
  `bridge cfg commit <bridge_node_id> s`. §6's restore chain had the same
  defect, so a careful operator would have "restored" both units and left
  them permanently powered. Both sections corrected.
- **DESIGN D16's model is confirmed in form** (`max(0, blackout/delay −
  slots)`) but its blackout was framed as a per-transmit sync session.
  It is actually a **periodic 5-minute-grid event**, so the correct
  denominator is per boundary crossed, not per image.
- Sprint09's ~400 B fast-path cliff was measured on **10-message
  bursts**; this run shows chunk size has no effect at 200-message
  scale, so no change to `image_buffer_size` is warranted either way.
